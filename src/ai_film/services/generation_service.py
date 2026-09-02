from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Callable, TypeVar

from ai_film.errors import CostGateError, ProviderError
from ai_film.approval import is_approved
from ai_film.jobs import run_job
from ai_film.logging_store import write_attempt_log
from ai_film.models import (
    GenerationJob,
    ImageGenerationRequest,
    JobStatus,
    LipsyncGenerationRequest,
    MusicGenerationRequest,
    SfxGenerationRequest,
    VideoGenerationRequest,
    VoiceGenerationRequest,
)
from ai_film.shot_store import load_shot, save_shot

T = TypeVar("T")

_SCOPE_BY_STAGE = {
    "image": "storyboard",
    "video": "storyboard",
    "voice": "storyboard",
    "sfx": "storyboard",
    "music": "storyboard",
}


@dataclass
class ArchiveResult:
    version: int
    history: list[dict]
    archived_path: Path | None
    restore: Callable[[], None] | None


def _history_entry(stage_data: dict, version: int, artifact: dict, superseded_reason: str) -> dict:
    return {
        "version": version,
        "provider": stage_data.get("provider"),
        "model": stage_data.get("model"),
        "artifact": artifact,
        "superseded_at": datetime.now(timezone.utc).isoformat(),
        "superseded_reason": superseded_reason,
    }


def archive_stage_artifact(
    project_dir: Path, stage_data: dict, superseded_reason: str,
) -> ArchiveResult:
    """Move stage_data's current artifact file (if any) into a sibling
    history/ directory next to it, returning the next version number, the
    updated history list, the path the file was archived to (audio_fix.py
    uses this as its ffmpeg input), and a zero-arg restore callable that
    undoes the move — used when the regeneration attempt that prompted the
    archive ends up failing, so a failed attempt never leaves the shot's
    prior working artifact missing.

    Whether to archive is decided by whether an `artifact` record exists,
    not by `status` — a failed regeneration attempt leaves `status` as
    "failed" while still preserving the previously-completed `version`,
    `history`, and `artifact` (see run_generation_stage's failure branch).
    Gating on `status == "completed"` here would silently drop the version
    counter back to 1 and let a still-valid artifact file be overwritten
    without ever being archived.
    """
    history = list(stage_data.get("history", []))
    next_version = stage_data.get("version", 0) + 1 if stage_data.get("version") else 1

    if not stage_data.get("artifact"):
        return ArchiveResult(version=next_version, history=history, archived_path=None, restore=None)

    old_version = stage_data.get("version", 1)
    old_artifact = stage_data["artifact"]
    old_path = project_dir / old_artifact["path"]

    if not old_path.exists():
        history.append(_history_entry(stage_data, old_version, old_artifact, superseded_reason))
        return ArchiveResult(version=old_version + 1, history=history, archived_path=None, restore=None)

    history_dir = old_path.parent / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    archived_path = history_dir / f"{old_path.stem}_v{old_version}{old_path.suffix}"
    old_path.replace(archived_path)
    archived_artifact = {
        **old_artifact, "path": project_relative_path(str(archived_path), project_dir),
    }
    history.append(_history_entry(stage_data, old_version, archived_artifact, superseded_reason))

    def restore() -> None:
        if archived_path.exists():
            archived_path.replace(old_path)

    return ArchiveResult(
        version=old_version + 1, history=history, archived_path=archived_path, restore=restore,
    )


