import shutil
import subprocess
from pathlib import Path

import pytest

from ai_film.approval import approve_generation
from ai_film.errors import CostGateError, ProviderError
from ai_film.models import (
    Capability,
    GenerationJob,
    ImageGenerationResult,
    JobStatus,
    VideoGenerationResult,
)
from ai_film.providers.mock.image import MockImageProvider
from ai_film.services.generation_service import (
    _MIN_LIPSYNC_AUDIO_SECONDS,
    _pad_audio_if_too_short,
    generate_image,
    generate_video,
)
from ai_film.shot_store import load_shot, save_shot


def _project(tmp_path: Path) -> Path:
    (tmp_path / "config.json").write_text('{"providers": {}, "generation": {}}')
    return tmp_path


def _shot_path(project_dir: Path, shot_id: str = "S01_SH01") -> Path:
    path = project_dir / "03_shots" / f"{shot_id}.json"
    save_shot(
        path,
        {
            "schema_version": "1.0",
            "id": shot_id,
            "status": "draft",
            "duration_seconds": 5,
            "continuity": {"status": "passed", "checked_at": None, "issues": []},
            "generation": {
                "image": {"status": "pending", "attempts": 0},
                "video": {"status": "pending", "attempts": 0},
                "voice": {"status": "pending", "attempts": 0},
                "sfx": {"status": "not_required"},
                "music": {"status": "not_required"},
            },
        },
    )
    return path


def test_generate_image_blocked_without_approval(tmp_path: Path):
    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    provider = MockImageProvider()
    with pytest.raises(CostGateError):
        generate_image(
            project_dir=project_dir,
            shot_path=shot_path,
            provider=provider,
            prompt="a girl in a corridor",
            model="nano-banana",
            reference_paths=[],
            output_path=project_dir / "04_storyboard" / "S01_SH01.png",
            provider_name="mock",
        )
    assert provider._submit_calls == 0


def test_generate_image_succeeds_once_approved(tmp_path: Path):
    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)

    stage = generate_image(
        project_dir=project_dir,
        shot_path=shot_path,
        provider=MockImageProvider(),
        prompt="a girl in a corridor",
        model="nano-banana",
        reference_paths=[],
        output_path=project_dir / "04_storyboard" / "S01_SH01.png",
        provider_name="mock",
    )

    assert stage["status"] == "completed"
    assert stage["attempts"] == 1
    # artifact.path is stored project-relative (see generation_service._project_relative_path)
    assert stage["artifact"]["path"] == "04_storyboard/S01_SH01.png"
    assert (project_dir / stage["artifact"]["path"]).exists()

    shot = load_shot(shot_path)
    assert shot["generation"]["image"]["status"] == "completed"
    # video/voice are still "pending" and continuity is "passed", so the derived
    # top-level status is "ready", not "completed" — see Task 5's compute_status.
    assert shot["status"] == "ready"


def test_generate_image_is_idempotent_by_default(tmp_path: Path):
    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)
    provider = MockImageProvider()

    kwargs = dict(
        project_dir=project_dir,
        shot_path=shot_path,
        provider=provider,
        prompt="a girl in a corridor",
        model="nano-banana",
        reference_paths=[],
        output_path=project_dir / "04_storyboard" / "S01_SH01.png",
        provider_name="mock",
    )
    generate_image(**kwargs)
    generate_image(**kwargs)  # second call should skip, not resubmit
    assert provider._submit_calls == 1


def test_generate_image_force_regenerates(tmp_path: Path):
    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)
    provider = MockImageProvider()

    kwargs = dict(
        project_dir=project_dir,
        shot_path=shot_path,
        provider=provider,
        prompt="a girl in a corridor",
        model="nano-banana",
        reference_paths=[],
        output_path=project_dir / "04_storyboard" / "S01_SH01.png",
        provider_name="mock",
    )
    generate_image(**kwargs)
    generate_image(**{**kwargs, "force": True})
    assert provider._submit_calls == 2


def test_generate_image_writes_attempt_log(tmp_path: Path):
    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)
    generate_image(
        project_dir=project_dir,
        shot_path=shot_path,
        provider=MockImageProvider(),
        prompt="a girl in a corridor",
        model="nano-banana",
        reference_paths=[],
        output_path=project_dir / "04_storyboard" / "S01_SH01.png",
        provider_name="mock",
    )
    logs = list((project_dir / "99_logs" / "S01_SH01").glob("*_image_attempt01.json"))
    assert len(logs) == 1


