# Render Format Contract & Robust Final Render Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `ai-film-studio` a canonical production format (resolution/fps), declared once at the project level and overridable per shot, that flows through generation (each provider adapter snaps it to its own native params, verified against fal's real schemas) and is enforced unconditionally at final render — so `ai-film render` never again silently drops audio or mishandles mismatched clip resolutions.

**Architecture:** `config.json`'s new `render` section is both the project default (fallback when a shot has no `format`) and the final render's unconditional target. `shot.json` gains an optional `format` override. Provider adapters derive native resolution/aspect_ratio params from the resolved target (mirroring the existing `_snap_duration`/`_duration_field` pattern) and, for models with no such fields, pre-resize the reference image. Post-generation, `ffprobe` validates the actual artifact's aspect ratio against the target (`strict_format` gates only aspect ratio, never exact pixels or fps — no provider can hit those). `render.py` is rewritten from concat-demuxer+`-c copy` to `filter_complex`+re-encode, normalizing every clip's resolution/SAR/fps and audio (sample rate/channel layout/presence) unconditionally.

**Tech Stack:** Python, ffmpeg/ffprobe (subprocess), `fractions.Fraction` for exact fps comparison, pytest, typer.

**Spec:** `docs/superpowers/specs/2026-09-01-render-format-contract-design.md`

## Global Constraints

- Never touch `one-more-life/` (the live project directory) or `.env` while implementing or testing this — both are gitignored and must stay untouched.
- No `shot.json` migration/mutation for legacy shots — `format` stays optional in the schema; generation resolves a missing `format` from `config.json`'s `render` defaults purely at runtime, never writing back to the shot file.
- `strict_format` gates only on aspect-ratio compatibility (2% relative tolerance) — never on exact resolution or exact fps. No fal model in this project's catalog can produce literal target pixel dimensions or a controllable fps (verified against each model's real OpenAPI schema — see the spec's provider capability table).
- `target_fps` exists only on `VideoGenerationRequest`, never on `ImageGenerationRequest` — images have no frame rate.
- Format validation must be skipped entirely when `provider_name == "mock"` — the mock providers write literal placeholder bytes (`b"MOCK-PNG-DATA"`, `b"MOCK-MP4-DATA"`), not real media files, so `ffprobe` cannot and must not be run against them. This also keeps this project's "try it free first, with the mock provider" workflow (README) from requiring ffmpeg.
- Full test suite must stay green after every task (358 passing at the start of this plan; run `./.venv/bin/pytest -q` after each task).
- Match this codebase's established convention of small `ffprobe`-wrapping helpers duplicated per module rather than centralized in a shared utility file (see the existing `_probe_duration`/`_has_audio_stream` pairs already independently defined in `video_diagnostics.py`, `video_fix.py`, and now `render.py`/`generation_service.py`).

---

### Task 1: Config defaults, shot schema, and request dataclass fields

**Files:**
- Modify: `src/ai_film/project.py:16-28` (`DEFAULT_CONFIG`)
- Modify: `src/ai_film/schema.py:28-71` (`SHOT_SCHEMA`)
- Modify: `src/ai_film/models.py:61-69` (`VideoGenerationRequest`), `:39-44` (`ImageGenerationRequest`)
- Test: `tests/test_project.py`, `tests/test_schema.py`, `tests/test_models.py`

**Interfaces:**
- Consumes: nothing (foundational).
- Produces: `DEFAULT_CONFIG["render"] = {"resolution": "1280x720", "fps": 24, "strict_format": False}`; `SHOT_SCHEMA` accepts an optional top-level `format` object (`{"resolution": str, "fps": number}`); `VideoGenerationRequest.target_width: int = 0`, `.target_height: int = 0`, `.target_fps: int = 0`; `ImageGenerationRequest.target_width: int = 0`, `.target_height: int = 0`. `target_width == 0` is the sentinel every later task uses to mean "no target format was resolved, skip format handling entirely."

- [ ] **Step 1: Write the failing tests**

`tests/test_project.py` — find the existing test that asserts on `DEFAULT_CONFIG` or `init_project`'s written `config.json` shape (e.g. a test checking `config["providers"]`) and add:

```python
def test_default_config_includes_render_format_defaults(tmp_path: Path):
    project_dir = init_project(tmp_path / "project", "Test Film")
    config = json.loads((project_dir / "config.json").read_text())
    assert config["render"] == {"resolution": "1280x720", "fps": 24, "strict_format": False}
```

`tests/test_schema.py` — add:

```python
def test_shot_schema_accepts_optional_format_field():
    shot = _valid_shot()  # use this file's existing minimal-valid-shot helper/fixture
    shot["format"] = {"resolution": "1280x720", "fps": 24}
    assert validate_shot(shot) == []


def test_shot_schema_accepts_missing_format_field():
    shot = _valid_shot()
    assert "format" not in shot
    assert validate_shot(shot) == []
```

(If this test file has no existing minimal-valid-shot helper, build the dict inline instead, copying the exact shape any other passing test in this file already uses for a schema-valid shot — do not invent new required fields.)

`tests/test_models.py` — add:

```python
def test_video_generation_request_target_format_fields_default_to_zero():
    request = VideoGenerationRequest(prompt="x", model="veo-3")
    assert request.target_width == 0
    assert request.target_height == 0
    assert request.target_fps == 0


def test_image_generation_request_target_format_fields_default_to_zero():
    request = ImageGenerationRequest(prompt="x", model="nano-banana")
    assert request.target_width == 0
    assert request.target_height == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_project.py tests/test_schema.py tests/test_models.py -v`
Expected: the three new tests FAIL (`KeyError: 'render'`, schema/attribute errors) — everything else still passes.

- [ ] **Step 3: Implement**

`src/ai_film/project.py` — change:

```python
DEFAULT_CONFIG = {
    "providers": {
        "image": {"provider": "fal", "model": "nano-banana", "parameters": {}},
        "video": {"provider": "fal", "model": "veo-3", "parameters": {}},
        "voice": {"provider": "fal", "model": "csm-1b", "parameters": {}},
        "sfx": {"provider": "fal", "model": "thinksound", "parameters": {}},
        "music": {"provider": "fal", "model": "cassetteai-music", "parameters": {}},
        "lipsync": {"provider": "fal", "model": "kling-lipsync", "parameters": {}},
    },
    "generation": {"max_attempts": 3, "max_parallel_jobs": 3, "poll_interval_seconds": 5},
    "render": {"resolution": "1280x720", "fps": 24, "strict_format": False},
    "generation_approval": {},
}
```

(Only the `"render": {}` line changes, to `"render": {"resolution": "1280x720", "fps": 24, "strict_format": False}`.)

`src/ai_film/schema.py` — add a `"format"` property to `SHOT_SCHEMA["properties"]` (do not add `"format"` to the top-level `"required"` list — it must stay optional):

```python
"format": {
    "type": "object",
    "properties": {
        "resolution": {"type": "string"},
        "fps": {"type": "number"},
    },
},
```

`src/ai_film/models.py` — change `VideoGenerationRequest`:

```python
@dataclass
class VideoGenerationRequest:
    prompt: str
    model: str
    reference_paths: list[str] = field(default_factory=list)
    duration_seconds: float = 5.0
    output_path: str = ""
    suppress_captions: bool = True
    end_reference_path: str = ""
    target_width: int = 0
    target_height: int = 0
    target_fps: int = 0
```