def run_generation_stage(
    project_dir: Path,
    shot_path: Path,
    stage: str,
    scope: str,
    submit_fn: Callable[[], GenerationJob],
    poll_fn: Callable[[GenerationJob], JobStatus],
    get_result_fn: Callable[[GenerationJob], T],
    result_to_artifact: Callable[[T], dict],
    provider_name: str,
    model_name: str,
    max_attempts: int = 3,
    poll_interval_seconds: float = 0.0,
    force: bool = False,
    superseded_reason: str = "regenerate",
) -> dict:
    shot = load_shot(shot_path)
    shot_id = shot["id"]

    if not is_approved(project_dir, scope, shot_id):
        raise CostGateError(
            f"shot {shot_id} is not approved for generation (scope={scope}); "
            f"run `ai-film approve-generation --scope {scope} --targets {shot_id},...` first"
        )

    stage_data = shot["generation"].get(stage, {"status": "pending", "attempts": 0})
    if stage_data.get("status") == "completed" and not force:
        return stage_data

    archive = archive_stage_artifact(project_dir, stage_data, superseded_reason)

    def on_attempt(attempt: int, job: GenerationJob | None, outcome: str) -> None:
        write_attempt_log(
            project_dir,
            shot_id,
            stage,
            attempt,
            job={"provider": job.provider, "id": job.id} if job else None,
            request={"provider": provider_name, "model": model_name},
            response={"outcome": outcome},
            outcome=outcome,
        )

    try:
        job_result = run_job(
            submit_fn=submit_fn,
            poll_fn=poll_fn,
            get_result_fn=get_result_fn,
            max_attempts=max_attempts,
            poll_interval_seconds=poll_interval_seconds,
            on_attempt=on_attempt,
        )
        artifact = result_to_artifact(job_result.result)
    except ProviderError:
        if archive.restore:
            archive.restore()
        shot["generation"][stage] = {
            **stage_data,
            "provider": provider_name,
            "model": model_name,
            "status": "failed",
            "attempts": max_attempts,
        }
        save_shot(shot_path, shot)
        raise

    if artifact.get("path"):
        artifact = {**artifact, "path": project_relative_path(artifact["path"], project_dir)}
    shot["generation"][stage] = {
        "provider": provider_name,
        "model": model_name,
        "status": "completed",
        "version": archive.version,
        "history": archive.history,
        "job": {"provider": job_result.job.provider, "id": job_result.job.id},
        "inputs": stage_data.get("inputs", []),
        "artifact": artifact,
        "attempts": job_result.attempts,
    }
    save_shot(shot_path, shot)
    return shot["generation"][stage]


def project_relative_path(path_str: str, project_dir: Path) -> str:
    """Store artifact paths relative to the project directory.

    Callers build `output_path` as `project_dir / "05_video" / ...`, so the
    artifact path returned by providers is already project-prefixed. render.py's
    build_manifest/preflight re-join manifest paths against project_dir a second
    time, so the value persisted here must be project-relative to avoid a
    double-joined path (e.g. "project/project/05_video/S01_SH01.mp4").
    """
    candidate = Path(path_str)
    try:
        return str(candidate.relative_to(project_dir))
    except ValueError:
        return path_str


def _image_artifact(result) -> dict:
    return {"path": result.artifact_path, "size_bytes": result.size_bytes, "sha256": None}


def _video_or_audio_artifact(result) -> dict:
    return {
        "path": result.artifact_path,
        "size_bytes": result.size_bytes,
        "sha256": None,
        "duration_seconds": result.duration_seconds,
    }


def _lipsync_video_artifact(result) -> dict:
    """Same shape as _video_or_audio_artifact, tagged so callers (the
    engine doesn't enforce this itself — generate_lipsync always forces,
    unlike every other stage) can tell the *current* video artifact already
    went through a lipsync pass, without needing a separate schema field or
    generation stage. A later plain generate-video regeneration replaces
    this artifact dict wholesale via the same _video_or_audio_artifact
    builder, so the tag correctly disappears again for a fresh, unsynced
    artifact — no explicit reset needed anywhere."""
    artifact = _video_or_audio_artifact(result)
    artifact["lipsynced"] = True
    return artifact


_ASPECT_RATIO_TOLERANCE = 0.02


def _probe_resolution(path: Path) -> tuple[int, int]:
    if shutil.which("ffprobe") is None:
        raise ProviderError("ffprobe is not installed or not on PATH")
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path),
        ],
        capture_output=True, text=True,
    )
    raw = probe.stdout.strip()
    try:
        width_str, height_str = raw.split("x")
        return int(width_str), int(height_str)
    except (ValueError, IndexError) as exc:
        raise ProviderError(
            f"could not parse resolution for {path}: ffprobe returned {raw!r}"
        ) from exc


def _probe_fps(path: Path) -> Fraction:
    if shutil.which("ffprobe") is None:
        raise ProviderError("ffprobe is not installed or not on PATH")
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate", "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True, text=True,
    )
    raw = probe.stdout.strip()
    try:
        return Fraction(raw)
    except (ValueError, ZeroDivisionError) as exc:
        raise ProviderError(
            f"could not parse fps for {path}: ffprobe returned {raw!r}"
        ) from exc