def test_generate_image_marks_stage_failed_after_exhausted_retries(tmp_path: Path):
    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)
    from ai_film.errors import ProviderError

    with pytest.raises(ProviderError):
        generate_image(
            project_dir=project_dir,
            shot_path=shot_path,
            provider=MockImageProvider(fail_first_n_submits=10),
            prompt="a girl in a corridor",
            model="nano-banana",
            reference_paths=[],
            output_path=project_dir / "04_storyboard" / "S01_SH01.png",
            provider_name="mock",
            max_attempts=2,
        )
    shot = load_shot(shot_path)
    assert shot["generation"]["image"]["status"] == "failed"
    assert shot["status"] == "failed"


def test_generate_video_first_completion_has_version_one_and_no_history(tmp_path: Path):
    from ai_film.providers.mock.video import MockVideoProvider
    from ai_film.services.generation_service import generate_video

    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)

    stage = generate_video(
        project_dir=project_dir, shot_path=shot_path, provider=MockVideoProvider(),
        prompt="a corridor", model="veo-3", reference_paths=[], duration_seconds=5.0,
        output_path=project_dir / "05_video" / "S01_SH01.mp4", provider_name="mock",
    )
    assert stage["version"] == 1
    assert stage["history"] == []


def test_generate_video_force_regenerate_archives_old_artifact_and_bumps_version(tmp_path: Path):
    from ai_film.providers.mock.video import MockVideoProvider
    from ai_film.services.generation_service import generate_video

    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)

    kwargs = dict(
        project_dir=project_dir, shot_path=shot_path, prompt="a corridor",
        model="veo-3", reference_paths=[], duration_seconds=5.0,
        output_path=project_dir / "05_video" / "S01_SH01.mp4", provider_name="mock",
    )
    first = generate_video(provider=MockVideoProvider(), **kwargs)
    first_path = project_dir / first["artifact"]["path"]
    first_path.write_bytes(b"FIRST-VERSION-BYTES")  # distinguish from the mock's fixed bytes

    second = generate_video(provider=MockVideoProvider(), force=True, **kwargs)

    assert second["version"] == 2
    assert len(second["history"]) == 1
    archived = second["history"][0]
    assert archived["version"] == 1
    assert archived["superseded_reason"] == "regenerate"
    archived_path = project_dir / archived["artifact"]["path"]
    assert archived_path.exists()
    assert archived_path.read_bytes() == b"FIRST-VERSION-BYTES"
    new_path = project_dir / second["artifact"]["path"]
    assert new_path.read_bytes() != b"FIRST-VERSION-BYTES"


def test_generate_video_failed_regeneration_restores_prior_artifact(tmp_path: Path):
    from ai_film.errors import ProviderError
    from ai_film.providers.mock.video import MockVideoProvider
    from ai_film.services.generation_service import generate_video

    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)

    kwargs = dict(
        project_dir=project_dir, shot_path=shot_path, prompt="a corridor",
        model="veo-3", reference_paths=[], duration_seconds=5.0,
        output_path=project_dir / "05_video" / "S01_SH01.mp4", provider_name="mock",
    )
    first = generate_video(provider=MockVideoProvider(), **kwargs)
    original_path = project_dir / first["artifact"]["path"]
    original_bytes = original_path.read_bytes()

    with pytest.raises(ProviderError):
        generate_video(
            provider=MockVideoProvider(fail_first_n_submits=10), force=True,
            max_attempts=1, **kwargs,
        )

    # a failed regeneration attempt must not leave the shot's working
    # artifact archived away or missing
    assert original_path.exists()
    assert original_path.read_bytes() == original_bytes
    shot = load_shot(shot_path)
    assert shot["generation"]["video"]["status"] == "failed"