and `ImageGenerationRequest`:

```python
@dataclass
class ImageGenerationRequest:
    prompt: str
    model: str
    num_candidates: int = 1
    reference_paths: list[str] = field(default_factory=list)
    output_path: str = ""
    target_width: int = 0
    target_height: int = 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_project.py tests/test_schema.py tests/test_models.py -v`
Expected: PASS, all of them.

- [ ] **Step 5: Run the full suite**

Run: `./.venv/bin/pytest -q`
Expected: 363 passed (358 + 5 new: 1 project test, 2 schema tests, 2 models tests), 0 failed.

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/project.py src/ai_film/schema.py src/ai_film/models.py \
  tests/test_project.py tests/test_schema.py tests/test_models.py
git commit -m "feat: add render format config defaults, optional shot format field, target format request fields"
```

---

### Task 2: `cli.py` target-format resolution helpers

**Files:**
- Modify: `src/ai_film/cli.py` (add helpers near `_stage_config`, `src/ai_film/cli.py:136-138`)
- Test: `tests/test_cli_generation_commands.py`

**Interfaces:**
- Consumes: `config.json`'s `render` section (Task 1); `shot.json`'s optional `format` field (Task 1).
- Produces: `_parse_resolution(resolution: str) -> tuple[int, int]`; `_render_config(path: Path) -> dict`; `_resolve_target_format(path: Path, shot_data: dict) -> tuple[int, int, int]` (returns `(width, height, fps)`); `_strict_format(path: Path) -> bool`. Task 6 wires these into `generate_video_cmd`/`generate_image_cmd`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli_generation_commands.py` (uses this file's existing `_init_mock_project` helper):

```python
from ai_film.cli import _parse_resolution, _resolve_target_format, _strict_format


def test_parse_resolution_splits_width_and_height():
    assert _parse_resolution("1280x720") == (1280, 720)
    assert _parse_resolution("1920x1080") == (1920, 1080)


def test_resolve_target_format_uses_shot_format_when_present(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    shot_data = _shot("S01_SH01")
    shot_data["format"] = {"resolution": "1920x1080", "fps": 30}
    assert _resolve_target_format(project_dir, shot_data) == (1920, 1080, 30)


def test_resolve_target_format_falls_back_to_render_config(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    config = json.loads((project_dir / "config.json").read_text())
    config["render"] = {"resolution": "1408x768", "fps": 25, "strict_format": False}
    (project_dir / "config.json").write_text(json.dumps(config))
    shot_data = _shot("S01_SH01")  # no format field
    assert _resolve_target_format(project_dir, shot_data) == (1408, 768, 25)


def test_resolve_target_format_falls_back_to_hardcoded_default_when_render_empty(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    config = json.loads((project_dir / "config.json").read_text())
    config["render"] = {}
    (project_dir / "config.json").write_text(json.dumps(config))
    shot_data = _shot("S01_SH01")
    assert _resolve_target_format(project_dir, shot_data) == (1280, 720, 24)


def test_strict_format_reads_render_config(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    assert _strict_format(project_dir) is False  # _init_mock_project's default config.json
    config = json.loads((project_dir / "config.json").read_text())
    config["render"]["strict_format"] = True
    (project_dir / "config.json").write_text(json.dumps(config))
    assert _strict_format(project_dir) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_cli_generation_commands.py -v -k "parse_resolution or resolve_target_format or strict_format"`
Expected: FAIL with `ImportError: cannot import name '_parse_resolution'`.

- [ ] **Step 3: Implement**

Add to `src/ai_film/cli.py`, immediately after `_stage_config` (`src/ai_film/cli.py:136-138`):

```python
_DEFAULT_TARGET_RESOLUTION = (1280, 720)
_DEFAULT_TARGET_FPS = 24


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_cli_generation_commands.py -v -k "parse_resolution or resolve_target_format or strict_format"`
Expected: PASS, all 5.

- [ ] **Step 5: Run the full suite**

Run: `./.venv/bin/pytest -q`
Expected: 368 passed (363 + 5 new), 0 failed.

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/cli.py tests/test_cli_generation_commands.py
git commit -m "feat: add cli target-format resolution helpers (shot override, config default, hardcoded fallback)"
```

---

### Task 3: `generation_service.py` post-generation format validation

**Files:**
- Modify: `src/ai_film/services/generation_service.py` (imports at top; `run_generation_stage` at `src/ai_film/services/generation_service.py:151-173`; `generate_video` at `:263-293`; `generate_image` at `:234-260`)
- Test: `tests/services/test_generation_service.py`

**Interfaces:**
- Consumes: `VideoGenerationRequest.target_width/target_height/target_fps`, `ImageGenerationRequest.target_width/target_height` (Task 1).
- Produces: `generate_video(..., target_width: int = 0, target_height: int = 0, target_fps: int = 0, strict_format: bool = False)`; `generate_image(..., target_width: int = 0, target_height: int = 0, strict_format: bool = False)`. Both, when a real target is given (`target_width and target_height` both truthy) AND `provider_name != "mock"`, add `requested_format`/`actual_format` (and `format_mismatch: true` on a real mismatch) to the returned artifact dict. Task 6 threads `target_width`/`target_height`/`target_fps`/`strict_format` into these from `cli.py`.

- [ ] **Step 1: Write the failing tests**

First, add a real-file test double to `tests/services/test_generation_service.py` — the shared `MockVideoProvider`/`MockImageProvider` write placeholder bytes (`b"MOCK-MP4-DATA"`), not real media, so validation needs a provider that writes actual `ffprobe`-readable files at controlled dimensions:

```python
import shutil

from ai_film.errors import ProviderError
from ai_film.models import (
    Capability, GenerationJob, ImageGenerationResult, JobStatus, VideoGenerationResult,
)
from ai_film.services.generation_service import generate_image, generate_video


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
```

Then the actual test cases:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/services/test_generation_service.py -v -k "format"`
Expected: FAIL — `generate_video`/`generate_image` raise `TypeError: unexpected keyword argument 'target_width'`.

- [ ] **Step 3: Implement**

Add `from fractions import Fraction` to the imports at the top of `src/ai_film/services/generation_service.py`.

Modify `run_generation_stage` (`src/ai_film/services/generation_service.py:151-173`) — move `artifact = result_to_artifact(job_result.result)` inside the `try` block, so a `ProviderError` raised during artifact-building (i.e. by strict-format validation) is handled identically to a job failure — archive restored, stage marked `failed`, exception re-raised:

```python
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
```

(The line `artifact = result_to_artifact(job_result.result)` that used to sit just after the `try`/`except` block is deleted — it now lives inside the `try`.)

Add these module-level helpers, right after `_lipsync_video_artifact` (`src/ai_film/services/generation_service.py:220-231`) and before `generate_image`:

```python
_ASPECT_RATIO_TOLERANCE = 0.02


def _probe_resolution(path: Path) -> tuple[int, int]:
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path),
        ],
        capture_output=True, text=True,
    )
    width_str, height_str = probe.stdout.strip().split("x")
    return int(width_str), int(height_str)


def _probe_fps(path: Path) -> Fraction:
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate", "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True, text=True,
    )
    return Fraction(probe.stdout.strip())


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
```

Modify `generate_image` (`src/ai_film/services/generation_service.py:234-260`):

```python
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
```

Modify `generate_video` (`src/ai_film/services/generation_service.py:263-293`):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/services/test_generation_service.py -v -k "format"`
Expected: PASS, all 6 new tests. Also re-run the whole file to confirm no regression on the pre-existing tests that exercise `run_generation_stage`'s failure path:

Run: `./.venv/bin/pytest tests/services/test_generation_service.py -v`
Expected: all PASS, including `test_generate_video_failed_regeneration_restores_prior_artifact` and `test_generate_image_marks_stage_failed_after_exhausted_retries` (these confirm the `try`-block change in Step 3 didn't alter the existing job-failure path).

- [ ] **Step 5: Run the full suite**

Run: `./.venv/bin/pytest -q`
Expected: 374 passed (368 + 6 new), 0 failed.

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/services/generation_service.py tests/services/test_generation_service.py
git commit -m "feat: validate generated video/image aspect ratio against target format, gate strict mode on ratio only"
```

---

### Task 4: `providers/fal/video.py` provider adapter

**Files:**
- Modify: `src/ai_film/providers/fal/video.py`
- Test: `tests/providers/test_fal_providers.py`

**Interfaces:**
- Consumes: `VideoGenerationRequest.target_width/target_height` (Task 1).
- Produces: `_nearest_aspect_ratio_enum(width: int, height: int, allowed: set[str]) -> str`; `_video_format_fields(model: str, width: int, height: int) -> dict`; `_resize_reference_for_target(image_path: Path, width: int, height: int) -> Path`; `MODELS_REQUIRING_RESIZED_REFERENCE = {"h3-max", "hailuo-2.3", "hailuo-2.3-fast"}`. `submit()` sends these native fields and, for the models in that set, uploads a resized reference image instead of the raw one whenever a target was resolved.

- [ ] **Step 1: Write the failing tests**

Add to `tests/providers/test_fal_providers.py`:

```python
from ai_film.providers.fal.video import (
    MODELS_REQUIRING_RESIZED_REFERENCE, _nearest_aspect_ratio_enum, _resize_reference_for_target,
    _video_format_fields,
)


def test_nearest_aspect_ratio_enum_picks_closest_by_ratio_distance():
    assert _nearest_aspect_ratio_enum(1280, 720, {"16:9", "9:16"}) == "16:9"
    assert _nearest_aspect_ratio_enum(720, 1280, {"16:9", "9:16"}) == "9:16"


def test_video_format_fields_for_veo3_sends_resolution_and_aspect_ratio():
    fields = _video_format_fields("veo-3", 1280, 720)
    assert fields == {"resolution": "720p", "aspect_ratio": "16:9"}


def test_video_format_fields_for_veo3_picks_1080p_tier():
    fields = _video_format_fields("veo-3", 1920, 1080)
    assert fields["resolution"] == "1080p"


def test_video_format_fields_for_h3_max_sends_resolution_only():
    fields = _video_format_fields("h3-max", 1280, 720)
    assert fields == {"resolution": "768P"}


def test_video_format_fields_for_hailuo_is_empty_no_native_fields_exist():
    assert _video_format_fields("hailuo-2.3", 1280, 720) == {}
    assert _video_format_fields("hailuo-2.3-fast", 1280, 720) == {}


def test_models_requiring_resized_reference_is_h3_max_and_both_hailuo_models():
    assert MODELS_REQUIRING_RESIZED_REFERENCE == {"h3-max", "hailuo-2.3", "hailuo-2.3-fast"}


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_resize_reference_for_target_produces_exact_target_dimensions(tmp_path: Path):
    source = tmp_path / "source.png"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=500x500:d=1", "-frames:v", "1", str(source)],
        check=True, capture_output=True,
    )

    output = _resize_reference_for_target(source, 1280, 720)

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(output),
        ],
        capture_output=True, text=True,
    )
    assert probe.stdout.strip() == "1280x720"


@patch("ai_film.providers.fal.client.requests")
def test_hailuo_uploads_resized_reference_when_target_is_set(mock_requests, tmp_path: Path, monkeypatch):
    """hailuo has no aspect_ratio/resolution field at all (verified against
    its OpenAPI schema) — the resized reference image is its only lever,
    so submit() must upload the RESIZED file, not the raw one, whenever a
    target was resolved."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    _mock_submit_response(mock_requests)
    reference = tmp_path / "ref.png"
    reference.write_bytes(b"REF-PNG")
    uploaded_paths = []
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file",
        lambda path: uploaded_paths.append(path) or "https://cdn.fal.run/uploaded.png",
    )
    monkeypatch.setattr(
        "ai_film.providers.fal.video._resize_reference_for_target",
        lambda image_path, width, height: Path(tmp_path / "resized.png"),
    )

    provider = FalVideoProvider()
    provider.submit(
        VideoGenerationRequest(
            prompt="a girl walks", model="hailuo-2.3", reference_paths=[str(reference)],
            duration_seconds=6, output_path=str(tmp_path / "out.mp4"),
            target_width=1280, target_height=720,
        )
    )

    assert uploaded_paths == [str(tmp_path / "resized.png")]


@patch("ai_film.providers.fal.client.requests")
def test_veo3_uploads_raw_reference_never_resized(mock_requests, tmp_path: Path, monkeypatch):
    """veo-3 has its own aspect_ratio field (verified against its OpenAPI
    schema) — it must never go through the reference-resize path."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    _mock_submit_response(mock_requests)
    reference = tmp_path / "ref.png"
    reference.write_bytes(b"REF-PNG")
    uploaded_paths = []
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file",
        lambda path: uploaded_paths.append(path) or "https://cdn.fal.run/uploaded.png",
    )

    provider = FalVideoProvider()
    provider.submit(
        VideoGenerationRequest(
            prompt="a girl walks", model="veo-3", reference_paths=[str(reference)],
            duration_seconds=6, output_path=str(tmp_path / "out.mp4"),
            target_width=1280, target_height=720,
        )
    )

    assert uploaded_paths == [str(reference)]
    sent_input = mock_requests.post.call_args.kwargs["json"]
    assert sent_input["resolution"] == "720p"
    assert sent_input["aspect_ratio"] == "16:9"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/providers/test_fal_providers.py -v -k "format or resize or aspect_ratio or models_requiring"`
Expected: FAIL — `ImportError: cannot import name '_video_format_fields'`.

- [ ] **Step 3: Implement**

Add `import shutil`, `import subprocess`, `import tempfile` and `from pathlib import Path` to the top of `src/ai_film/providers/fal/video.py` (currently only imports `from __future__ import annotations` and the two `ai_film` modules).

Add these constants right after `MODELS_WITH_END_IMAGE_URL` (`src/ai_film/providers/fal/video.py:69`):

```python
# Verified against fal's real OpenAPI schemas — see the design spec's
# provider capability table. veo-3 has both resolution (720p/1080p) and
# aspect_ratio (auto/16:9/9:16) fields; h3-max has only resolution
# (480P/768P), its aspect ratio "follows image_url" per its own docs;
# hailuo has neither field at all.
_VEO3_RESOLUTION_TIERS = {720: "720p", 1080: "1080p"}
_VEO3_ASPECT_RATIOS = {"16:9", "9:16"}
_H3_MAX_RESOLUTION_TIERS = {480: "480P", 768: "768P"}

# Models whose output aspect ratio is controlled entirely by the input
# reference image, not a request field — the target aspect ratio can only
# reach the provider by resizing the reference image itself before
# upload. veo-3 is excluded: its own aspect_ratio field already covers it.
MODELS_REQUIRING_RESIZED_REFERENCE = {"h3-max", "hailuo-2.3", "hailuo-2.3-fast"}


def _nearest_aspect_ratio_enum(width: int, height: int, allowed: set[str]) -> str:
    """Reduce width:height to a ratio and pick the closest allowed enum
    value by numeric ratio distance — same snap-to-nearest idea
    _snap_duration already uses for clip length."""
    target_ratio = width / height

    def _ratio_value(enum: str) -> float:
        w_str, h_str = enum.split(":")
        return int(w_str) / int(h_str)

    return min(allowed, key=lambda enum: abs(_ratio_value(enum) - target_ratio))


def _video_format_fields(model: str, width: int, height: int) -> dict:
    """Native resolution/aspect_ratio fields for a model's request body,
    derived from the canonical target. Empty dict for models with no such
    fields at all (the hailuo family), which rely entirely on
    _resize_reference_for_target instead."""
    if model == "veo-3":
        tier = min(_VEO3_RESOLUTION_TIERS, key=lambda t: abs(t - height))
        return {
            "resolution": _VEO3_RESOLUTION_TIERS[tier],
            "aspect_ratio": _nearest_aspect_ratio_enum(width, height, _VEO3_ASPECT_RATIOS),
        }
    if model == "h3-max":
        tier = min(_H3_MAX_RESOLUTION_TIERS, key=lambda t: abs(t - height))
        return {"resolution": _H3_MAX_RESOLUTION_TIERS[tier]}
    return {}


def _resize_reference_for_target(image_path: Path, width: int, height: int) -> Path:
    """Resize/pad image_path to exactly width x height via the same
    scale+pad technique render.py uses for clip normalization, writing a
    throwaway temp file. For h3-max/hailuo, whose output aspect ratio
    follows the reference image, this is how the target aspect ratio
    actually reaches the provider — the exact pixel size is incidental
    (the simplest way to produce a well-formed image carrying the right
    ratio), not a promise the provider is expected to reproduce. See the
    design spec's Section 3 note."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not installed or not on PATH")
    tmp_dir = Path(tempfile.mkdtemp(prefix="ai-film-refsize-"))
    output_path = tmp_dir / f"resized_{image_path.name}"
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(image_path),
            "-vf", (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
            ),
            str(output_path),
        ],
        check=True, capture_output=True,
    )
    return output_path
```

Modify `submit()` (`src/ai_film/providers/fal/video.py:104-128`):

```python
    def submit(self, request: VideoGenerationRequest) -> GenerationJob:
        prompt = request.prompt
        if request.suppress_captions:
            prompt += _CAPTION_SUPPRESSION_CLAUSE
        input_data = {"prompt": prompt}
        if request.suppress_captions and request.model in MODELS_WITH_NEGATIVE_PROMPT:
            input_data["negative_prompt"] = "on-screen text, captions, subtitles"
        if request.model not in _NO_DURATION_FIELD_MODELS:
            input_data["duration"] = _duration_field(request.model, request.duration_seconds)
        if request.model == "h3-max":
            input_data["prompt_expansion_mode"] = "balanced"
        if request.model in MODELS_WITH_AUTO_AUDIO:
            input_data["generate_audio"] = False
        has_target = bool(request.target_width and request.target_height)
        if has_target:
            input_data.update(
                _video_format_fields(request.model, request.target_width, request.target_height)
            )
        if request.reference_paths:
            app_id = MODEL_TO_IMAGE_TO_VIDEO_APP_ID.get(
                request.model, MODEL_TO_APP_ID[request.model]
            )
            reference_path = request.reference_paths[0]
            if has_target and request.model in MODELS_REQUIRING_RESIZED_REFERENCE:
                reference_path = str(
                    _resize_reference_for_target(
                        Path(reference_path), request.target_width, request.target_height
                    )
                )
            input_data["image_url"] = client.upload_file(reference_path)
            if request.end_reference_path and request.model in MODELS_WITH_END_IMAGE_URL:
                input_data["end_image_url"] = client.upload_file(request.end_reference_path)
        else:
            app_id = MODEL_TO_APP_ID[request.model]
        job, status_url, response_url = client.submit(app_id, input_data, Capability.VIDEO)
        self._jobs[job.id] = (status_url, response_url, request)
        return job
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/providers/test_fal_providers.py -v -k "format or resize or aspect_ratio or models_requiring or veo3_uploads or hailuo_uploads"`
Expected: PASS, all 9 new tests.

- [ ] **Step 5: Run the full suite**

Run: `./.venv/bin/pytest -q`
Expected: 383 passed (374 + 9 new), 0 failed. (Confirms the existing veo-3/hailuo/h3-max provider tests from earlier sessions — which construct requests with `target_width`/`target_height` left at their `0` default — are unaffected, since `has_target` is `False` for them.)

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/providers/fal/video.py tests/providers/test_fal_providers.py
git commit -m "feat: derive native video-model resolution/aspect_ratio params from target format"
```

---

### Task 5: `providers/fal/image.py` provider adapter

**Files:**
- Modify: `src/ai_film/providers/fal/image.py`
- Test: `tests/providers/test_fal_providers.py`

**Interfaces:**
- Consumes: `ImageGenerationRequest.target_width/target_height` (Task 1).
- Produces: `_image_format_fields(model: str, width: int, height: int) -> dict`. `submit()` sends these native fields whenever a target was resolved.

- [ ] **Step 1: Write the failing tests**

Add to `tests/providers/test_fal_providers.py`:

```python
from ai_film.providers.fal.image import _image_format_fields


def test_image_format_fields_for_nano_banana_sends_aspect_ratio_and_quality_tier():
    fields = _image_format_fields("nano-banana", 1280, 720)
    assert fields == {"aspect_ratio": "16:9", "resolution": "1K"}


def test_image_format_fields_for_nano_banana_pro_has_no_half_k_tier():
    # nano-banana-pro's real schema has no "0.5K" option (verified against
    # its OpenAPI schema) — a small target must still snap to its lowest
    # real tier, "1K", not an invalid "0.5K".
    fields = _image_format_fields("nano-banana-pro", 100, 100)
    assert fields["resolution"] == "1K"


def test_image_format_fields_for_unknown_model_is_empty():
    assert _image_format_fields("some-future-model", 1280, 720) == {}


@patch("ai_film.providers.fal.client.requests")
def test_image_provider_sends_format_fields_on_edit_call(mock_requests, tmp_path: Path, monkeypatch):
    """nano-banana-2/edit's real schema also has aspect_ratio + resolution
    (verified) — the reference-conditioned /edit path must get them too,
    not just the base text-to-image path."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    _mock_submit_response(mock_requests)
    reference = tmp_path / "ref.png"
    reference.write_bytes(b"REF-PNG")
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file", lambda path: "https://cdn.fal.run/ref.png"
    )

    provider = FalImageProvider()
    provider.submit(
        ImageGenerationRequest(
            prompt="a girl", model="nano-banana", reference_paths=[str(reference)],
            output_path=str(tmp_path / "out.png"), target_width=1280, target_height=720,
        )
    )

    sent_input = mock_requests.post.call_args.kwargs["json"]
    assert sent_input["aspect_ratio"] == "16:9"
    assert sent_input["resolution"] == "1K"
```

(This file's existing imports already include `FalImageProvider`, `ImageGenerationRequest`, `_mock_submit_response`, and `patch` — reuse them, don't re-import.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/providers/test_fal_providers.py -v -k "image_format_fields or sends_format_fields_on_edit"`
Expected: FAIL — `ImportError: cannot import name '_image_format_fields'`.

- [ ] **Step 3: Implement**

Add to `src/ai_film/providers/fal/image.py`, right after `MODEL_TO_EDIT_APP_ID`/`EDIT_APP_ID` (`src/ai_film/providers/fal/image.py:25-30`):

```python
# Verified against fal's real OpenAPI schemas for fal-ai/nano-banana-2 and
# fal-ai/nano-banana-pro (and their /edit variants, which share the same
# aspect_ratio/resolution fields): "resolution" is a coarse quality tier
# (long-side pixel count), never an exact width/height. nano-banana-pro's
# schema has no "0.5K" option — its own smallest real tier is "1K".
_IMAGE_QUALITY_TIERS = {
    "nano-banana": {512: "0.5K", 1024: "1K", 2048: "2K", 4096: "4K"},
    "nano-banana-pro": {1024: "1K", 2048: "2K", 4096: "4K"},
}
_IMAGE_ASPECT_RATIOS = {"21:9", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4", "2:3", "9:16"}


def _nearest_aspect_ratio_enum(width: int, height: int, allowed: set[str]) -> str:
    """Duplicated from providers/fal/video.py's helper of the same name —
    this codebase duplicates small helpers per module rather than sharing
    a utils file (see _probe_duration/_has_audio_stream elsewhere)."""
    target_ratio = width / height

    def _ratio_value(enum: str) -> float:
        w_str, h_str = enum.split(":")
        return int(w_str) / int(h_str)

    return min(allowed, key=lambda enum: abs(_ratio_value(enum) - target_ratio))


def _image_format_fields(model: str, width: int, height: int) -> dict:
    """Native aspect_ratio + resolution (quality-tier) fields for a
    model's request body, derived from the canonical target. Empty dict
    for a model with no known quality-tier mapping."""
    tiers = _IMAGE_QUALITY_TIERS.get(model)
    if tiers is None:
        return {}
    long_side = max(width, height)
    tier = min(tiers, key=lambda t: abs(t - long_side))
    return {
        "aspect_ratio": _nearest_aspect_ratio_enum(width, height, _IMAGE_ASPECT_RATIOS),
        "resolution": tiers[tier],
    }
```

Modify `submit()` (`src/ai_film/providers/fal/image.py:38-47`):

```python
    def submit(self, request: ImageGenerationRequest) -> GenerationJob:
        input_data = {"prompt": request.prompt, "num_images": request.num_candidates}
        if request.target_width and request.target_height:
            input_data.update(
                _image_format_fields(request.model, request.target_width, request.target_height)
            )
        if request.reference_paths:
            input_data["image_urls"] = [client.upload_file(p) for p in request.reference_paths]
            app_id = MODEL_TO_EDIT_APP_ID.get(request.model, MODEL_TO_APP_ID[request.model])
        else:
            app_id = MODEL_TO_APP_ID[request.model]
        job, status_url, response_url = client.submit(app_id, input_data, Capability.IMAGE)
        self._jobs[job.id] = (status_url, response_url, request)
        return job
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/providers/test_fal_providers.py -v -k "image_format_fields or sends_format_fields_on_edit"`
Expected: PASS, all 4 new tests.

- [ ] **Step 5: Run the full suite**

Run: `./.venv/bin/pytest -q`
Expected: 387 passed (383 + 4 new), 0 failed.

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/providers/fal/image.py tests/providers/test_fal_providers.py
git commit -m "feat: derive nano-banana aspect_ratio/resolution params from target format"
```

---

### Task 6: `cli.py` command wiring

**Files:**
- Modify: `src/ai_film/cli.py:141-153` (`_run_generation`), `:298-320` (`generate_image_cmd`), `:387-424` (`generate_video_cmd`)
- Test: `tests/test_cli_generation_commands.py`

**Interfaces:**
- Consumes: `_resolve_target_format`/`_strict_format` (Task 2); `generate_video`/`generate_image`'s `target_width`/`target_height`/`target_fps`/`strict_format` params (Task 3).
- Produces: `generate-image`/`generate-video` CLI commands now resolve and pass the shot's target format on every call; `_run_generation` prints a warning line when the returned artifact carries `format_mismatch: true`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli_generation_commands.py` (reuses this file's existing `_RecordingVideoProvider`/`_RecordingImageProvider`, `_init_mock_project`, `_approve`):

```python
def test_generate_video_passes_resolved_target_format_to_provider(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    _approve(project_dir)
    provider = _RecordingVideoProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-video", "--shot", "S01_SH01", "--path", str(project_dir)])

    assert result.exit_code == 0, result.output
    request = provider.requests[-1]
    assert request.target_width == 1280
    assert request.target_height == 720
    assert request.target_fps == 24


def test_generate_video_uses_shot_format_override(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["format"] = {"resolution": "1920x1080", "fps": 30}
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)
    _approve(project_dir)
    provider = _RecordingVideoProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-video", "--shot", "S01_SH01", "--path", str(project_dir)])

    assert result.exit_code == 0, result.output
    request = provider.requests[-1]
    assert request.target_width == 1920
    assert request.target_height == 1080
    assert request.target_fps == 30


def test_generate_image_passes_resolved_target_format_to_provider(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    _approve(project_dir)
    provider = _RecordingImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir)])

    assert result.exit_code == 0, result.output
    request = provider.requests[-1]
    assert request.target_width == 1280
    assert request.target_height == 720


def test_generate_video_prints_warning_on_format_mismatch(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    # generation_service.generate_video skips format validation entirely when
    # provider_name == "mock" (see Task 3's Global Constraints guard — the mock
    # provider writes placeholder bytes ffprobe can't read). _init_mock_project
    # sets every stage's config provider to "mock", so this one test switches
    # video's config provider string to "fal" — the actual provider OBJECT
    # used is still the fake _RecordingVideoProvider below, injected via the
    # resolve_provider monkeypatch; only the provider_name string that flows
    # into generate_video's skip-guard needs to read "fal" here.
    config = json.loads((project_dir / "config.json").read_text())
    config["providers"]["video"]["provider"] = "fal"
    (project_dir / "config.json").write_text(json.dumps(config))
    _approve(project_dir)

    provider = _RecordingVideoProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)
    # Force the service layer's validation to report a mismatch without needing
    # a real ffmpeg-generated file: patch _apply_video_format_validation directly.
    monkeypatch.setattr(
        "ai_film.services.generation_service._apply_video_format_validation",
        lambda artifact, *a, **k: {**artifact, "format_mismatch": True,
                                    "requested_format": {"width": 1280, "height": 720},
                                    "actual_format": {"width": 1080, "height": 1080}},
    )

    result = runner.invoke(app, ["generate-video", "--shot", "S01_SH01", "--path", str(project_dir)])

    assert result.exit_code == 0, result.output
    assert "format mismatch" in result.output
    assert "1280x720" in result.output
    assert "1080x1080" in result.output
```

Also confirm `_RecordingVideoProvider`/`_RecordingImageProvider` (already defined earlier in this test file) don't need changes — they already store whatever `VideoGenerationRequest`/`ImageGenerationRequest` object they receive, and Task 1 already gave those dataclasses `target_width`/`target_height`/`target_fps` attributes, so `request.target_width` etc. is already accessible on them with no edits.

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_cli_generation_commands.py -v -k "target_format or format_mismatch"`
Expected: FAIL — `assert 0 == 1280` (the CLI isn't resolving/passing target format yet).

- [ ] **Step 3: Implement**

Modify `_run_generation` (`src/ai_film/cli.py:141-153`):

```python
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
```

Modify `generate_image_cmd` (`src/ai_film/cli.py:298-320`):

```python
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
```

Modify `generate_video_cmd` (`src/ai_film/cli.py:387-424`) — add the target-format resolution lines and thread the three new params into the `generate_video_service` call:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_cli_generation_commands.py -v -k "target_format or format_mismatch"`
Expected: PASS, all 4 new tests.

- [ ] **Step 5: Run the full suite**

Run: `./.venv/bin/pytest -q`
Expected: 391 passed (387 + 4 new), 0 failed. Pay particular attention to every pre-existing `generate-video`/`generate-image`/`generate-all` test in this file and in `tests/test_render_relative_path_regression.py` — they use the `mock` provider, so Task 3's `provider_name != "mock"` guard must keep them passing with no `ffprobe` calls happening at all.

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/cli.py tests/test_cli_generation_commands.py
git commit -m "feat: wire resolved target format and strict_format into generate-video/generate-image commands"
```

---

### Task 7: `render.py` rewrite — filter_complex, audio normalization, preflight_warnings

**Files:**
- Modify: `src/ai_film/render.py` (full rewrite of `render()`; `build_manifest()` gains two fields; new helpers; `_escape_concat_path` removed)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `config.json`'s `render.resolution`/`render.fps` (Task 1); an artifact's `requested_format`/`actual_format` (Task 3), if present.
- Produces: `render()` — same signature `(project_dir, manifest, output_name="reel_001.mp4") -> Path`, now normalizes every clip via `filter_complex` and always produces a final file with audio; `preflight_warnings(manifest, project_dir) -> list[str]`; `build_manifest()`'s per-shot dicts gain `requested_format`/`actual_format` keys (read from the shot's video artifact, `None` if absent).

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_render.py`'s `_make_tiny_mp4` helper and its two ffmpeg-dependent tests, and add new ones. First, add a helper for a video with configurable resolution/audio, alongside the existing `_make_tiny_mp4`:

```python
def _make_video(
    path: Path, width: int = 64, height: int = 64, fps: int = 24,
    duration: float = 1.0, with_audio: bool = False, audio_channels: int = 2,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"testsrc=duration={duration}:size={width}x{height}:rate={fps}",
    ]
    if with_audio:
        layout = "mono" if audio_channels == 1 else "stereo"
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}", "-ac", str(audio_channels)]
    cmd += [str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
```

Remove `test_escape_concat_path_escapes_single_quote` (the function it tests, `_escape_concat_path`, is deleted in this task — there is nothing left to shell-escape once ffmpeg inputs are passed as direct subprocess argv elements). Keep `test_render_handles_single_quote_in_artifact_path` as-is — it should still pass unmodified, now for a different reason (no shell involved at all).

Add:

```python
def _write_render_config(project_dir: Path, resolution: str = "1280x720", fps: int = 24) -> None:
    (project_dir / "config.json").write_text(
        json.dumps({"render": {"resolution": resolution, "fps": fps, "strict_format": False}})
    )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_render_normalizes_mixed_resolutions_to_config_target(tmp_path: Path):
    _write_render_config(tmp_path, resolution="640x360", fps=24)
    _make_video(tmp_path / "05_video" / "S01_SH01.mp4", width=640, height=360)
    _make_video(tmp_path / "05_video" / "S01_SH02.mp4", width=800, height=450)  # different native size
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01", "05_video/S01_SH01.mp4"))
    save_shot(tmp_path / "03_shots" / "S01_SH02.json", _shot("S01_SH02", "05_video/S01_SH02.mp4"))

    manifest = build_manifest(tmp_path)
    output_path = render(tmp_path, manifest)

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(output_path),
        ],
        capture_output=True, text=True,
    )
    assert probe.stdout.strip() == "640x360"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_render_never_drops_audio_when_shots_mix_silent_and_voiced(tmp_path: Path):
    _write_render_config(tmp_path)
    _make_video(tmp_path / "05_video" / "S01_SH01.mp4", width=640, height=360, with_audio=False)
    _make_video(tmp_path / "05_video" / "S01_SH02.mp4", width=640, height=360, with_audio=True)
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01", "05_video/S01_SH01.mp4"))
    save_shot(tmp_path / "03_shots" / "S01_SH02.json", _shot("S01_SH02", "05_video/S01_SH02.mp4"))

    manifest = build_manifest(tmp_path)
    output_path = render(tmp_path, manifest)

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
            "-of", "csv=p=0", str(output_path),
        ],
        capture_output=True, text=True,
    )
    assert probe.stdout.strip() != ""  # the final file has an audio stream — the original bug


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_render_normalizes_mixed_mono_stereo_and_silent_audio(tmp_path: Path):
    _write_render_config(tmp_path)
    _make_video(tmp_path / "05_video" / "S01_SH01.mp4", with_audio=True, audio_channels=1)
    _make_video(tmp_path / "05_video" / "S01_SH02.mp4", with_audio=True, audio_channels=2)
    _make_video(tmp_path / "05_video" / "S01_SH03.mp4", with_audio=False)
    for shot_id in ("S01_SH01", "S01_SH02", "S01_SH03"):
        save_shot(tmp_path / "03_shots" / f"{shot_id}.json", _shot(shot_id, f"05_video/{shot_id}.mp4"))

    manifest = build_manifest(tmp_path)
    output_path = render(tmp_path, manifest)  # must not raise — this is the regression this test guards

    assert output_path.exists()
    assert output_path.stat().st_size > 0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_render_normalizes_mixed_fps_to_config_target(tmp_path: Path):
    _write_render_config(tmp_path, resolution="640x360", fps=24)
    _make_video(tmp_path / "05_video" / "S01_SH01.mp4", width=640, height=360, fps=24)
    _make_video(tmp_path / "05_video" / "S01_SH02.mp4", width=640, height=360, fps=30)
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01", "05_video/S01_SH01.mp4"))
    save_shot(tmp_path / "03_shots" / "S01_SH02.json", _shot("S01_SH02", "05_video/S01_SH02.mp4"))

    manifest = build_manifest(tmp_path)
    output_path = render(tmp_path, manifest)

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate", "-of", "default=noprint_wrappers=1:nokey=1",
            str(output_path),
        ],
        capture_output=True, text=True,
    )
    from fractions import Fraction
    assert Fraction(probe.stdout.strip()) == Fraction(24, 1)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_preflight_warnings_reports_resolution_and_audio_spread(tmp_path: Path):
    _write_render_config(tmp_path, resolution="1280x720", fps=24)
    _make_video(tmp_path / "05_video" / "S01_SH01.mp4", width=640, height=360, with_audio=True)
    _make_video(tmp_path / "05_video" / "S01_SH02.mp4", width=800, height=450, with_audio=False)
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01", "05_video/S01_SH01.mp4"))
    save_shot(tmp_path / "03_shots" / "S01_SH02.json", _shot("S01_SH02", "05_video/S01_SH02.mp4"))

    manifest = build_manifest(tmp_path)
    warnings = preflight_warnings(manifest, tmp_path)

    joined = "\n".join(warnings)
    assert "S01_SH01" in joined
    assert "S01_SH02" in joined
    assert "audio: no" in joined
    assert "final output format: 1280x720@24" in joined


def test_preflight_warnings_returns_empty_list_without_ffprobe(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("ai_film.render.shutil.which", lambda name: None)
    manifest = {"shots": [{"id": "S01_SH01", "video": "05_video/S01_SH01.mp4"}]}
    assert preflight_warnings(manifest, tmp_path) == []
```

Update the imports at the top of `tests/test_render.py`:

```python
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ai_film.render import RenderPreflightError, build_manifest, preflight, preflight_warnings, render
from ai_film.shot_store import save_shot
```

(`_escape_concat_path` is removed from this import list — it no longer exists.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_render.py -v`
Expected: FAIL — `ImportError: cannot import name 'preflight_warnings'`, and the removed `_escape_concat_path` import/test also fail until Step 3 lands.

- [ ] **Step 3: Implement**

Replace the entire contents of `src/ai_film/render.py`:

```python
from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

from ai_film.shot_store import list_shot_paths, load_shot

_ASPECT_RATIO_TOLERANCE = 0.02
_DEFAULT_RESOLUTION = "1280x720"
_DEFAULT_FPS = 24


def build_manifest(project_dir: Path) -> dict:
    shots_dir = project_dir / "03_shots"
    shot_paths = list_shot_paths(shots_dir)
    shots = []
    for path in shot_paths:
        shot = load_shot(path)
        video = shot["generation"]["video"]
        artifact = video.get("artifact") or {}
        shots.append({
            "id": shot["id"],
            "video": artifact.get("path"),
            "duration": shot.get("duration_seconds"),
            "requested_format": artifact.get("requested_format"),
            "actual_format": artifact.get("actual_format"),
        })
    return {"shots": shots, "audio": [], "captions": []}


def preflight(manifest: dict, project_dir: Path) -> list[str]:
    errors: list[str] = []
    shots_dir = project_dir / "03_shots"
    resolved_root = project_dir.resolve()

    if not manifest["shots"]:
        return ["manifest contains no shots to render"]

    for entry in manifest["shots"]:
        shot_id = entry["id"]
        shot_path = shots_dir / f"{shot_id}.json"
        if not shot_path.exists():
            errors.append(f"{shot_id}: shot.json not found at {shot_path}")
            continue

        shot = load_shot(shot_path)
        if shot["generation"].get("video", {}).get("status") == "failed":
            errors.append(f"{shot_id}: video generation status is 'failed'")

        video_path_str = entry.get("video")
        if not video_path_str:
            errors.append(f"{shot_id}: no video artifact path in manifest")
            continue

        video_path = (project_dir / video_path_str).resolve()
        try:
            video_path.relative_to(resolved_root)
        except ValueError:
            errors.append(f"{shot_id}: artifact path escapes project directory: {video_path}")
            continue

        if not video_path.exists():
            errors.append(f"{shot_id}: artifact file does not exist: {video_path}")
        elif video_path.stat().st_size == 0:
            errors.append(f"{shot_id}: artifact file is empty: {video_path}")

    return errors


def _probe_duration(video_path: Path) -> float:
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
        ],
        capture_output=True, text=True,
    )
    return float(probe.stdout.strip())


def _probe_resolution(video_path: Path) -> tuple[int, int]:
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(video_path),
        ],
        capture_output=True, text=True,
    )
    width_str, height_str = probe.stdout.strip().split("x")
    return int(width_str), int(height_str)


def _probe_fps(video_path: Path) -> Fraction:
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate", "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True, text=True,
    )
    return Fraction(probe.stdout.strip())


def _has_audio_stream(video_path: Path) -> bool:
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a",
            "-show_entries", "stream=index", "-of", "csv=p=0", str(video_path),
        ],
        capture_output=True, text=True,
    )
    return bool(probe.stdout.strip())


def _parse_resolution(resolution: str) -> tuple[int, int]:
    width_str, height_str = resolution.lower().split("x")
    return int(width_str), int(height_str)


def _render_target(project_dir: Path) -> tuple[int, int, int]:
    """The final output format (see the design spec's Terminology
    section) — render's own unconditional normalization target, read
    directly from config.json's render section every call, independent of
    any individual shot's own target format."""
    config = json.loads((project_dir / "config.json").read_text())
    render_config = config.get("render", {})
    width, height = _parse_resolution(render_config.get("resolution", _DEFAULT_RESOLUTION))
    fps = render_config.get("fps", _DEFAULT_FPS)
    return width, height, fps


def preflight_warnings(manifest: dict, project_dir: Path) -> list[str]:
    """Non-fatal diagnostics: one line per shot showing its declared
    (generation-time target, if the artifact recorded one) vs. actual
    (ffprobe'd) format and audio presence, plus a final summary line for
    the render's own unconditional target. Never blocks — render()'s own
    normalization already handles every case shown here correctly. Only
    inspects shots whose artifact file already exists; preflight() (the
    blocking check) already reports a missing file."""
    if shutil.which("ffprobe") is None:
        return []
    lines: list[str] = []
    for entry in manifest["shots"]:
        video_path_str = entry.get("video")
        if not video_path_str:
            continue
        video_path = project_dir / video_path_str
        if not video_path.exists() or video_path.stat().st_size == 0:
            continue
        requested = entry.get("requested_format")
        actual = entry.get("actual_format")
        if requested and actual:
            declared = f"{requested['width']}x{requested['height']}@{requested.get('fps', '?')}"
            actual_str = f"{actual['width']}x{actual['height']}@{actual.get('fps', '?')}"
        else:
            width, height = _probe_resolution(video_path)
            fps = _probe_fps(video_path)
            declared = "unknown (generated before this shot recorded a format)"
            actual_str = f"{width}x{height}@{float(fps):.3f}"
        audio = "yes" if _has_audio_stream(video_path) else "no (padded with silence)"
        lines.append(f"{entry['id']}: declared {declared}, actual {actual_str}, audio: {audio}")
    if lines:
        width, height, fps = _render_target(project_dir)
        lines.append(f"final output format: {width}x{height}@{fps} (from config.json render.resolution/fps)")
    return lines


class RenderPreflightError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def render(project_dir: Path, manifest: dict, output_name: str = "reel_001.mp4") -> Path:
    errors = preflight(manifest, project_dir)
    if errors:
        raise RenderPreflightError(errors)

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("ffmpeg/ffprobe is not installed or not on PATH")

    width, height, fps = _render_target(project_dir)
    video_paths = [(project_dir / entry["video"]).resolve() for entry in manifest["shots"]]

    filter_parts = []
    video_labels = []
    audio_labels = []
    for i, video_path in enumerate(video_paths):
        filter_parts.append(
            f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[v{i}]"
        )
        video_labels.append(f"[v{i}]")
        if _has_audio_stream(video_path):
            filter_parts.append(
                f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo[a{i}]"
            )
        else:
            duration = _probe_duration(video_path)
            filter_parts.append(
                f"anullsrc=channel_layout=stereo:sample_rate=48000:duration={duration}[a{i}]"
            )
        audio_labels.append(f"[a{i}]")

    n = len(video_paths)
    concat_inputs = "".join(v + a for v, a in zip(video_labels, audio_labels))
    filter_parts.append(f"{concat_inputs}concat=n={n}:v=1:a=1[vout][aout]")
    filter_complex = ";".join(filter_parts)

    output_path = project_dir / "final" / output_name
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["ffmpeg", "-y"]
    for video_path in video_paths:
        cmd += ["-i", str(video_path)]
    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-c:a", "aac",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path
```

(`_escape_concat_path` and the concat-list-file logic are gone entirely — ffmpeg inputs are now direct subprocess argv elements via the `-i` flags built in the loop above, so there is nothing to shell-escape.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_render.py -v`
Expected: PASS, all tests (existing ones unmodified in behavior, e.g. `test_render_produces_playable_final_video`, plus all new ones from Step 1).

- [ ] **Step 5: Run the full suite**

Run: `./.venv/bin/pytest -q`
Expected: 396 passed (391 − 1 removed `_escape_concat_path` test + 6 new: mixed resolution, never-drops-audio, mixed mono/stereo/silent, fps normalization, preflight resolution/audio spread, preflight without ffprobe), 0 failed. Also re-run the cross-file regression test that exercises `render` end-to-end through the CLI with a genuinely relative `--path`:

Run: `./.venv/bin/pytest tests/test_render_relative_path_regression.py -v`
Expected: PASS — confirms the rewritten `render()` still resolves paths correctly under a relative `--path` (this test's `_shot()` helper writes shots with the default `providers.video.provider = "mock"`, which — per Task 3/6 — writes overwritten-by-hand real mp4s via `_overwrite_with_real_mp4`, so `render()`'s own `filter_complex` normalization runs against real files here too).

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/render.py tests/test_render.py
git commit -m "fix: replace render's concat-demuxer+copy with filter_complex+re-encode, normalizing resolution/fps/audio unconditionally"
```

---

### Task 8: Documentation

**Files:**
- Modify: `README.md`
- Modify: `.claude/agents/ai-film-storyboard.md`
- Modify: `.claude/agents/ai-film-media.md`

**Interfaces:**
- Consumes: everything from Tasks 1-7 (this task only documents already-shipped behavior).
- Produces: no code interfaces — documentation only.

- [ ] **Step 1: Update README.md**

In the "Known limitations (v1)" section, remove the bullet:

```
- **`render` drops audio.** Voice/sfx/music generate and save to disk correctly, but
  the render manifest doesn't include them yet — the final video is video-only.
```

In the "Command reference" section, after the existing `generate-video`/`generate-lipsync` paragraphs, add:

```markdown
`shot.json` may declare an optional `format` object (`{"resolution": "1280x720", "fps":
24}`) — an explicit per-shot override of the project's production format. Most shots
don't need one: `config.json`'s `render.resolution`/`render.fps` (default `1280x720`/`24`)
is the project default every shot without its own `format` inherits, and it's also what
`render` unconditionally normalizes every clip to at final-render time regardless of any
shot's own target or what a provider actually returned. `render.strict_format` (default
`false`) makes a generation call fail if the actual artifact's aspect ratio deviates from
its target by more than 2% — resolution and fps differences never fail generation in
either mode, since no fal model used by this project can hit an exact target pixel size
or frame rate (verified against each model's own schema); `render` is what actually
enforces the exact final size.
```

In the "Project layout" section's `config.json` line, note the new key — change:

```
config.json                 # provider/model selection, approval state, generation settings
```

to:

```
config.json                 # provider/model selection, approval state, generation settings,
                             #   render.resolution/render.fps/render.strict_format (production format)
```

- [ ] **Step 2: Update `.claude/agents/ai-film-storyboard.md`**

Read the file in full first to match its established prose style and the existing Step numbering (Steps 0-5) before editing. Add, in the step where a new shot's `shot.json` is authored (before its action/dialogue text is written — the same point Scene Continuity's `set-scene-continuity` guidance was added, per that feature's own instructions in this file), a short paragraph instructing the agent to populate the shot's `format` field, resolved from `config.json`'s `render.resolution`/`render.fps`, on every newly-authored shot:

```markdown
**Populate the shot's production format.** Every new shot.json this step writes should
include a `format` field resolved from the project's `config.json`:

```json
"format": {
  "resolution": "<config.json's render.resolution>",
  "fps": <config.json's render.fps>
}
```

This is optional at the schema level (older shots without it fall back to
`config.json`'s `render` defaults automatically at generation time — nothing breaks if
it's ever missing), but every shot this agent writes going forward should carry it
explicitly, so `shot.json` stays a self-contained production contract rather than
depending on the project's current config at generation time. Only set this to
something other than the project default if the human has explicitly asked for a
different format for this specific shot (e.g. one hero shot at a higher resolution) —
otherwise, always mirror `config.json`'s current `render` values.
```

- [ ] **Step 3: Update `.claude/agents/ai-film-media.md`**

Read the file in full first. Near the existing note about `generate-video`/`generate-voice` being free no-ops (`.claude/agents/ai-film-media.md:75`, "Never skip `generate-video`/`generate-voice`..."), add a short paragraph:

```markdown
**A `format_mismatch: true` flag on a video/image artifact is informational, not
actionable.** `generate-video`/`generate-image` record the shot's requested production
format alongside whatever a provider actually returned, and print a warning line when
the actual aspect ratio deviates from the target by more than a small tolerance — this
reflects real, expected provider behavior (no video/image model this project uses can
hit an exact target resolution), not a defect to fix. Don't regenerate a shot solely
because of this flag; only look into it if the human directly asks why a shot looks
visually off, since `render` normalizes every clip's resolution/aspect ratio/fps
unconditionally at final-render time regardless of this flag.
```

- [ ] **Step 4: Run the full suite one final time**

Run: `./.venv/bin/pytest -q`
Expected: 396 passed, 0 failed (docs-only task, no test count change).

- [ ] **Step 5: Commit**

```bash
git add README.md .claude/agents/ai-film-storyboard.md .claude/agents/ai-film-media.md
git commit -m "docs: document render format contract for README and storyboard/media agents"
```

---

## Final Verification

After Task 8's commit, run the full suite one more time from a clean state and confirm the count:

```bash
./.venv/bin/pytest -q
```

Expected: 396 passed, 0 failed, 0 skipped-that-shouldn't-be (ffmpeg-gated tests only skip if ffmpeg genuinely isn't on `PATH` — confirm `which ffmpeg` succeeds in the environment these tests run in before treating any skip as acceptable).

Confirm `git status --porcelain` shows nothing under `one-more-life/` or touching `.env` across every commit in this plan (`git log --stat` for each of Tasks 1-8's commits) — this plan should only ever have touched `src/ai_film/`, `tests/`, `README.md`, `.claude/agents/`, and (already committed, pre-implementation) `docs/superpowers/`.