def _format_mismatch(target_width: int, target_height: int, actual_width: int, actual_height: int) -> bool:
    """True if the actual aspect ratio deviates from the target by more
    than _ASPECT_RATIO_TOLERANCE. Deliberately checks aspect ratio only,
    never exact resolution — no fal model this project uses can hit an
    exact target pixel size (see the design spec's provider capability
    table), so gating on exact equality would make strict_format=true
    fail every real generation regardless of whether anything is
    actually wrong."""
    target_ratio = target_width / target_height
    actual_ratio = actual_width / actual_height
    return abs(actual_ratio - target_ratio) / target_ratio > _ASPECT_RATIO_TOLERANCE


def _apply_video_format_validation(
    artifact: dict, artifact_path: Path, target_width: int, target_height: int,
    target_fps: int, strict_format: bool,
) -> dict:
    """When a real target was resolved (target_width/target_height both
    non-zero), probe the actual artifact and record requested vs. actual
    format on it — always, regardless of strict_format. Raises
    ProviderError only when strict_format is true AND the aspect ratio is
    outside tolerance; resolution/fps differences never raise in either
    mode, since render.py's own unconditional normalization is what
    actually enforces an exact final size."""
    if not target_width or not target_height:
        return artifact
    actual_width, actual_height = _probe_resolution(artifact_path)
    actual_fps = _probe_fps(artifact_path)
    artifact = {
        **artifact,
        "requested_format": {
            "width": target_width, "height": target_height,
            "fps": f"{float(target_fps):.3f}" if target_fps else None,
        },
        "actual_format": {
            "width": actual_width, "height": actual_height, "fps": f"{float(actual_fps):.3f}",
        },
    }
    if _format_mismatch(target_width, target_height, actual_width, actual_height):
        artifact["format_mismatch"] = True
        if strict_format:
            raise ProviderError(
                f"aspect ratio mismatch: requested {target_width}x{target_height}, "
                f"got {actual_width}x{actual_height} (exceeds {_ASPECT_RATIO_TOLERANCE:.0%} tolerance)"
            )
    return artifact


def _apply_image_format_validation(
    artifact: dict, artifact_path: Path, target_width: int, target_height: int, strict_format: bool,
) -> dict:
    """Same as _apply_video_format_validation, minus fps — images have no
    frame rate to probe or record."""
    if not target_width or not target_height:
        return artifact
    actual_width, actual_height = _probe_resolution(artifact_path)
    artifact = {
        **artifact,
        "requested_format": {"width": target_width, "height": target_height},
        "actual_format": {"width": actual_width, "height": actual_height},
    }
    if _format_mismatch(target_width, target_height, actual_width, actual_height):
        artifact["format_mismatch"] = True
        if strict_format:
            raise ProviderError(
                f"aspect ratio mismatch: requested {target_width}x{target_height}, "
                f"got {actual_width}x{actual_height} (exceeds {_ASPECT_RATIO_TOLERANCE:.0%} tolerance)"
            )
    return artifact


def generate_image(
    project_dir: Path,
    shot_path: Path,
    provider,
    prompt: str,
    model: str,
    reference_paths: list[str],
    output_path: Path,
    provider_name: str,
    max_attempts: int = 3,
    poll_interval_seconds: float = 0.0,
    force: bool = False,
    target_width: int = 0,
    target_height: int = 0,
    strict_format: bool = False,
) -> dict:
    request = ImageGenerationRequest(
        prompt=prompt, model=model, reference_paths=reference_paths,
        output_path=str(output_path), target_width=target_width, target_height=target_height,
    )
    validate = target_width and target_height and provider_name != "mock"

    def _artifact(result):
        artifact = _image_artifact(result)
        if validate:
            artifact = _apply_image_format_validation(
                artifact, Path(result.artifact_path), target_width, target_height, strict_format,
            )
        return artifact

    return run_generation_stage(
        project_dir=project_dir, shot_path=shot_path, stage="image",
        scope=_SCOPE_BY_STAGE["image"],
        submit_fn=lambda: provider.submit(request),
        poll_fn=provider.poll, get_result_fn=provider.get_result,
        result_to_artifact=_artifact,
        provider_name=provider_name, model_name=model,
        max_attempts=max_attempts, poll_interval_seconds=poll_interval_seconds,
        force=force,
    )