def test_generate_video_preserves_version_across_a_failed_then_successful_regeneration(tmp_path: Path):
    from ai_film.providers.mock.video import MockVideoProvider
    from ai_film.services.generation_service import generate_video
    from ai_film.errors import ProviderError

    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)

    kwargs = dict(
        project_dir=project_dir, shot_path=shot_path, prompt="a corridor",
        model="veo-3", reference_paths=[], duration_seconds=5.0,
        output_path=project_dir / "05_video" / "S01_SH01.mp4", provider_name="mock",
    )
    generate_video(provider=MockVideoProvider(), **kwargs)
    second = generate_video(provider=MockVideoProvider(), force=True, **kwargs)
    assert second["version"] == 2
    second_path = project_dir / second["artifact"]["path"]
    second_path.write_bytes(b"GOOD-V2-BYTES")

    with pytest.raises(ProviderError):
        generate_video(
            provider=MockVideoProvider(fail_first_n_submits=10), force=True,
            max_attempts=1, **kwargs,
        )

    third = generate_video(provider=MockVideoProvider(), force=True, **kwargs)

    assert third["version"] == 3, "version must not reset to 1 after a failed-then-retried regeneration"
    versions_in_history = [h["version"] for h in third["history"]]
    assert versions_in_history == [1, 2], "v2 must be archived, not silently overwritten"
    v2_history_entry = next(h for h in third["history"] if h["version"] == 2)
    v2_archived_path = project_dir / v2_history_entry["artifact"]["path"]
    assert v2_archived_path.read_bytes() == b"GOOD-V2-BYTES", "the v2 artifact must survive, not be destroyed"


def _synthesize_silence(path: Path, seconds: float) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
            "-t", str(seconds), str(path),
        ],
        check=True, capture_output=True,
    )


def _probe_duration(path: Path) -> float:
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True, text=True,
    )
    return float(probe.stdout.strip())


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_pad_audio_if_too_short_pads_a_clip_under_the_minimum(tmp_path: Path):
    short_clip = tmp_path / "short.wav"
    _synthesize_silence(short_clip, 0.72)  # matches the real "I know." clip length found in testing
    assert _probe_duration(short_clip) < _MIN_LIPSYNC_AUDIO_SECONDS

    _pad_audio_if_too_short(short_clip)

    assert _probe_duration(short_clip) >= _MIN_LIPSYNC_AUDIO_SECONDS


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_pad_audio_if_too_short_leaves_a_long_enough_clip_untouched(tmp_path: Path):
    long_clip = tmp_path / "long.wav"
    _synthesize_silence(long_clip, _MIN_LIPSYNC_AUDIO_SECONDS + 1.0)
    original_bytes = long_clip.read_bytes()

    _pad_audio_if_too_short(long_clip)

    assert long_clip.read_bytes() == original_bytes, "a clip already long enough must not be re-encoded"


def test_pad_audio_if_too_short_is_a_noop_without_ffmpeg(tmp_path: Path, monkeypatch):
    """Best-effort: if ffmpeg/ffprobe aren't available, leave the file
    untouched rather than guessing or raising — the provider's own error
    (if the clip really is too short) surfaces normally instead."""
    monkeypatch.setattr("ai_film.services.generation_service.shutil.which", lambda name: None)
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"NOT-REALLY-AUDIO")

    _pad_audio_if_too_short(clip)  # must not raise

    assert clip.read_bytes() == b"NOT-REALLY-AUDIO"


def _make_real_video(path: Path, width: int, height: int, fps: int = 24, duration: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"testsrc=duration={duration}:size={width}x{height}:rate={fps}",
            str(path),
        ],
        check=True, capture_output=True,
    )


def _make_real_image(path: Path, width: int, height: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:d=1", "-frames:v", "1", str(path)],
        check=True, capture_output=True,
    )


class _RealFileVideoProvider:
    """Writes a real, ffprobe-readable video at a caller-controlled
    resolution/fps, unlike MockVideoProvider (which writes placeholder
    bytes) — needed to exercise format validation against a genuinely
    known actual size."""

    def __init__(self, width: int, height: int, fps: int = 24):
        self.width, self.height, self.fps = width, height, fps
        self._requests: dict[str, object] = {}
        self._n = 0

    def submit(self, request):
        self._n += 1
        job_id = f"real-video-{self._n}"
        self._requests[job_id] = request
        return GenerationJob(provider="test", id=job_id, capability=Capability.VIDEO)

    def poll(self, job):
        return JobStatus.COMPLETED

    def get_result(self, job):
        request = self._requests[job.id]
        output_path = Path(request.output_path)
        _make_real_video(output_path, self.width, self.height, self.fps, request.duration_seconds)
        return VideoGenerationResult(
            artifact_path=str(output_path), size_bytes=output_path.stat().st_size,
            duration_seconds=request.duration_seconds,
        )


