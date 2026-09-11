from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import typer
from dotenv import find_dotenv, load_dotenv

from ai_film import __version__
from ai_film.approval import approve_generation as approve_generation_service
from ai_film.asset_staleness import check_stale as check_stale_service
from ai_film.batch import run_bounded
from ai_film.errors import CostGateError, ProviderError
from ai_film.models import Capability
from ai_film.project import init_project
from ai_film.prompts import build_image_prompt, build_video_prompt
from ai_film.providers.fal.catalog import FalProviderCatalog
from ai_film.providers.registry import resolve_provider
from ai_film.render import RenderPreflightError, build_manifest
from ai_film.render import preflight_warnings as preflight_warnings_service
from ai_film.render import render as render_engine
from ai_film.review_gallery import build_gallery, open_in_browser
from ai_film.schema import validate_shot
from ai_film.services.candidate_service import (
    edit_candidate as edit_candidate_service,
    generate_candidates as generate_candidates_service,
    select_candidate as select_candidate_service,
)
from ai_film.services.generation_service import (
    generate_image as generate_image_service,
    generate_lipsync as generate_lipsync_service,
    generate_motion_transfer as generate_motion_transfer_service,
    generate_music as generate_music_service,
    generate_sfx as generate_sfx_service,
    generate_video as generate_video_service,
    generate_voice as generate_voice_service,
)
from ai_film.audio_fix import apply_audio_offset as apply_audio_offset_service
from ai_film.video_fix import trim_video as trim_video_service
from ai_film.video_fix import mux_audio_track as mux_audio_track_service
from ai_film.video_diagnostics import diagnose_video as diagnose_video_service
from ai_film.video_diagnostics import extract_last_frame
from ai_film.reference_analysis import analyze_reference_video as analyze_reference_video_service
from ai_film.template_store import (
    export_template as export_template_service,
    import_template as import_template_service,
    list_templates as list_templates_service,
    save_template as save_template_service,
    show_template as show_template_service,
)
from ai_film.scene_continuity import (
    add_continuity_transition as add_continuity_transition_service,
    effective_spatial_state,
    load_continuity,
    lock_continuity_master as lock_continuity_master_service,
    scene_id_for_shot,
    set_scene_continuity as set_scene_continuity_service,
)
from ai_film.feedback_store import (
    add_feedback_entry as add_feedback_entry_service,
    resolve_feedback_entry as resolve_feedback_entry_service,
)
from ai_film.media_review import build_media_review
from ai_film.shot_store import (
    list_shot_paths,
    load_shot,
    previous_shot_id,
    previous_shot_image_reference,
    save_shot,
)

def _load_env_file() -> None:
    """Load a `.env` file (e.g. FAL_KEY) from the current working directory
    or any parent, if one exists. A no-op if none is found — real
    generation then fails with the existing "FAL_KEY environment variable
    is not set" error, same as before this loaded automatically."""
    load_dotenv(find_dotenv(usecwd=True))


_load_env_file()

app = typer.Typer(name="ai-film", help="AI Film Studio production engine.")

DEFAULT_PROJECT_PATH = Path("project")
DEFAULT_TEMPLATES_PATH = Path("templates")


@app.command()
def version() -> None:
    """Print the ai-film package version."""
    typer.echo(__version__)