def generate_video(
    project_dir: Path,
    shot_path: Path,
    provider,
    prompt: str,
    model: str,
    reference_paths: list[str],
    duration_seconds: float,
    output_path: Path,
    provider_name: str,
    max_attempts: int = 3,
    poll_interval_seconds: float = 0.0,
    force: bool = False,
    suppress_captions: bool = True,
    end_reference_path: str = "",
    target_width: int = 0,
    target_height: int = 0,
    target_fps: int = 0,
    strict_format: bool = False,
) -> dict:
    request = VideoGenerationRequest(
        prompt=prompt, model=model, reference_paths=reference_paths,
        duration_seconds=duration_seconds, output_path=str(output_path),
        suppress_captions=suppress_captions, end_reference_path=end_reference_path,
        target_width=target_width, target_height=target_height, target_fps=target_fps,
    )
    validate = target_width and target_height and provider_name != "mock"

    def _artifact(result):
        artifact = _video_or_audio_artifact(result)
        if validate:
            artifact = _apply_video_format_validation(
                artifact, Path(result.artifact_path), target_width, target_height,
                target_fps, strict_format,
            )
        return artifact

    return run_generation_stage(
        project_dir=project_dir, shot_path=shot_path, stage="video",
        scope=_SCOPE_BY_STAGE["video"],
        submit_fn=lambda: provider.submit(request),
        poll_fn=provider.poll, get_result_fn=provider.get_result,
        result_to_artifact=_artifact,
        provider_name=provider_name, model_name=model,
        max_attempts=max_attempts, poll_interval_seconds=poll_interval_seconds,
        force=force,
    )


# fal's Kling lipsync endpoint rejects audio under 2.0s
# (audio_duration_too_short). Short dialogue lines ("I know.") routinely
# produce clips under that from TTS — confirmed against a real generation:
# a one-word line came back at 0.72s. Padded a bit past the documented
# floor for margin, not right up against it.
_MIN_LIPSYNC_AUDIO_SECONDS = 2.5