class _RealFileImageProvider:
    def __init__(self, width: int, height: int):
        self.width, self.height = width, height
        self._requests: dict[str, object] = {}
        self._n = 0

    def submit(self, request):
        self._n += 1
        job_id = f"real-image-{self._n}"
        self._requests[job_id] = request
        return GenerationJob(provider="test", id=job_id, capability=Capability.IMAGE)

    def poll(self, job):
        return JobStatus.COMPLETED

    def get_result(self, job):
        request = self._requests[job.id]
        output_path = Path(request.output_path)
        _make_real_image(output_path, self.width, self.height)
        return ImageGenerationResult(artifact_path=str(output_path), size_bytes=output_path.stat().st_size)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_generate_video_records_format_metadata_on_close_match(tmp_path: Path):
    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)

    stage = generate_video(
        project_dir=project_dir, shot_path=shot_path,
        provider=_RealFileVideoProvider(width=1344, height=768, fps=24),
        prompt="x", model="h3-max", reference_paths=[],
        duration_seconds=2.0, output_path=project_dir / "05_video" / "S01_SH01.mp4",
        provider_name="fal", target_width=1280, target_height=720, target_fps=24,
    )

    artifact = stage["artifact"]
    assert artifact["requested_format"] == {"width": 1280, "height": 720, "fps": "24.000"}
    assert artifact["actual_format"]["width"] == 1344
    assert artifact["actual_format"]["height"] == 768
    assert "format_mismatch" not in artifact  # 1344x768 is within 2% of 1280x720's aspect ratio


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_generate_video_default_records_mismatch_without_raising(tmp_path: Path):
    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)

    stage = generate_video(
        project_dir=project_dir, shot_path=shot_path,
        provider=_RealFileVideoProvider(width=720, height=1280, fps=24),  # portrait, wrong ratio entirely
        prompt="x", model="hailuo-2.3", reference_paths=[],
        duration_seconds=2.0, output_path=project_dir / "05_video" / "S01_SH01.mp4",
        provider_name="fal", target_width=1280, target_height=720, target_fps=24,
        strict_format=False,
    )

    assert stage["status"] == "completed"
    assert stage["artifact"]["format_mismatch"] is True


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_generate_video_strict_format_raises_and_restores_on_aspect_ratio_mismatch(tmp_path: Path):
    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)

    with pytest.raises(ProviderError):
        generate_video(
            project_dir=project_dir, shot_path=shot_path,
            provider=_RealFileVideoProvider(width=720, height=1280, fps=24),
            prompt="x", model="hailuo-2.3", reference_paths=[],
            duration_seconds=2.0, output_path=project_dir / "05_video" / "S01_SH01.mp4",
            provider_name="fal", target_width=1280, target_height=720, target_fps=24,
            strict_format=True,
        )

    shot = load_shot(shot_path)
    assert shot["generation"]["video"]["status"] == "failed"
    assert shot["generation"]["video"].get("artifact") is None  # never persisted


def test_generate_video_skips_format_validation_for_mock_provider(tmp_path: Path):
    """The mock provider writes placeholder bytes ffprobe can't read —
    validation must never run against it, target_width/height or not."""
    from ai_film.providers.mock.video import MockVideoProvider

    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)

    stage = generate_video(
        project_dir=project_dir, shot_path=shot_path, provider=MockVideoProvider(),
        prompt="x", model="veo-3", reference_paths=[],
        duration_seconds=2.0, output_path=project_dir / "05_video" / "S01_SH01.mp4",
        provider_name="mock", target_width=1280, target_height=720, target_fps=24,
    )

    assert stage["status"] == "completed"
    assert "requested_format" not in stage["artifact"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_generate_image_strict_format_raises_on_aspect_ratio_mismatch(tmp_path: Path):
    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)

    with pytest.raises(ProviderError):
        generate_image(
            project_dir=project_dir, shot_path=shot_path,
            provider=_RealFileImageProvider(width=1080, height=1080),  # square, way off 16:9
            prompt="x", model="nano-banana", reference_paths=[],
            output_path=project_dir / "04_storyboard" / "S01_SH01.png",
            provider_name="fal", target_width=1280, target_height=720, strict_format=True,
        )


def test_generate_video_no_target_skips_validation_entirely(tmp_path: Path):
    """target_width/target_height left at their 0 default (no caller
    resolved a target) — validation is a pure no-op, matching every
    pre-existing generate_video call site until Task 6 lands."""
    from ai_film.providers.mock.video import MockVideoProvider

    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)

    stage = generate_video(
        project_dir=project_dir, shot_path=shot_path, provider=MockVideoProvider(),
        prompt="x", model="veo-3", reference_paths=[],
        duration_seconds=2.0, output_path=project_dir / "05_video" / "S01_SH01.mp4",
        provider_name="mock",
    )
    assert "requested_format" not in stage["artifact"]