@app.command(name="init")
def init_cmd(title: str, path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path")) -> None:
    """Scaffold a new project directory tree and default config.json."""
    init_project(path, title)
    typer.echo(f"Initialized project '{title}' at {path}")


@app.command(name="models")
def models_cmd(
    capability: str = typer.Option(
        ..., "--capability", help="image|video|voice|sfx|music|lipsync|motion_transfer"
    ),
) -> None:
    """List the provider/model catalog for a capability (v1: fal.ai only)."""
    try:
        capability_enum = Capability(capability)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    catalog = FalProviderCatalog()
    for model in catalog.models(capability_enum):
        typer.echo(f"{model.provider}/{model.model}  {model.display_name}")


@app.command(name="status")
def status_cmd(path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path")) -> None:
    """Report every shot's aggregate status and a project-wide summary."""
    counts: dict[str, int] = {}
    for shot_path in list_shot_paths(path / "03_shots"):
        shot = load_shot(shot_path)
        counts[shot["status"]] = counts.get(shot["status"], 0) + 1
        typer.echo(f"{shot['id']}  {shot['status']}")
    if counts:
        summary = ", ".join(f"{status}: {count}" for status, count in counts.items())
        typer.echo(f"\n{sum(counts.values())} shots — {summary}")


@app.command(name="validate")
def validate_cmd(path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path")) -> None:
    """Validate every shot.json against the schema; exit 1 if any are invalid."""
    had_errors = False
    for shot_path in list_shot_paths(path / "03_shots"):
        errors = validate_shot(json.loads(shot_path.read_text()))
        if errors:
            had_errors = True
            typer.echo(f"{shot_path.name}: INVALID")
            for error in errors:
                typer.echo(f"  - {error}")
        else:
            typer.echo(f"{shot_path.name}: valid")
    if had_errors:
        raise typer.Exit(code=1)


@app.command(name="check-stale")
def check_stale_cmd(path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path")) -> None:
    """Report shots whose generated image was built against a character or
    environment reference that has since changed. Tracks shots imaged via
    generate-image/generate-all --stage image and shots locked via the
    candidate loop (generate-candidates/select-candidate). Read-only —
    never triggers regeneration itself."""
    stale = check_stale_service(path)
    if not stale:
        typer.echo("no stale shots")
        return
    typer.echo(f"{len(stale)} shot(s) reference a changed asset:")
    for entry in stale:
        typer.echo(f"\n{entry['shot_id']}")
        for asset_path in entry["changed_assets"]:
            typer.echo(f"  {asset_path} changed since generation")


def _stage_config(path: Path, stage: str, default: dict | None = None) -> dict:
    config = json.loads((path / "config.json").read_text())
    stage_config = config["providers"].get(stage, default)
    if stage_config is None:
        raise KeyError(stage)
    return stage_config, config["generation"]


_DEFAULT_TARGET_RESOLUTION = (1280, 720)
_DEFAULT_TARGET_FPS = 24

_MOTION_TRANSFER_DEFAULT_CONFIG = {
    "provider": "fal", "model": "kling-motion-control", "parameters": {},
}


def _parse_resolution(resolution: str) -> tuple[int, int]:
    width_str, height_str = resolution.lower().split("x")
    return int(width_str), int(height_str)


def _render_config(path: Path) -> dict:
    config = json.loads((path / "config.json").read_text())
    return config.get("render", {})


def _resolve_target_format(path: Path, shot_data: dict) -> tuple[int, int, int]:
    """The shot target format (width, height, fps) — an explicit per-shot
    override via shot.json's `format` field if present, else the
    project's config.json `render` defaults, else the engine's hardcoded
    fallback. Never mutates shot.json; a legacy shot with no `format`
    resolves purely at call time, every time. See the design spec's
    Terminology section for why this is an override, not a read-only
    copy."""
    shot_format = shot_data.get("format")
    if shot_format and shot_format.get("resolution"):
        width, height = _parse_resolution(shot_format["resolution"])
        fps = shot_format.get("fps", _DEFAULT_TARGET_FPS)
        return width, height, fps
    render_config = _render_config(path)
    if render_config.get("resolution"):
        width, height = _parse_resolution(render_config["resolution"])
        fps = render_config.get("fps", _DEFAULT_TARGET_FPS)
        return width, height, fps
    return (*_DEFAULT_TARGET_RESOLUTION, _DEFAULT_TARGET_FPS)


def _strict_format(path: Path) -> bool:
    return bool(_render_config(path).get("strict_format", False))


def _run_generation(shot_id: str, stage_name: str, run_fn) -> None:
    try:
        result = run_fn()
    except CostGateError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    except ProviderError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{shot_id}: {stage_name} {result['status']}")
    artifact = result.get("artifact") or {}
    if artifact.get("format_mismatch"):
        requested = artifact.get("requested_format", {})
        actual = artifact.get("actual_format", {})
        typer.echo(
            f"{shot_id}: {stage_name} format mismatch — requested "
            f"{requested.get('width')}x{requested.get('height')}, got "
            f"{actual.get('width')}x{actual.get('height')}",
            err=True,
        )


def _character_and_environment_references(path: Path, shot_data: dict) -> list[str]:
    references = []
    if (shot_data.get("environment") or {}).get("reference"):
        references.append(str(path / shot_data["environment"]["reference"]))
    references += [
        str(path / c["reference"]) for c in shot_data.get("characters", []) if c.get("reference")
    ]
    return references


def _image_references(
    path: Path, shot_id: str, shot_data: dict, quiet: bool = False
) -> list[str]:
    references = _character_and_environment_references(path, shot_data)

    prev_ref = previous_shot_image_reference(path, shot_id)
    prev_abs = str(path / prev_ref) if prev_ref else None

    scene_id = scene_id_for_shot(shot_id)
    if scene_id is not None:
        continuity = load_continuity(path, scene_id)
        master_ref = continuity.get("master_reference_image")
        if master_ref and continuity.get("master_shot") != shot_id:
            master_abs = str(path / master_ref)
            if master_abs != prev_abs:
                references.append(master_abs)

    if prev_abs:
        references.append(prev_abs)
    elif previous_shot_id(shot_id) is not None and not quiet:
        typer.echo(
            f"note: {shot_id}'s predecessor in this scene has no locked image yet — "
            f"generating without a continuity anchor",
            err=True,
        )
    return references


def _effective_spatial(path: Path, shot_id: str, shot_data: dict) -> dict:
    scene_id = scene_id_for_shot(shot_id)
    if scene_id is None:
        return {}
    full_state = effective_spatial_state(load_continuity(path, scene_id), shot_id)
    shot_characters = {c["name"] for c in shot_data.get("characters", [])}
    return {name: values for name, values in full_state.items() if name in shot_characters}


def _shot_features(shot_data: dict, continue_from_previous: bool = False) -> list[str]:
    """Ordered, most-specific-first tags describing this shot, used only to
    key optional per-feature video-model overrides in config. Add a tag
    here whenever some model needs different handling for a class of shot
    — today: "continue_from_previous" (this call passed
    --continue-from-previous — only a model in MODELS_WITH_END_IMAGE_URL
    actually benefits from the dual-keyframe continuity that flag sets up,
    see providers/fal/video.py) ahead of "dialogue" vs "silent" (some
    models generate their own uncontrollable talking motion/audio on shots
    with no assigned line at all, not just ones with dialogue).
    continue_from_previous is a per-call concern, not shot data, so it's
    listed first but never replaces the dialogue/silent tag — both are
    returned so a project can configure an override for one, the other, or
    both, and the first one present in model_by_feature wins."""
    features = []
    if continue_from_previous:
        features.append("continue_from_previous")
    if shot_data.get("dialogue", {}).get("text"):
        features.append("dialogue")
    else:
        features.append("silent")
    return features


def _video_model(stage_config: dict, shot_data: dict, continue_from_previous: bool = False) -> str:
    """Pick which video model to use for a shot. `providers.video.model` is
    the default; an optional `providers.video.model_by_feature` maps a shot
    feature tag (see `_shot_features`) to an override model — e.g. a model
    prone to inventing its own talking motion can be kept off silent shots
    by defaulting elsewhere and opting it in only for `"dialogue"`, where a
    lipsync pass fixes what it invents anyway; or a model that honors
    end_image_url can be opted in only for `"continue_from_previous"` so
    dual-keyframe shots automatically get it without a project-wide model
    change. First matching feature wins; falls back to the default model
    if no feature has an override."""
    overrides = stage_config.get("model_by_feature", {})
    for feature in _shot_features(shot_data, continue_from_previous):
        if feature in overrides:
            return overrides[feature]
    return stage_config["model"]


def _load_voice_cast(path: Path) -> dict:
    """Optional per-project voice cast: 01_bibles/voices.json maps a
    character name to their locked voice, per model — {"csm_speaker_id":
    <int>, "speech_voice_preset": <str>} — picked by ear, since neither a
    csm-1b speaker slot nor a speech-02-hd preset has any inherent
    gender/identity tie without this. Empty dict if the file doesn't
    exist or a character isn't listed."""
    voices_path = path / "01_bibles" / "voices.json"
    if not voices_path.exists():
        return {}
    return json.loads(voices_path.read_text())


def _speaker_voice_id(path: Path, speaker: str) -> int | None:
    """csm-1b's speaker_id for `speaker`, or None to fall back to the
    provider's hash-based default."""
    return _load_voice_cast(path).get(speaker, {}).get("csm_speaker_id")


def _voice_preset(path: Path, speaker: str) -> str | None:
    """speech-02-hd's named voice_id for `speaker`, or None to fall back to
    the provider's generic default voice."""
    return _load_voice_cast(path).get(speaker, {}).get("speech_voice_preset")


def _video_references(path: Path, shot_data: dict) -> list[str]:
    image_artifact = shot_data.get("generation", {}).get("image", {}).get("artifact")
    if image_artifact and image_artifact.get("path"):
        return [str(path / image_artifact["path"])]
    return _character_and_environment_references(path, shot_data)


def _base_video_artifact(shot_data: dict) -> dict | None:
    """The shot's pre-lipsync video artifact. SFX generation must always
    consume this, never a lipsynced (audio-bearing) artifact — ThinkSound's
    own behavior mixing with audio already present in a source video is
    unverified, and SFX must never risk stepping on lip-synced dialogue.
    The current video artifact IS the base video unless it's been through
    lipsync (tagged "lipsynced": true, cleared again by any later plain
    video regeneration — see _lipsync_video_artifact's own docstring), in
    which case the base version lives in history: the most recent entry
    that isn't itself tagged lipsynced."""
    video_stage = shot_data.get("generation", {}).get("video", {})
    current = video_stage.get("artifact")
    if current and not current.get("lipsynced"):
        return current
    for entry in reversed(video_stage.get("history", [])):
        artifact = entry.get("artifact")
        if artifact and not artifact.get("lipsynced"):
            return artifact
    return None


@app.command(name="generate-image")
def generate_image_cmd(
    shot: str = typer.Option(..., "--shot"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    stage_config, gen_config = _stage_config(path, "image")
    shot_path = path / "03_shots" / f"{shot}.json"
    shot_data = load_shot(shot_path)
    references = _image_references(path, shot, shot_data)
    target_width, target_height, _target_fps = _resolve_target_format(path, shot_data)
    strict_format = _strict_format(path)

    def _run():
        provider = resolve_provider(Capability.IMAGE, stage_config["provider"])
        return generate_image_service(
            project_dir=path, shot_path=shot_path, provider=provider,
            prompt=build_image_prompt(shot_data, spatial=_effective_spatial(path, shot, shot_data)),
            model=stage_config["model"],
            reference_paths=references, output_path=path / "04_storyboard" / f"{shot}.png",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
            target_width=target_width, target_height=target_height, strict_format=strict_format,
        )

    _run_generation(shot, "image", _run)


def _video_duration(shot_data: dict) -> float:
    """The target video length: for a shot with dialogue whose voice is
    already generated, this is the voice's own measured duration — so video
    and voice end up the same length by construction instead of drifting
    apart (video sized off the shot's static duration_seconds, voice sized
    off however long the TTS naturally takes for the text, with nothing
    ever reconciling the two). Falls back to the shot's static
    duration_seconds, unchanged from today, whenever there's no dialogue or
    the voice hasn't been generated yet."""
    dialogue_text = shot_data.get("dialogue", {}).get("text", "")
    voice = shot_data.get("generation", {}).get("voice", {})
    if dialogue_text and voice.get("status") == "completed":
        voice_duration = (voice.get("artifact") or {}).get("duration_seconds")
        if voice_duration:
            return voice_duration
    return shot_data["duration_seconds"]


def _continue_from_previous_references(
    path: Path, shot: str, shot_data: dict
) -> tuple[list[str], str]:
    """Resolve --continue-from-previous into (reference_paths, end_reference_path):
    the previous shot's current video's last frame as the sole starting
    reference, and this shot's own locked storyboard image as the end
    reference — true dual-keyframe continuity for models that honor
    end_image_url (see MODELS_WITH_END_IMAGE_URL in providers/fal/video.py);
    silently ignored by models that don't. Falls back to this shot's normal
    references with no end frame, printing why, whenever there's no usable
    predecessor video or no locked end image to aim for."""
    fallback = (_video_references(path, shot_data), "")
    prev_id = previous_shot_id(shot)
    if prev_id is None:
        typer.echo(
            f"note: {shot} is a scene's first shot — --continue-from-previous has no predecessor to use",
            err=True,
        )
        return fallback
    prev_shot_path = path / "03_shots" / f"{prev_id}.json"
    if not prev_shot_path.exists():
        typer.echo(
            f"note: {shot}'s predecessor {prev_id} not found — ignoring --continue-from-previous",
            err=True,
        )
        return fallback
    prev_video = load_shot(prev_shot_path).get("generation", {}).get("video", {}).get("artifact")
    if not prev_video or not prev_video.get("path"):
        typer.echo(
            f"note: {shot}'s predecessor {prev_id} has no video yet — ignoring --continue-from-previous",
            err=True,
        )
        return fallback
    end_artifact = shot_data.get("generation", {}).get("image", {}).get("artifact")
    if not end_artifact or not end_artifact.get("path"):
        typer.echo(
            f"note: {shot} has no locked storyboard image yet — ignoring --continue-from-previous",
            err=True,
        )
        return fallback
    last_frame_path = path / "05_video" / "last_frame" / f"{prev_id}.png"
    extract_last_frame(path / prev_video["path"], last_frame_path)
    return [str(last_frame_path)], str(path / end_artifact["path"])


@app.command(name="generate-video")
def generate_video_cmd(
    shot: str = typer.Option(..., "--shot"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
    force: bool = typer.Option(False, "--force"),
    continue_from_previous: bool = typer.Option(
        False,
        "--continue-from-previous",
        help=(
            "Start this shot's video from the previous shot's last frame, "
            "ending at this shot's own locked storyboard image (dual-keyframe "
            "continuity). Only models in MODELS_WITH_END_IMAGE_URL (h3-max) "
            "honor the end frame; other models fall back to the start frame only."
        ),
    ),
) -> None:
    stage_config, gen_config = _stage_config(path, "video")
    shot_path = path / "03_shots" / f"{shot}.json"
    shot_data = load_shot(shot_path)
    if continue_from_previous:
        references, end_reference_path = _continue_from_previous_references(path, shot, shot_data)
    else:
        references, end_reference_path = _video_references(path, shot_data), ""
    target_width, target_height, target_fps = _resolve_target_format(path, shot_data)
    strict_format = _strict_format(path)

    def _run():
        provider = resolve_provider(Capability.VIDEO, stage_config["provider"])
        return generate_video_service(
            project_dir=path, shot_path=shot_path, provider=provider,
            prompt=build_video_prompt(shot_data),
            model=_video_model(stage_config, shot_data, continue_from_previous),
            reference_paths=references, duration_seconds=_video_duration(shot_data),
            output_path=path / "05_video" / f"{shot}.mp4",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
            suppress_captions=not stage_config.get("parameters", {}).get("captions", False),
            end_reference_path=end_reference_path,
            target_width=target_width, target_height=target_height, target_fps=target_fps,
            strict_format=strict_format,
        )

    _run_generation(shot, "video", _run)


@app.command(name="generate-lipsync")
def generate_lipsync_cmd(
    shot: str = typer.Option(..., "--shot"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Run an audio-driven lip-sync pass over an already-locked video and
    voice, superseding the video artifact with the synced result (same
    version/history bookkeeping as any other video regeneration)."""
    stage_config, gen_config = _stage_config(path, "lipsync")
    shot_path = path / "03_shots" / f"{shot}.json"
    shot_data = load_shot(shot_path)
    video_artifact = shot_data.get("generation", {}).get("video", {}).get("artifact")
    voice_artifact = shot_data.get("generation", {}).get("voice", {}).get("artifact")
    if not video_artifact or not video_artifact.get("path"):
        typer.echo(f"{shot}: video must be generated before lipsync", err=True)
        raise typer.Exit(code=1)
    if not voice_artifact or not voice_artifact.get("path"):
        typer.echo(f"{shot}: voice must be generated before lipsync", err=True)
        raise typer.Exit(code=1)

    def _run():
        provider = resolve_provider(Capability.LIPSYNC, stage_config["provider"])
        return generate_lipsync_service(
            project_dir=path, shot_path=shot_path, provider=provider,
            video_path=str(path / video_artifact["path"]),
            audio_path=str(path / voice_artifact["path"]),
            model=stage_config["model"],
            duration_seconds=video_artifact.get("duration_seconds") or shot_data["duration_seconds"],
            output_path=path / "05_video" / f"{shot}.mp4",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"],
        )

    _run_generation(shot, "lipsync", _run)


@app.command(name="generate-motion-transfer")
def generate_motion_transfer_cmd(
    shot: str = typer.Option(..., "--shot"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Generate a shot's video by retargeting a driving reference video's
    motion onto the shot's first character's locked reference image,
    instead of prompt-driven text-to-video. Reads both inputs from the
    shot's own fields (driving_video.path, characters[0].reference) — no
    CLI flags for either, matching generate-lipsync's pattern of reading
    already-set shot fields. Writes into the same generation.video slot
    generate-video uses; idempotent by default, --force to regenerate."""
    stage_config, gen_config = _stage_config(
        path, "motion_transfer", default=_MOTION_TRANSFER_DEFAULT_CONFIG,
    )
    shot_path = path / "03_shots" / f"{shot}.json"
    shot_data = load_shot(shot_path)

    driving_video = (shot_data.get("driving_video") or {}).get("path")
    if not driving_video:
        typer.echo(f"{shot}: no driving_video.path set in shot.json", err=True)
        raise typer.Exit(code=1)
    driving_video_path = path / driving_video
    if not driving_video_path.exists():
        typer.echo(f"{shot}: driving video not found at {driving_video_path}", err=True)
        raise typer.Exit(code=1)

    characters = shot_data.get("characters", [])
    if not characters or not characters[0].get("reference"):
        typer.echo(
            f"{shot}: motion-transfer requires characters[0] to have a locked reference image",
            err=True,
        )
        raise typer.Exit(code=1)
    image_path = path / characters[0]["reference"]

    target_width, target_height, target_fps = _resolve_target_format(path, shot_data)
    strict_format = _strict_format(path)

    def _run():
        provider = resolve_provider(Capability.MOTION_TRANSFER, stage_config["provider"])
        return generate_motion_transfer_service(
            project_dir=path, shot_path=shot_path, provider=provider,
            image_path=str(image_path), driving_video_path=str(driving_video_path),
            model=stage_config["model"], output_path=path / "05_video" / f"{shot}.mp4",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
            target_width=target_width, target_height=target_height, target_fps=target_fps,
            strict_format=strict_format,
        )

    _run_generation(shot, "motion-transfer", _run)


@app.command(name="generate-voice")
def generate_voice_cmd(
    shot: str = typer.Option(..., "--shot"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    stage_config, gen_config = _stage_config(path, "voice")
    shot_path = path / "03_shots" / f"{shot}.json"
    shot_data = load_shot(shot_path)
    dialogue = shot_data.get("dialogue", {})

    def _run():
        provider = resolve_provider(Capability.VOICE, stage_config["provider"])
        return generate_voice_service(
            project_dir=path, shot_path=shot_path, provider=provider,
            text=dialogue.get("text", ""), model=stage_config["model"],
            speaker=dialogue.get("speaker", ""),
            speaker_id=_speaker_voice_id(path, dialogue.get("speaker", "")),
            voice_preset=_voice_preset(path, dialogue.get("speaker", "")),
            output_path=path / "06_audio" / "dialogue" / f"{shot}.wav",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
        )

    _run_generation(shot, "voice", _run)


@app.command(name="generate-sfx")
def generate_sfx_cmd(
    shot: str = typer.Option(..., "--shot"),
    prompt: str = typer.Option(..., "--prompt"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    stage_config, gen_config = _stage_config(path, "sfx")
    shot_path = path / "03_shots" / f"{shot}.json"
    shot_data = load_shot(shot_path)
    base_video = _base_video_artifact(shot_data)
    if not base_video or not base_video.get("path"):
        typer.echo(f"{shot}: video must be generated before sfx", err=True)
        raise typer.Exit(code=1)

    def _run():
        provider = resolve_provider(Capability.SFX, stage_config["provider"])
        return generate_sfx_service(
            project_dir=path, shot_path=shot_path, provider=provider,
            prompt=prompt, model=stage_config["model"],
            video_path=str(path / base_video["path"]),
            output_path=path / "06_audio" / "sfx" / f"{shot}.wav",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
        )

    _run_generation(shot, "sfx", _run)


@app.command(name="generate-music")
def generate_music_cmd(
    shot: str = typer.Option(..., "--shot"),
    prompt: str = typer.Option(..., "--prompt"),
    duration_seconds: float = typer.Option(30.0, "--duration-seconds"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    stage_config, gen_config = _stage_config(path, "music")
    shot_path = path / "03_shots" / f"{shot}.json"

    def _run():
        provider = resolve_provider(Capability.MUSIC, stage_config["provider"])
        return generate_music_service(
            project_dir=path, shot_path=shot_path, provider=provider,
            prompt=prompt, model=stage_config["model"], duration_seconds=duration_seconds,
            output_path=path / "06_audio" / "music" / f"{shot}.wav",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
        )

    _run_generation(shot, "music", _run)


@app.command(name="check-continuity")
def check_continuity_cmd(
    shot: str = typer.Option(..., "--shot"),
    status: str = typer.Option(..., "--status", help="passed|warning|failed"),
    issue: list[str] = typer.Option([], "--issue"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Record the Continuity agent's text/spec-level judgment onto shot.json."""
    shot_path = path / "03_shots" / f"{shot}.json"
    shot_data = load_shot(shot_path)
    shot_data["continuity"] = {
        "status": status,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "issues": list(issue),
    }
    try:
        save_shot(shot_path, shot_data)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{shot}: continuity {status}")


@app.command(name="set-scene-continuity")
def set_scene_continuity_cmd(
    scene: str = typer.Option(..., "--scene"),
    character: str = typer.Option(..., "--character"),
    screen_side: str = typer.Option(..., "--screen-side", help="left|center|right"),
    facing: str = typer.Option(..., "--facing", help="left|right|camera|away"),
    master_shot: str = typer.Option(None, "--master-shot"),
    force: bool = typer.Option(False, "--force"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Set one character's initial screen_side/facing in a scene's spatial
    canon. Fails on a conflicting re-set unless --force — never silently
    overwrites an established state."""
    try:
        set_scene_continuity_service(
            path, scene, character, screen_side, facing,
            master_shot=master_shot, force=force,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{scene}: {character} set to screen_side={screen_side} facing={facing}")


@app.command(name="add-continuity-transition")
def add_continuity_transition_cmd(
    scene: str = typer.Option(..., "--scene"),
    after_shot: str = typer.Option(..., "--after-shot"),
    character: str = typer.Option(..., "--character"),
    screen_side: str = typer.Option(..., "--screen-side"),
    facing: str = typer.Option(..., "--facing"),
    reason: str = typer.Option(..., "--reason"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Declare (or extend) an explicit blocking change for a scene, taking
    effect starting the shot immediately after --after-shot."""
    try:
        add_continuity_transition_service(
            path, scene, after_shot, character, screen_side, facing, reason,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(
        f"{scene}: transition after {after_shot} — {character} -> "
        f"screen_side={screen_side} facing={facing}"
    )


@app.command(name="lock-continuity-master")
def lock_continuity_master_cmd(
    scene: str = typer.Option(..., "--scene"),
    force: bool = typer.Option(False, "--force"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Freeze the scene's master_shot's current locked image as the scene's
    permanent master reference — a one-time snapshot, never live-tracked."""
    try:
        data = lock_continuity_master_service(path, scene, force=force)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{scene}: master reference locked at {data['master_reference_image']}")


@app.command(name="show-continuity")
def show_continuity_cmd(
    shot: str = typer.Option(..., "--shot"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Print the effective spatial state (screen_side/facing per
    character) for a shot, folding in every transition that applies by
    that shot."""
    scene_id = scene_id_for_shot(shot)
    if scene_id is None:
        typer.echo(f"{shot}: doesn't match the S<SS>_SH<NN> shot-id convention", err=True)
        raise typer.Exit(code=1)
    continuity = load_continuity(path, scene_id)
    if not continuity.get("spatial") and not continuity.get("transitions"):
        typer.echo(f"{shot}: no continuity file for scene {scene_id}")
        return
    state = effective_spatial_state(continuity, shot)
    if not state:
        typer.echo(f"{shot}: continuity file exists but no characters have a recorded state yet")
        return
    for character, values in state.items():
        typer.echo(
            f"{character}: screen_side={values['screen_side']} facing={values['facing']}"
        )


@app.command(name="approve-generation")
def approve_generation_cmd(
    scope: str = typer.Option(..., "--scope", help="bibles|storyboard"),
    targets: str = typer.Option(..., "--targets", help="comma-separated target IDs"),
    estimated_cost: float = typer.Option(None, "--estimated-cost"),
    revision: int = typer.Option(None, "--revision"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    target_ids = [t for t in targets.split(",") if t]
    try:
        approve_generation_service(path, scope, target_ids, estimated_cost, revision)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"approved {scope}: {len(target_ids)} target(s)")


@app.command(name="add-feedback")
def add_feedback_cmd(
    shot: str = typer.Option(..., "--shot"),
    target: str = typer.Option(..., "--target", help="video|voice|sfx|music|sync"),
    note: str = typer.Option(..., "--note"),
    at: float = typer.Option(None, "--at"),
    range_start: float = typer.Option(None, "--range-start"),
    range_end: float = typer.Option(None, "--range-end"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Record a piece of review feedback for a shot's video/voice/sfx/music/sync."""
    try:
        entry = add_feedback_entry_service(
            project_dir=path, shot_id=shot, target=target, note=note,
            at=at, range_start=range_start, range_end=range_end,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{shot}: added feedback {entry['id']}")


@app.command(name="resolve-feedback")
def resolve_feedback_cmd(
    shot: str = typer.Option(..., "--shot"),
    id: str = typer.Option(..., "--id"),
    resolution: str = typer.Option(None, "--resolution"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Mark a feedback entry as resolved."""
    try:
        entry = resolve_feedback_entry_service(path, shot, id, resolution)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{shot}: resolved {entry['id']}")


@app.command(name="apply-audio-offset")
def apply_audio_offset_cmd(
    shot: str = typer.Option(..., "--shot"),
    track: str = typer.Option(..., "--track", help="voice|sfx|music"),
    offset_ms: float = typer.Option(..., "--offset-ms"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Nudge an audio track's start time via ffmpeg — no provider spend."""
    shot_path = path / "03_shots" / f"{shot}.json"
    try:
        stage = apply_audio_offset_service(path, shot_path, track, offset_ms)
    except (ValueError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{shot}: {track} now at version {stage['version']}")


@app.command(name="trim-video")
def trim_video_cmd(
    shot: str = typer.Option(..., "--shot"),
    end_seconds: float = typer.Option(..., "--end-seconds"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Cut a completed video artifact down to its first `end_seconds` via
    ffmpeg — no provider spend. Use when a video model's minimum-duration
    floor leaves trailing dead air past the real dialogue length."""
    shot_path = path / "03_shots" / f"{shot}.json"
    try:
        stage = trim_video_service(path, shot_path, end_seconds)
    except (ValueError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{shot}: video trimmed to {end_seconds}s, now at version {stage['version']}")


@app.command(name="mux-audio")
def mux_audio_cmd(
    shot: str = typer.Option(..., "--shot"),
    track: str = typer.Option(..., "--track", help="voice|sfx"),
    force: bool = typer.Option(False, "--force"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Mix the shot's already-generated voice or sfx track into its current
    video via ffmpeg — no provider spend. Layers onto any existing audio,
    never replaces it. Requires generate-voice/generate-sfx to have
    already run; never generates the track itself. `--track voice` is for
    an off-screen speaker's line (no on-screen mouth to lip-sync) — it
    refuses an on-screen speaker's line, which needs generate-lipsync
    instead. `--track sfx` is scoped to shot-bound event SFX only — never
    use this for scene ambience or film-level music."""
    shot_path = path / "03_shots" / f"{shot}.json"
    try:
        stage = mux_audio_track_service(path, shot_path, track, force=force)
    except (ValueError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{shot}: {track} muxed into video, now at version {stage['version']}")


@app.command(name="diagnose-video")
def diagnose_video_cmd(
    shot: str = typer.Option(..., "--shot"),
    interval_seconds: float = typer.Option(0.5, "--interval-seconds"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Extract frames from a shot's current video at a fixed interval and
    report silence windows in its audio track — read the printed frame
    paths to check by eye whether mouth movement lines up with real audio."""
    shot_path = path / "03_shots" / f"{shot}.json"
    try:
        report = diagnose_video_service(path, shot_path, interval_seconds)
    except (ValueError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(
        f"{shot}: video={report['video_path']} duration={report['duration_seconds']:.2f}s"
    )
    if report["silence_windows"] is None:
        typer.echo("  audio: no audio track")
    else:
        for window in report["silence_windows"]:
            typer.echo(f"  silence: {window['start']:.2f}s - {window['end']:.2f}s")
    for frame in report["frames"]:
        typer.echo(f"  frame {frame['t']:.2f}s -> {frame['path']}")


@app.command(name="analyze-reference-video")
def analyze_reference_video_cmd(
    source: Path = typer.Option(..., "--source"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Analyze a local reference video: scene cuts, a coarse per-scene
    motion signal, and keyframes — local ffmpeg only, no fal cost. Writes
    assets/reference-video/video_analysis_brief.json for
    ai-film-reference-analyst to read and enrich."""
    try:
        brief = analyze_reference_video_service(path, source, force=force)
    except (ValueError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    brief_path = path / "assets" / "reference-video" / "video_analysis_brief.json"
    typer.echo(f"analyzed {source.name}: {len(brief['scenes'])} scene(s) -> {brief_path}")


@app.command(name="save-template")
def save_template_cmd(
    from_: Path = typer.Option(..., "--from"),
    id: str = typer.Option(..., "--id"),
    templates_dir: Path = typer.Option(DEFAULT_TEMPLATES_PATH, "--templates-dir"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Validate a draft template JSON and save it to templates/<id>/,
    copying any referenced keyframe images alongside it."""
    try:
        save_template_service(from_, id, templates_dir, force=force)
    except (ValueError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"saved template {id} -> {templates_dir / id / 'template.json'}")


@app.command(name="list-templates")
def list_templates_cmd(
    templates_dir: Path = typer.Option(DEFAULT_TEMPLATES_PATH, "--templates-dir"),
) -> None:
    """List every saved template with its name and shot-pattern count."""
    templates = list_templates_service(templates_dir)
    if not templates:
        typer.echo("no templates found")
        return
    for template in templates:
        typer.echo(
            f"{template['id']:<20} {template['name']:<30} ({template['shot_pattern_count']} shot patterns)"
        )


@app.command(name="show-template")
def show_template_cmd(
    id: str = typer.Option(..., "--id"),
    templates_dir: Path = typer.Option(DEFAULT_TEMPLATES_PATH, "--templates-dir"),
) -> None:
    """Print a saved template's full JSON content."""
    try:
        template = show_template_service(id, templates_dir)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(json.dumps(template, indent=2, ensure_ascii=False))


@app.command(name="export-template")
def export_template_cmd(
    id: str = typer.Option(..., "--id"),
    templates_dir: Path = typer.Option(DEFAULT_TEMPLATES_PATH, "--templates-dir"),
    output: Path = typer.Option(..., "--output"),
) -> None:
    """Bundle a saved template's template.json and keyframes into a zip
    archive that can be handed to someone else's ai-film-studio install."""
    try:
        export_template_service(id, templates_dir, output)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"exported {id} -> {output}")


@app.command(name="import-template")
def import_template_cmd(
    from_: Path = typer.Option(..., "--from"),
    templates_dir: Path = typer.Option(DEFAULT_TEMPLATES_PATH, "--templates-dir"),
    id: str = typer.Option(None, "--id"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Import a template zip archive exported by export-template. Uses the
    archive's own id unless --id overrides it."""
    try:
        result = import_template_service(from_, templates_dir, template_id=id, force=force)
    except (ValueError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"imported {result['id']} -> {templates_dir / result['id'] / 'template.json'}")


@app.command(name="review-media")
def review_media_cmd(
    shot: str = typer.Option(..., "--shot"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Build (or rebuild) the video/audio review page for a shot and open it."""
    try:
        html_path = build_media_review(path, shot)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    open_in_browser(html_path)
    typer.echo(f"opened {html_path}")


@app.command(name="render")
def render_cmd(path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path")) -> None:
    manifest = build_manifest(path)
    try:
        for warning in preflight_warnings_service(manifest, path):
            typer.echo(warning, err=True)
    except RuntimeError:
        # preflight_warnings is purely diagnostic (see its docstring) — a
        # probe failure here (e.g. an artifact ffprobe can't parse) must
        # never block the render itself, only the warning it would have
        # printed.
        pass
    try:
        output_path = render_engine(path, manifest)
    except RenderPreflightError as exc:
        typer.echo("render preflight failed:", err=True)
        for error in exc.errors:
            typer.echo(f"  - {error}", err=True)
        raise typer.Exit(code=1)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"rendered {output_path}")


_BATCH_SERVICE_BY_STAGE = {
    "image": (Capability.IMAGE, generate_image_service),
    "video": (Capability.VIDEO, generate_video_service),
    "voice": (Capability.VOICE, generate_voice_service),
}


def _build_stage_call(path: Path, shot_id: str, stage: str, force: bool):
    capability, service_fn = _BATCH_SERVICE_BY_STAGE[stage]
    stage_config, gen_config = _stage_config(path, stage)
    shot_path = path / "03_shots" / f"{shot_id}.json"
    shot_data = load_shot(shot_path)
    provider = resolve_provider(capability, stage_config["provider"])

    if stage == "image":
        references = _image_references(path, shot_id, shot_data, quiet=True)
        target_width, target_height, _target_fps = _resolve_target_format(path, shot_data)
        strict_format = _strict_format(path)
        return lambda: service_fn(
            project_dir=path, shot_path=shot_path, provider=provider,
            prompt=build_image_prompt(
                shot_data, spatial=_effective_spatial(path, shot_id, shot_data),
            ),
            model=stage_config["model"],
            reference_paths=references, output_path=path / "04_storyboard" / f"{shot_id}.png",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
            target_width=target_width, target_height=target_height, strict_format=strict_format,
        )
    if stage == "video":
        references = _video_references(path, shot_data)
        target_width, target_height, target_fps = _resolve_target_format(path, shot_data)
        strict_format = _strict_format(path)
        return lambda: service_fn(
            project_dir=path, shot_path=shot_path, provider=provider,
            prompt=build_video_prompt(shot_data), model=_video_model(stage_config, shot_data),
            reference_paths=references, duration_seconds=_video_duration(shot_data),
            output_path=path / "05_video" / f"{shot_id}.mp4",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
            suppress_captions=not stage_config.get("parameters", {}).get("captions", False),
            target_width=target_width, target_height=target_height, target_fps=target_fps,
            strict_format=strict_format,
        )
    dialogue = shot_data.get("dialogue", {})
    return lambda: service_fn(
        project_dir=path, shot_path=shot_path, provider=provider,
        text=dialogue.get("text", ""), model=stage_config["model"],
        speaker=dialogue.get("speaker", ""),
        speaker_id=_speaker_voice_id(path, dialogue.get("speaker", "")),
        voice_preset=_voice_preset(path, dialogue.get("speaker", "")),
        output_path=path / "06_audio" / "dialogue" / f"{shot_id}.wav",
        provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
        poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
    )


@app.command(name="generate-all")
def generate_all_cmd(
    stage: str = typer.Option(..., "--stage", help="image|video|voice"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Generate `stage` for every shot, bounded by config.generation.max_parallel_jobs."""
    if stage not in _BATCH_SERVICE_BY_STAGE:
        typer.echo(
            f"generate-all supports stage in {sorted(_BATCH_SERVICE_BY_STAGE)}, got {stage!r}",
            err=True,
        )
        raise typer.Exit(code=1)

    _, gen_config = _stage_config(path, stage)
    shot_ids = [p.stem for p in list_shot_paths(path / "03_shots")]
    calls = [_build_stage_call(path, shot_id, stage, force) for shot_id in shot_ids]
    results = run_bounded(calls, max_workers=gen_config["max_parallel_jobs"])

    failed_ids = {shot_id for shot_id, error in zip(shot_ids, results) if error is not None}
    for shot_id in shot_ids:
        status = "FAILED" if shot_id in failed_ids else "done"
        typer.echo(f"{shot_id}: {stage} {status}")
    if failed_ids:
        for shot_id, error in zip(shot_ids, results):
            if error is not None:
                typer.echo(f"  {shot_id}: {error}", err=True)
        raise typer.Exit(code=1)


@app.command(name="generate-candidates")
def generate_candidates_cmd(
    target: str = typer.Option(..., "--target"),
    count: int = typer.Option(..., "--count"),
    prompt: str = typer.Option(None, "--prompt"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Generate N image candidates for a character:/env:/shot: target (cost-gated)."""
    stage_config, gen_config = _stage_config(path, "image")

    references: list[str] = []
    if target.startswith("shot:"):
        shot_id = target.split(":")[1]
        shot_data = load_shot(path / "03_shots" / f"{shot_id}.json")
        references = _image_references(path, shot_id, shot_data)
        if prompt is None:
            prompt = build_image_prompt(
                shot_data, spatial=_effective_spatial(path, shot_id, shot_data),
            )
    elif prompt is None:
        typer.echo("--prompt is required for character:/env: targets", err=True)
        raise typer.Exit(code=1)

    def _run():
        provider = resolve_provider(Capability.IMAGE, stage_config["provider"])
        kwargs = {"reference_paths": references} if target.startswith("shot:") else {}
        return generate_candidates_service(
            project_dir=path, target=target, provider=provider,
            prompt=prompt, model=stage_config["model"], count=count,
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"],
            **kwargs,
        )

    try:
        result = _run()
    except CostGateError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    except ProviderError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{target}: generated {len(result['added'])} candidate(s): {', '.join(result['added'])}")
    _rebuild_gallery(path, target)


def _rebuild_gallery(path: Path, target: str) -> None:
    """Keep review.html current with whatever candidates are actually on
    disk after generate-candidates/edit-candidate add one — without this,
    the gallery a human has open silently falls behind (a freshly edited
    candidate doesn't show up until someone remembers to run `ai-film
    review` by hand). Best-effort and non-fatal: a gallery-rebuild failure
    must never turn an otherwise-successful generate/edit into a reported
    failure, since the real work (the new candidate file, the shot.json
    entry) already succeeded and persisted before this runs."""
    try:
        html_path = build_gallery(path, target)
    except ValueError as exc:
        typer.echo(f"{target}: could not rebuild review gallery: {exc}", err=True)
        return
    typer.echo(f"{target}: review {html_path}")


@app.command(name="review")
def review_cmd(
    target: str = typer.Option(..., "--target"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Build (or rebuild) the candidate review gallery and open it in the browser."""
    try:
        html_path = build_gallery(path, target)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    open_in_browser(html_path)
    typer.echo(f"opened {html_path}")


@app.command(name="select-candidate")
def select_candidate_cmd(
    target: str = typer.Option(..., "--target"),
    id: str = typer.Option(..., "--id"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Lock in a candidate as the canonical artifact for a target."""
    try:
        result = select_candidate_service(path, target, id)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{target}: selected {id} -> {result['canonical_path']}")


@app.command(name="edit-candidate")
def edit_candidate_cmd(
    target: str = typer.Option(..., "--target"),
    id: str = typer.Option(..., "--id"),
    instruction: str = typer.Option(..., "--instruction"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Refine a candidate via true edit (if the provider supports it) or regeneration."""
    stage_config, gen_config = _stage_config(path, "image")

    def _run():
        provider = resolve_provider(Capability.IMAGE, stage_config["provider"])
        return edit_candidate_service(
            project_dir=path, target=target, candidate_id=id, instruction=instruction,
            provider=provider, provider_name=stage_config["provider"], model=stage_config["model"],
            max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"],
        )

    try:
        entry = _run()
    except CostGateError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    except ProviderError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{target}: added candidate {entry['id']} (edit of {entry['parent']})")
    _rebuild_gallery(path, target)


if __name__ == "__main__":
    app()