def _pad_audio_if_too_short(audio_path: Path) -> None:
    """Pad audio_path in place with trailing silence up to
    _MIN_LIPSYNC_AUDIO_SECONDS, if it's currently shorter than that.
    Silence appended after the spoken line doesn't affect lip-sync for the
    portion that's actually speech. Best-effort: if ffmpeg/ffprobe aren't
    available or probing fails, leaves the file untouched and lets the
    provider's own error (if any) surface normally rather than guessing."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        return
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path),
        ],
        capture_output=True, text=True,
    )
    try:
        duration = float(probe.stdout.strip())
    except ValueError:
        return
    if duration >= _MIN_LIPSYNC_AUDIO_SECONDS:
        return
    padded_path = audio_path.with_name(audio_path.stem + "_padded" + audio_path.suffix)
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(audio_path), "-af",
            f"apad=whole_dur={_MIN_LIPSYNC_AUDIO_SECONDS}", str(padded_path),
        ],
        capture_output=True,
    )
    if result.returncode == 0:
        padded_path.replace(audio_path)


def generate_lipsync(
    project_dir: Path,
    shot_path: Path,
    provider,
    video_path: str,
    audio_path: str,
    model: str,
    duration_seconds: float,
    output_path: Path,
    provider_name: str,
    max_attempts: int = 3,
    poll_interval_seconds: float = 0.0,
) -> dict:
    """Runs an audio-driven lip-sync pass over an already-locked video, and
    supersedes the video stage's own artifact with the synced result — the
    same version/history bookkeeping as any other video regeneration, so
    render (and everything downstream) automatically uses the synced clip
    with no changes needed there. There is no separate "lipsync" stage in
    shot.json; each pass shows up as one more entry in generation.video's
    own history, tagged via superseded_reason.

    Always supersedes the current video artifact (force=True is implicit,
    not a caller choice) — running this at all only makes sense once a
    video already exists to sync against; there is no "don't force" case.

    video_path/audio_path are copied to a temp location before
    run_generation_stage runs, because run_generation_stage archives (moves)
    the *current* video artifact before calling submit_fn — for every other
    stage that's fine, since their inputs (prompts, reference images) are
    unrelated to the artifact being superseded, but lipsync's whole input
    *is* that artifact. Without the copy, the archive move races the
    provider's upload of the same path and always wins, since archiving
    happens first by construction — confirmed against the real API: the
    archived file vanished out from under an in-flight upload every time,
    not an occasional glitch. The temp copies are immune to the move
    regardless of timing, and are cleaned up whether the call succeeds or
    fails.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="ai-film-lipsync-"))
    try:
        tmp_video_path = tmp_dir / Path(video_path).name
        tmp_audio_path = tmp_dir / Path(audio_path).name
        shutil.copy(video_path, tmp_video_path)
        shutil.copy(audio_path, tmp_audio_path)
        _pad_audio_if_too_short(tmp_audio_path)

        request = LipsyncGenerationRequest(
            video_path=str(tmp_video_path), audio_path=str(tmp_audio_path), model=model,
            duration_seconds=duration_seconds, output_path=str(output_path),
        )
        return run_generation_stage(
            project_dir=project_dir, shot_path=shot_path, stage="video",
            scope=_SCOPE_BY_STAGE["video"],
            submit_fn=lambda: provider.submit(request),
            poll_fn=provider.poll, get_result_fn=provider.get_result,
            result_to_artifact=_lipsync_video_artifact,
            provider_name=provider_name, model_name=model,
            max_attempts=max_attempts, poll_interval_seconds=poll_interval_seconds,
            force=True, superseded_reason="lipsync",
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def generate_voice(
    project_dir: Path,
    shot_path: Path,
    provider,
    text: str,
    model: str,
    speaker: str,
    output_path: Path,
    provider_name: str,
    max_attempts: int = 3,
    poll_interval_seconds: float = 0.0,
    force: bool = False,
    speaker_id: int | None = None,
    voice_preset: str | None = None,
) -> dict:
    request = VoiceGenerationRequest(
        text=text, model=model, speaker=speaker, output_path=str(output_path),
        speaker_id=speaker_id, voice_preset=voice_preset,
    )
    return run_generation_stage(
        project_dir=project_dir, shot_path=shot_path, stage="voice",
        scope=_SCOPE_BY_STAGE["voice"],
        submit_fn=lambda: provider.submit_voice(request),
        poll_fn=provider.poll, get_result_fn=provider.get_result,
        result_to_artifact=_video_or_audio_artifact,
        provider_name=provider_name, model_name=model,
        max_attempts=max_attempts, poll_interval_seconds=poll_interval_seconds,
        force=force,
    )


def generate_sfx(
    project_dir: Path,
    shot_path: Path,
    provider,
    prompt: str,
    model: str,
    video_path: str,
    output_path: Path,
    provider_name: str,
    max_attempts: int = 3,
    poll_interval_seconds: float = 0.0,
    force: bool = False,
) -> dict:
    request = SfxGenerationRequest(
        prompt=prompt, model=model, video_path=video_path, output_path=str(output_path),
    )
    return run_generation_stage(
        project_dir=project_dir, shot_path=shot_path, stage="sfx",
        scope=_SCOPE_BY_STAGE["sfx"],
        submit_fn=lambda: provider.submit_sfx(request),
        poll_fn=provider.poll, get_result_fn=provider.get_result,
        result_to_artifact=_video_or_audio_artifact,
        provider_name=provider_name, model_name=model,
        max_attempts=max_attempts, poll_interval_seconds=poll_interval_seconds,
        force=force,
    )


def generate_music(
    project_dir: Path,
    shot_path: Path,
    provider,
    prompt: str,
    model: str,
    duration_seconds: float,
    output_path: Path,
    provider_name: str,
    max_attempts: int = 3,
    poll_interval_seconds: float = 0.0,
    force: bool = False,
) -> dict:
    request = MusicGenerationRequest(
        prompt=prompt, model=model, duration_seconds=duration_seconds,
        output_path=str(output_path),
    )
    return run_generation_stage(
        project_dir=project_dir, shot_path=shot_path, stage="music",
        scope=_SCOPE_BY_STAGE["music"],
        submit_fn=lambda: provider.submit_music(request),
        poll_fn=provider.poll, get_result_fn=provider.get_result,
        result_to_artifact=_video_or_audio_artifact,
        provider_name=provider_name, model_name=model,
        max_attempts=max_attempts, poll_interval_seconds=poll_interval_seconds,
        force=force,
    )
