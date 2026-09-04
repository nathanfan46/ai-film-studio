# Motion-Transfer (Pose-Guided) Video Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `Capability.MOTION_TRANSFER` — a `generate-motion-transfer` CLI command that retargets a driving reference video's motion onto a shot's locked character reference image via `fal-ai/kling-video/v2.6/standard/motion-control`, writing into the shot's existing `generation.video` artifact slot so every downstream consumer (`render`, `generate-lipsync`, `mux-audio`, media review) needs zero changes.

**Architecture:** Structurally parallel to `Capability.LIPSYNC` (its own request/provider/service function, different request shape than plain video generation) but with `generate-video`'s idempotency semantics (skip if completed, `--force` to regenerate — not lipsync's always-supersede). Resolves and validates the shot's production format through the exact same helpers `generate-video` already uses (`_resolve_target_format`, `_apply_video_format_validation`) — no second format policy. Reuses `_resize_reference_for_target` (imported, not duplicated) from `providers/fal/video.py` to communicate the target aspect ratio to a provider with no native resolution/aspect_ratio field. `keep_original_sound` is hardcoded `false` at the provider layer, never a request field.

**Tech Stack:** Python, fal.ai queue API, ffmpeg/ffprobe, pytest, typer.

**Spec:** `docs/superpowers/specs/2026-09-03-motion-transfer-design.md`

## Global Constraints

- Never touch `one-more-life/`, `hot-&-fierce-villain/`, or any other live project directory, or `.env` — standing rule, unaffected by this plan's scope (`src/ai_film/`, `tests/`, `docs/`, `.claude/agents/`, `README.md` only).
- `keep_original_sound` is hardcoded `false` inside `FalMotionTransferProvider.submit()` — it is never a field on `MotionTransferRequest` and never a caller-supplied parameter anywhere in the call chain.
- `character_orientation` always resolves to `"video"` from the CLI command (kling's mode documented as better for complex motion) — the dataclass field exists with that default so a future caller *could* override it, but `generate-motion-transfer` itself never does.
- Motion-transfer format handling MUST go through the exact same helpers `generate-video` uses (`_resolve_target_format` in `cli.py`, `_apply_video_format_validation` in `generation_service.py`) — implementing a second, parallel format-handling path anywhere in this plan is a defect, not a design choice.
- `_resize_reference_for_target` is imported from `providers/fal/video.py` into `providers/fal/motion_transfer.py` — it is not duplicated. That function invokes real ffmpeg subprocess logic, past the threshold where this codebase's "small helper duplicated per module" convention applies (that convention covers two-line ffprobe/ratio-math wrappers, not a temp-file-and-subprocess function).
- `shot.characters[0]` is the driving character for motion-transfer specifically — this is a motion-transfer-scoped rule, not a schema-wide "first character is primary" claim (the schema's `characters` array has no ordering contract anywhere else).
- `driving_video.path` (like every other reference path in this codebase — `characters[i].reference`, `environment.reference`) is project-root-relative, resolved as `path / shot_data["driving_video"]["path"]`.
- Full test suite must stay green after every task (400 passing at the start of this plan; run `./.venv/bin/pytest -q` after each task).

---

### Task 1: Data model, config, schema, and catalog foundations

**Files:**
- Modify: `src/ai_film/models.py:14-20` (`Capability` enum), add new `MotionTransferRequest` dataclass after `LipsyncGenerationRequest` (`src/ai_film/models.py:84-90`)
- Modify: `src/ai_film/project.py:17-24` (`DEFAULT_CONFIG["providers"]`)
- Modify: `src/ai_film/schema.py:59-77` (`SHOT_SCHEMA`)
- Modify: `src/ai_film/providers/fal/catalog.py:5-39` (`_MODELS`)
- Test: `tests/test_models.py`, `tests/test_project.py`, `tests/test_schema.py`, `tests/providers/test_fal_providers.py`

**Interfaces:**
- Consumes: nothing (foundational).
- Produces: `Capability.MOTION_TRANSFER = "motion_transfer"`; `MotionTransferRequest(image_path: str, driving_video_path: str, model: str, character_orientation: str = "video", prompt: str = "", output_path: str = "", target_width: int = 0, target_height: int = 0, target_fps: int = 0)`; `DEFAULT_CONFIG["providers"]["motion_transfer"] = {"provider": "fal", "model": "kling-motion-control", "parameters": {}}`; `SHOT_SCHEMA` accepts an optional top-level `driving_video: {"path": str}` object; `FalProviderCatalog().models(Capability.MOTION_TRANSFER)` returns the new `kling-motion-control` entry.

- [ ] **Step 1: Write the failing tests**

`tests/test_models.py` — add:

```python
def test_motion_transfer_request_defaults():
    from ai_film.models import MotionTransferRequest

    request = MotionTransferRequest(
        image_path="ref.png", driving_video_path="dance.mp4", model="kling-motion-control",
    )
    assert request.character_orientation == "video"
    assert request.prompt == ""
    assert request.output_path == ""
    assert request.target_width == 0
    assert request.target_height == 0
    assert request.target_fps == 0
    # keep_original_sound is deliberately NOT a field on this dataclass —
    # it's hardcoded at the provider layer (Task 3), never a request param.
    assert not hasattr(request, "keep_original_sound")
```

`tests/test_project.py` — add:

```python
def test_default_config_includes_motion_transfer_provider(tmp_path: Path):
    project_dir = init_project(tmp_path / "project", "Test Film")
    config = json.loads((project_dir / "config.json").read_text())
    assert config["providers"]["motion_transfer"] == {
        "provider": "fal", "model": "kling-motion-control", "parameters": {},
    }
```

`tests/test_schema.py` — add:

```python
def test_shot_schema_accepts_optional_driving_video_field():
    shot = _valid_shot()
    shot["driving_video"] = {"path": "05_video/reference_clips/dance.mp4"}
    assert validate_shot(shot) == []


def test_shot_schema_accepts_missing_driving_video_field():
    shot = _valid_shot()
    assert "driving_video" not in shot
    assert validate_shot(shot) == []
```

`tests/providers/test_fal_providers.py` — add:

```python
def test_catalog_lists_kling_motion_control_for_motion_transfer():
    from ai_film.models import Capability
    from ai_film.providers.fal.catalog import FalProviderCatalog

    models = FalProviderCatalog().models(Capability.MOTION_TRANSFER)
    assert [m.model for m in models] == ["kling-motion-control"]
    assert models[0].provider == "fal"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_models.py tests/test_project.py tests/test_schema.py tests/providers/test_fal_providers.py -v -k "motion_transfer or driving_video or kling_motion_control"`
Expected: FAIL — `ImportError`/`AttributeError`/`KeyError` (`Capability.MOTION_TRANSFER` and `MotionTransferRequest` don't exist yet).

- [ ] **Step 3: Implement**

`src/ai_film/models.py` — change the `Capability` enum:

```python
class Capability(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    VOICE = "voice"
    SFX = "sfx"
    MUSIC = "music"
    LIPSYNC = "lipsync"
    MOTION_TRANSFER = "motion_transfer"
```

Add, immediately after `LipsyncGenerationRequest`:

```python
@dataclass
class MotionTransferRequest:
    image_path: str
    driving_video_path: str
    model: str
    character_orientation: str = "video"
    prompt: str = ""
    output_path: str = ""
    target_width: int = 0
    target_height: int = 0
    target_fps: int = 0
```

(Reuses `VideoGenerationResult` for output — no new result dataclass. `keep_original_sound` is intentionally absent — see Global Constraints.)

`src/ai_film/project.py` — add one line to `DEFAULT_CONFIG["providers"]`:

```python
DEFAULT_CONFIG = {
    "providers": {
        "image": {"provider": "fal", "model": "nano-banana", "parameters": {}},
        "video": {"provider": "fal", "model": "veo-3", "parameters": {}},
        "voice": {"provider": "fal", "model": "csm-1b", "parameters": {}},
        "sfx": {"provider": "fal", "model": "thinksound", "parameters": {}},
        "music": {"provider": "fal", "model": "cassetteai-music", "parameters": {}},
        "lipsync": {"provider": "fal", "model": "kling-lipsync", "parameters": {}},
        "motion_transfer": {"provider": "fal", "model": "kling-motion-control", "parameters": {}},
    },
    "generation": {"max_attempts": 3, "max_parallel_jobs": 3, "poll_interval_seconds": 5},
    "render": {"resolution": "1280x720", "fps": 24, "strict_format": False},
    "generation_approval": {},
}
```

`src/ai_film/schema.py` — add a `"driving_video"` property to `SHOT_SCHEMA["properties"]` (not added to the top-level `"required"` list — stays optional):

```python
"driving_video": {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
    },
},
```

`src/ai_film/providers/fal/catalog.py` — add to `_MODELS`:

```python
ModelInfo(
    "fal", "kling-motion-control", Capability.MOTION_TRANSFER,
    "Kling v2.6 Motion Control (driving-video + reference-image -> character"
    " performs that motion, $0.07/s)",
),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_models.py tests/test_project.py tests/test_schema.py tests/providers/test_fal_providers.py -v -k "motion_transfer or driving_video or kling_motion_control"`
Expected: PASS, all 5 new tests.

- [ ] **Step 5: Run the full suite**

Run: `./.venv/bin/pytest -q`
Expected: 405 passed (400 + 5 new), 0 failed.

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/models.py src/ai_film/project.py src/ai_film/schema.py src/ai_film/providers/fal/catalog.py \
  tests/test_models.py tests/test_project.py tests/test_schema.py tests/providers/test_fal_providers.py
git commit -m "feat: add motion-transfer capability, request type, config default, schema field, catalog entry"
```

---

### Task 2: Mock motion-transfer provider

**Files:**
- Create: `src/ai_film/providers/mock/motion_transfer.py`
- Test: `tests/providers/test_mock_providers.py`

**Interfaces:**
- Consumes: `Capability.MOTION_TRANSFER`, `MotionTransferRequest` (Task 1).
- Produces: `MockMotionTransferProvider` with `submit(request) -> GenerationJob`, `poll(job) -> JobStatus`, `get_result(job) -> VideoGenerationResult`, matching the exact shape of every other mock provider in this codebase (`_submit_calls` counter, `fail_first_n_submits`/`polls_until_complete` constructor knobs, writes placeholder bytes, no network).

- [ ] **Step 1: Write the failing test**

Add to `tests/providers/test_mock_providers.py` (add `MotionTransferRequest` to the existing `from ai_film.models import (...)` block, and add a new import line for the provider):

```python
def test_mock_motion_transfer_provider_completes_and_writes_artifact(tmp_path: Path):
    from ai_film.providers.mock.motion_transfer import MockMotionTransferProvider

    provider = MockMotionTransferProvider()
    output_path = tmp_path / "shot.mp4"
    request = MotionTransferRequest(
        image_path=str(tmp_path / "ref.png"), driving_video_path=str(tmp_path / "dance.mp4"),
        model="kling-motion-control", output_path=str(output_path),
    )
    job = provider.submit(request)
    assert job.capability == Capability.MOTION_TRANSFER
    assert provider.poll(job) == JobStatus.COMPLETED
    result = provider.get_result(job)
    assert result.artifact_path == str(output_path)
    assert output_path.exists()
    assert result.size_bytes > 0
    assert result.duration_seconds > 0


def test_mock_motion_transfer_provider_simulates_submit_failures(tmp_path: Path):
    from ai_film.providers.mock.motion_transfer import MockMotionTransferProvider

    provider = MockMotionTransferProvider(fail_first_n_submits=1)
    request = MotionTransferRequest(
        image_path=str(tmp_path / "ref.png"), driving_video_path=str(tmp_path / "dance.mp4"),
        model="kling-motion-control", output_path=str(tmp_path / "shot.mp4"),
    )
    with pytest.raises(ProviderError):
        provider.submit(request)
    job = provider.submit(request)
    assert provider.poll(job) == JobStatus.COMPLETED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/pytest tests/providers/test_mock_providers.py -v -k "motion_transfer"`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_film.providers.mock.motion_transfer'`.

- [ ] **Step 3: Implement**

Create `src/ai_film/providers/mock/motion_transfer.py`:

```python
from __future__ import annotations

from pathlib import Path

from ai_film.errors import ProviderError
from ai_film.models import (
    Capability,
    GenerationJob,
    JobStatus,
    MotionTransferRequest,
    VideoGenerationResult,
)

# MotionTransferRequest carries no duration_seconds field (real duration is
# entirely provider-determined — see providers/fal/motion_transfer.py's own
# duration probing), so unlike every other mock provider this one has no
# input value to echo back. Any fixed positive placeholder works since
# nothing asserts on its exact value.
_MOCK_DURATION_SECONDS = 2.0


class MockMotionTransferProvider:
    def __init__(self, fail_first_n_submits: int = 0, polls_until_complete: int = 1):
        self.fail_first_n_submits = fail_first_n_submits
        self.polls_until_complete = polls_until_complete
        self._submit_calls = 0
        self._poll_counts: dict[str, int] = {}
        self._requests: dict[str, MotionTransferRequest] = {}

    def submit(self, request: MotionTransferRequest) -> GenerationJob:
        self._submit_calls += 1
        if self._submit_calls <= self.fail_first_n_submits:
            raise ProviderError("simulated submit failure")
        job_id = f"mock-motion-transfer-{self._submit_calls}"
        self._requests[job_id] = request
        self._poll_counts[job_id] = 0
        return GenerationJob(provider="mock", id=job_id, capability=Capability.MOTION_TRANSFER)

    def poll(self, job: GenerationJob) -> JobStatus:
        self._poll_counts[job.id] += 1
        if self._poll_counts[job.id] >= self.polls_until_complete:
            return JobStatus.COMPLETED
        return JobStatus.RUNNING

    def get_result(self, job: GenerationJob) -> VideoGenerationResult:
        request = self._requests[job.id]
        output_path = Path(request.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"MOCK-MP4-DATA")
        return VideoGenerationResult(
            artifact_path=str(output_path),
            size_bytes=output_path.stat().st_size,
            duration_seconds=_MOCK_DURATION_SECONDS,
        )
```

Add `MotionTransferRequest` to `tests/providers/test_mock_providers.py`'s existing `from ai_film.models import (...)` block (alphabetical, matching the existing style of that import list).

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/providers/test_mock_providers.py -v -k "motion_transfer"`
Expected: PASS, both new tests.

- [ ] **Step 5: Run the full suite**

Run: `./.venv/bin/pytest -q`
Expected: 407 passed (405 + 2 new), 0 failed.

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/providers/mock/motion_transfer.py tests/providers/test_mock_providers.py
git commit -m "feat: add MockMotionTransferProvider"
```

---

### Task 3: Fal motion-transfer provider (kling-motion-control adapter)

**Files:**
- Create: `src/ai_film/providers/fal/motion_transfer.py`
- Test: `tests/providers/test_fal_providers.py`

**Interfaces:**
- Consumes: `MotionTransferRequest` (Task 1); `_resize_reference_for_target` from `providers/fal/video.py` (imported, not duplicated — already exists); `client.upload_file`/`client.submit`/`client.poll`/`client.result`/`client.download` (already exist in `providers/fal/client.py`).
- Produces: `FalMotionTransferProvider` (`submit`/`poll`/`get_result`); `MODEL_TO_APP_ID = {"kling-motion-control": "fal-ai/kling-video/v2.6/standard/motion-control"}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/providers/test_fal_providers.py` (this file already has `@patch("ai_film.providers.fal.client.requests")`-style tests, `_mock_submit_response`, and imports `MotionTransferRequest`/`Capability` from Task 1/2 work — add the provider import too):

```python
from ai_film.providers.fal.motion_transfer import FalMotionTransferProvider, MODEL_TO_APP_ID


def test_motion_transfer_model_to_app_id_is_kling_motion_control():
    assert MODEL_TO_APP_ID == {
        "kling-motion-control": "fal-ai/kling-video/v2.6/standard/motion-control",
    }


@patch("ai_film.providers.fal.client.requests")
def test_motion_transfer_provider_sends_expected_request_body(
    mock_requests, tmp_path: Path, monkeypatch
):
    """Verified against fal's real OpenAPI schema for
    fal-ai/kling-video/v2.6/standard/motion-control: image_url, video_url,
    character_orientation, and keep_original_sound are the fields that
    matter here. keep_original_sound must always be False — never a
    request-object value — per this project's own audio-ownership design
    (dialogue/sfx/music are attached later, deliberately, via
    generate-lipsync/mux-audio)."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    _mock_submit_response(mock_requests)
    image = tmp_path / "ref.png"
    image.write_bytes(b"REF-PNG")
    driving_video = tmp_path / "dance.mp4"
    driving_video.write_bytes(b"DRIVING-MP4")
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file",
        lambda path: f"https://cdn.fal.run/{Path(path).name}",
    )

    provider = FalMotionTransferProvider()
    provider.submit(
        MotionTransferRequest(
            image_path=str(image), driving_video_path=str(driving_video),
            model="kling-motion-control", output_path=str(tmp_path / "out.mp4"),
        )
    )

    called_url = mock_requests.post.call_args.args[0]
    assert called_url == "https://queue.fal.run/fal-ai/kling-video/v2.6/standard/motion-control"
    sent_input = mock_requests.post.call_args.kwargs["json"]
    assert sent_input["image_url"] == "https://cdn.fal.run/ref.png"
    assert sent_input["video_url"] == "https://cdn.fal.run/dance.mp4"
    assert sent_input["character_orientation"] == "video"
    assert sent_input["keep_original_sound"] is False
    assert "prompt" not in sent_input  # empty prompt is omitted, not sent as ""


@patch("ai_film.providers.fal.client.requests")
def test_motion_transfer_provider_sends_prompt_only_when_non_empty(
    mock_requests, tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("FAL_KEY", "test-key")
    _mock_submit_response(mock_requests)
    image = tmp_path / "ref.png"
    image.write_bytes(b"REF-PNG")
    driving_video = tmp_path / "dance.mp4"
    driving_video.write_bytes(b"DRIVING-MP4")
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file",
        lambda path: f"https://cdn.fal.run/{Path(path).name}",
    )

    provider = FalMotionTransferProvider()
    provider.submit(
        MotionTransferRequest(
            image_path=str(image), driving_video_path=str(driving_video),
            model="kling-motion-control", output_path=str(tmp_path / "out.mp4"),
            prompt="a woman dancing energetically",
        )
    )

    sent_input = mock_requests.post.call_args.kwargs["json"]
    assert sent_input["prompt"] == "a woman dancing energetically"


@patch("ai_film.providers.fal.client.requests")
def test_motion_transfer_provider_resizes_reference_image_when_target_set(
    mock_requests, tmp_path: Path, monkeypatch
):
    """Reuses _resize_reference_for_target from providers/fal/video.py —
    kling-motion-control has no resolution/aspect_ratio field, so the
    reference image's own aspect ratio is the only lever available (same
    situation this project already solved once for h3-max/hailuo)."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    _mock_submit_response(mock_requests)
    image = tmp_path / "ref.png"
    image.write_bytes(b"REF-PNG")
    driving_video = tmp_path / "dance.mp4"
    driving_video.write_bytes(b"DRIVING-MP4")
    uploaded_paths = []
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file",
        lambda path: uploaded_paths.append(path) or f"https://cdn.fal.run/{Path(path).name}",
    )
    monkeypatch.setattr(
        "ai_film.providers.fal.motion_transfer._resize_reference_for_target",
        lambda image_path, width, height: Path(tmp_path / "resized.png"),
    )

    provider = FalMotionTransferProvider()
    provider.submit(
        MotionTransferRequest(
            image_path=str(image), driving_video_path=str(driving_video),
            model="kling-motion-control", output_path=str(tmp_path / "out.mp4"),
            target_width=1280, target_height=720,
        )
    )

    assert str(tmp_path / "resized.png") in uploaded_paths
    assert str(image) not in uploaded_paths
    # the driving video is never resized — see the spec's non-goals
    assert str(driving_video) in uploaded_paths


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not installed")
def test_motion_transfer_probe_duration_raises_provider_error_on_malformed_output(tmp_path: Path):
    from ai_film.errors import ProviderError
    from ai_film.providers.fal.motion_transfer import _probe_duration

    garbage = tmp_path / "not-a-video.mp4"
    garbage.write_bytes(b"not a real video")
    with pytest.raises(ProviderError):
        _probe_duration(garbage)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/providers/test_fal_providers.py -v -k "motion_transfer"`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_film.providers.fal.motion_transfer'`.

- [ ] **Step 3: Implement**

Create `src/ai_film/providers/fal/motion_transfer.py`:

```python
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ai_film.errors import ProviderError
from ai_film.models import (
    Capability, GenerationJob, JobStatus, MotionTransferRequest, VideoGenerationResult,
)
from ai_film.providers.fal import client
from ai_film.providers.fal.video import _resize_reference_for_target

# Verified against fal's real OpenAPI schema for
# fal-ai/kling-video/v2.6/standard/motion-control.
MODEL_TO_APP_ID = {
    "kling-motion-control": "fal-ai/kling-video/v2.6/standard/motion-control",
}


def _probe_duration(video_path: Path) -> float:
    """Motion-transfer's endpoint has no duration request field at all —
    output length is entirely provider-determined (bounded by the driving
    video's own 3-30.05s constraint) — so the real artifact must be
    probed, there's no static value to fall back to. Hardened the same
    way render.py's/generation_service.py's own probe helpers already are
    (raise ProviderError, not a bare subprocess/parse exception, so a
    corrupt or unparseable artifact flows through run_generation_stage's
    existing restore-and-mark-failed path instead of crashing uncaught)."""
    if shutil.which("ffprobe") is None:
        raise ProviderError("ffprobe is not installed or not on PATH")
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
        ],
        capture_output=True, text=True,
    )
    raw = probe.stdout.strip()
    try:
        return float(raw)
    except ValueError as exc:
        raise ProviderError(
            f"could not parse duration for {video_path}: ffprobe returned {raw!r}"
        ) from exc


class FalMotionTransferProvider:
    def __init__(self):
        self._jobs: dict[str, tuple[str, str, MotionTransferRequest]] = {}

    def submit(self, request: MotionTransferRequest) -> GenerationJob:
        image_path = request.image_path
        if request.target_width and request.target_height:
            image_path = str(
                _resize_reference_for_target(
                    Path(image_path), request.target_width, request.target_height
                )
            )
        input_data = {
            "image_url": client.upload_file(image_path),
            "video_url": client.upload_file(request.driving_video_path),
            "character_orientation": request.character_orientation,
            # Always False — never a request-object value. This project
            # already has a dedicated, deliberate place audio gets
            # attached to a shot (generate-lipsync, mux-audio);
            # inheriting a driving clip's own soundtrack would introduce
            # a second, accidental audio source into generation.video.
            "keep_original_sound": False,
        }
        if request.prompt:
            input_data["prompt"] = request.prompt
        app_id = MODEL_TO_APP_ID[request.model]
        job, status_url, response_url = client.submit(
            app_id, input_data, Capability.MOTION_TRANSFER
        )
        self._jobs[job.id] = (status_url, response_url, request)
        return job

    def poll(self, job: GenerationJob) -> JobStatus:
        status_url, _, _ = self._jobs[job.id]
        return client.poll(status_url)

    def get_result(self, job: GenerationJob) -> VideoGenerationResult:
        _, response_url, request = self._jobs[job.id]
        body = client.result(response_url)
        video_url = body["video"]["url"]
        size_bytes = client.download(video_url, request.output_path)
        duration_seconds = _probe_duration(Path(request.output_path))
        return VideoGenerationResult(
            artifact_path=request.output_path,
            size_bytes=size_bytes,
            duration_seconds=duration_seconds,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/providers/test_fal_providers.py -v -k "motion_transfer"`
Expected: PASS, all 5 new tests (the `_probe_duration` test only runs if `ffprobe` is on PATH — confirm it actually ran, not skipped, in this environment).

- [ ] **Step 5: Run the full suite**

Run: `./.venv/bin/pytest -q`
Expected: 412 passed (407 + 5 new), 0 failed.

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/providers/fal/motion_transfer.py tests/providers/test_fal_providers.py
git commit -m "feat: add FalMotionTransferProvider for kling-video/v2.6/motion-control"
```

---

### Task 4: Provider registry wiring

**Files:**
- Modify: `src/ai_film/providers/registry.py`
- Test: `tests/providers/test_fal_providers.py` or a new small test in `tests/` for the registry itself — check first whether `tests/providers/` already has a registry test file.

**Interfaces:**
- Consumes: `Capability.MOTION_TRANSFER` (Task 1), `FalMotionTransferProvider` (Task 3), `MockMotionTransferProvider` (Task 2).
- Produces: `resolve_provider(Capability.MOTION_TRANSFER, "fal")` returns a `FalMotionTransferProvider` instance; `resolve_provider(Capability.MOTION_TRANSFER, "mock")` returns a `MockMotionTransferProvider` instance.

- [ ] **Step 1: Write the failing test**

First check whether a registry test file already exists: `ls tests/providers/test_registry.py` or `grep -rn "def resolve_provider\|resolve_provider(" tests/ | grep -v "monkeypatch\|cli.py"`. If a `tests/providers/test_registry.py` (or similarly named file) already tests `resolve_provider` for the other 6 capabilities, add to it in the same style. If no such file exists, add this test to `tests/providers/test_fal_providers.py` instead (this codebase's existing convention appears to test `resolve_provider` indirectly through CLI/service tests rather than a dedicated registry test file — verify this before deciding, and match whichever pattern the codebase actually uses):

```python
def test_resolve_provider_returns_fal_motion_transfer_provider():
    from ai_film.providers.fal.motion_transfer import FalMotionTransferProvider
    from ai_film.providers.registry import resolve_provider

    provider = resolve_provider(Capability.MOTION_TRANSFER, "fal")
    assert isinstance(provider, FalMotionTransferProvider)


def test_resolve_provider_returns_mock_motion_transfer_provider():
    from ai_film.providers.mock.motion_transfer import MockMotionTransferProvider
    from ai_film.providers.registry import resolve_provider

    provider = resolve_provider(Capability.MOTION_TRANSFER, "mock")
    assert isinstance(provider, MockMotionTransferProvider)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest -v -k "resolve_provider_returns_fal_motion_transfer or resolve_provider_returns_mock_motion_transfer"`
Expected: FAIL — `ValueError: unknown provider... for motion_transfer` or `KeyError: <Capability.MOTION_TRANSFER>` (no registry entry yet).

- [ ] **Step 3: Implement**

Modify `src/ai_film/providers/registry.py`:

```python
from __future__ import annotations

from ai_film.models import Capability
from ai_film.providers.fal.audio import FalAudioProvider
from ai_film.providers.fal.image import FalImageProvider
from ai_film.providers.fal.lipsync import FalLipsyncProvider
from ai_film.providers.fal.motion_transfer import FalMotionTransferProvider
from ai_film.providers.fal.video import FalVideoProvider
from ai_film.providers.mock.audio import MockAudioProvider
from ai_film.providers.mock.image import MockImageProvider
from ai_film.providers.mock.lipsync import MockLipsyncProvider
from ai_film.providers.mock.motion_transfer import MockMotionTransferProvider
from ai_film.providers.mock.video import MockVideoProvider

_REGISTRIES = {
    Capability.IMAGE: {"fal": FalImageProvider, "mock": MockImageProvider},
    Capability.VIDEO: {"fal": FalVideoProvider, "mock": MockVideoProvider},
    Capability.VOICE: {"fal": FalAudioProvider, "mock": MockAudioProvider},
    Capability.SFX: {"fal": FalAudioProvider, "mock": MockAudioProvider},
    Capability.MUSIC: {"fal": FalAudioProvider, "mock": MockAudioProvider},
    Capability.LIPSYNC: {"fal": FalLipsyncProvider, "mock": MockLipsyncProvider},
    Capability.MOTION_TRANSFER: {
        "fal": FalMotionTransferProvider, "mock": MockMotionTransferProvider,
    },
}


def resolve_provider(capability: Capability, provider_name: str):
    registry = _REGISTRIES[capability]
    if provider_name not in registry:
        raise ValueError(
            f"unknown provider {provider_name!r} for {capability.value}; "
            f"available: {sorted(registry)}"
        )
    return registry[provider_name]()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest -v -k "resolve_provider_returns_fal_motion_transfer or resolve_provider_returns_mock_motion_transfer"`
Expected: PASS, both new tests.

- [ ] **Step 5: Run the full suite**

Run: `./.venv/bin/pytest -q`
Expected: 414 passed (412 + 2 new), 0 failed.

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/providers/registry.py tests/
git commit -m "feat: register motion-transfer provider in the capability registry"
```

---

### Task 5: `generate_motion_transfer()` service function

**Files:**
- Modify: `src/ai_film/services/generation_service.py` (imports; new function after `generate_lipsync`, `src/ai_film/services/generation_service.py:482-543`)
- Test: `tests/services/test_generation_service.py`

**Interfaces:**
- Consumes: `MotionTransferRequest` (Task 1); `run_generation_stage`, `_video_or_audio_artifact`, `_apply_video_format_validation` (already exist in this file).
- Produces: `generate_motion_transfer(project_dir, shot_path, provider, image_path: str, driving_video_path: str, model: str, output_path: Path, provider_name: str, max_attempts: int = 3, poll_interval_seconds: float = 0.0, force: bool = False, character_orientation: str = "video", prompt: str = "", target_width: int = 0, target_height: int = 0, target_fps: int = 0, strict_format: bool = False) -> dict`. Writes into `generation.video` (stage="video") — same slot `generate_video` uses.

- [ ] **Step 1: Write the failing tests**

Add to `tests/services/test_generation_service.py` (reuses the `_RealFileVideoProvider`-style pattern already in this file from earlier work — check it's still there; if the exact class isn't reusable as-is because its `submit`/`get_result` signature is tied to `VideoGenerationRequest`, adapt a `_RealFileMotionTransferProvider` test double following the identical shape, just accepting/recording a `MotionTransferRequest`):

```python
from ai_film.models import MotionTransferRequest  # add to existing models import block
from ai_film.services.generation_service import generate_motion_transfer  # add to existing import block


class _RealFileMotionTransferProvider:
    """Writes a real, ffprobe-readable video — mirrors _RealFileVideoProvider
    from the format-validation tests earlier in this file, but records
    MotionTransferRequest objects."""

    def __init__(self, width: int = 1280, height: int = 720, fps: int = 24):
        self.width, self.height, self.fps = width, height, fps
        self._requests: dict[str, object] = {}
        self._n = 0
        self.submit_calls = 0

    def submit(self, request):
        self.submit_calls += 1
        self._n += 1
        job_id = f"real-motion-transfer-{self._n}"
        self._requests[job_id] = request
        return GenerationJob(provider="test", id=job_id, capability=Capability.MOTION_TRANSFER)

    def poll(self, job):
        return JobStatus.COMPLETED

    def get_result(self, job):
        request = self._requests[job.id]
        output_path = Path(request.output_path)
        _make_real_video(output_path, self.width, self.height, self.fps, duration=2.0)
        return VideoGenerationResult(
            artifact_path=str(output_path), size_bytes=output_path.stat().st_size,
            duration_seconds=2.0,
        )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_generate_motion_transfer_writes_into_generation_video_stage(tmp_path: Path):
    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)

    stage = generate_motion_transfer(
        project_dir=project_dir, shot_path=shot_path,
        provider=_RealFileMotionTransferProvider(),
        image_path=str(tmp_path / "ref.png"), driving_video_path=str(tmp_path / "dance.mp4"),
        model="kling-motion-control", output_path=project_dir / "05_video" / "S01_SH01.mp4",
        provider_name="fal",
    )

    assert stage["status"] == "completed"
    shot = load_shot(shot_path)
    assert shot["generation"]["video"]["status"] == "completed"
    assert shot["generation"]["video"]["artifact"]["path"] == "05_video/S01_SH01.mp4"


def test_generate_motion_transfer_is_idempotent_by_default(tmp_path: Path):
    """Explicitly assert the provider is never called a second time — not
    just that the output file is unchanged, which could pass even if the
    provider were wastefully re-invoked and its result discarded."""
    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)
    provider = _RealFileMotionTransferProvider()
    kwargs = dict(
        project_dir=project_dir, shot_path=shot_path, provider=provider,
        image_path=str(tmp_path / "ref.png"), driving_video_path=str(tmp_path / "dance.mp4"),
        model="kling-motion-control", output_path=project_dir / "05_video" / "S01_SH01.mp4",
        provider_name="fal",
    )

    generate_motion_transfer(**kwargs)
    assert provider.submit_calls == 1
    generate_motion_transfer(**kwargs)
    assert provider.submit_calls == 1  # not called again


def test_generate_motion_transfer_force_regenerates(tmp_path: Path):
    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)
    provider = _RealFileMotionTransferProvider()
    kwargs = dict(
        project_dir=project_dir, shot_path=shot_path, provider=provider,
        image_path=str(tmp_path / "ref.png"), driving_video_path=str(tmp_path / "dance.mp4"),
        model="kling-motion-control", output_path=project_dir / "05_video" / "S01_SH01.mp4",
        provider_name="fal",
    )

    generate_motion_transfer(**kwargs)
    assert provider.submit_calls == 1
    generate_motion_transfer(force=True, **kwargs)
    assert provider.submit_calls == 2


def test_generate_motion_transfer_skips_format_validation_for_mock_provider(tmp_path: Path):
    from ai_film.providers.mock.motion_transfer import MockMotionTransferProvider

    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)

    stage = generate_motion_transfer(
        project_dir=project_dir, shot_path=shot_path, provider=MockMotionTransferProvider(),
        image_path=str(tmp_path / "ref.png"), driving_video_path=str(tmp_path / "dance.mp4"),
        model="kling-motion-control", output_path=project_dir / "05_video" / "S01_SH01.mp4",
        provider_name="mock", target_width=1280, target_height=720,
    )

    assert "requested_format" not in stage["artifact"]
```

(`_make_real_video`, `_project`, `_shot_path` are already defined earlier in this test file from the render-format-contract work — reuse them, don't redefine. `GenerationJob`, `JobStatus`, `Capability`, `VideoGenerationResult` are already imported in this file too — add only the two new names, `MotionTransferRequest` and `generate_motion_transfer`, to the existing import blocks.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/services/test_generation_service.py -v -k "motion_transfer"`
Expected: FAIL — `ImportError: cannot import name 'generate_motion_transfer'`.

- [ ] **Step 3: Implement**

Add `MotionTransferRequest` to the `from ai_film.models import (...)` block at the top of `src/ai_film/services/generation_service.py` (alphabetical, matching the existing list's ordering convention).

Add, immediately after `generate_lipsync` (`src/ai_film/services/generation_service.py:482-543`):

```python
def generate_motion_transfer(
    project_dir: Path,
    shot_path: Path,
    provider,
    image_path: str,
    driving_video_path: str,
    model: str,
    output_path: Path,
    provider_name: str,
    max_attempts: int = 3,
    poll_interval_seconds: float = 0.0,
    force: bool = False,
    character_orientation: str = "video",
    prompt: str = "",
    target_width: int = 0,
    target_height: int = 0,
    target_fps: int = 0,
    strict_format: bool = False,
) -> dict:
    """Generates a shot's video by retargeting a driving reference video's
    motion onto a character reference image, writing into the same
    generation.video slot generate_video uses — this is an alternative
    primary video-generation method for a shot, not a post-processing
    pass (unlike generate_lipsync, idempotency matches generate_video:
    skip if already completed, force=True to regenerate)."""
    request = MotionTransferRequest(
        image_path=image_path, driving_video_path=driving_video_path, model=model,
        character_orientation=character_orientation, prompt=prompt, output_path=str(output_path),
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
        force=force, superseded_reason="motion_transfer",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/services/test_generation_service.py -v -k "motion_transfer"`
Expected: PASS, all 4 new tests.

- [ ] **Step 5: Run the full suite**

Run: `./.venv/bin/pytest -q`
Expected: 418 passed (414 + 4 new), 0 failed.

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/services/generation_service.py tests/services/test_generation_service.py
git commit -m "feat: add generate_motion_transfer service writing into generation.video"
```

---

### Task 6: `generate-motion-transfer` CLI command

**Files:**
- Modify: `src/ai_film/cli.py` (imports; `_stage_config` at `src/ai_film/cli.py:137-139`; new command after `generate_lipsync_cmd`, `src/ai_film/cli.py:484-518`)
- Test: `tests/test_cli_generation_commands.py`

**Interfaces:**
- Consumes: `_resolve_target_format`, `_strict_format` (already exist in `cli.py`); `generate_motion_transfer` (Task 5); `Capability.MOTION_TRANSFER` (Task 1).
- Produces: `_stage_config(path, stage, default=None)` — backward-compatible extension (existing 6 call sites unaffected); `generate-motion-transfer --shot <id> [--force]` command; validates `driving_video.path` exists and `characters[0].reference` is set, both before the cost gate.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli_generation_commands.py` (reuses `_init_mock_project`, `_approve`, `_shot` helpers already in this file):

```python
def test_generate_motion_transfer_fails_without_driving_video(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    _approve(project_dir)

    result = runner.invoke(
        app, ["generate-motion-transfer", "--shot", "S01_SH01", "--path", str(project_dir)]
    )

    assert result.exit_code == 1
    assert "driving_video" in result.output


def test_generate_motion_transfer_fails_when_driving_video_file_missing(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["driving_video"] = {"path": "05_video/reference_clips/dance.mp4"}  # never created
    shot["characters"] = [{"name": "girl", "reference": "assets/characters/girl/reference.png"}]
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)
    _approve(project_dir)

    result = runner.invoke(
        app, ["generate-motion-transfer", "--shot", "S01_SH01", "--path", str(project_dir)]
    )

    assert result.exit_code == 1
    assert "not found" in result.output


def test_generate_motion_transfer_fails_without_character_reference(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["driving_video"] = {"path": "05_video/reference_clips/dance.mp4"}
    driving_path = project_dir / "05_video" / "reference_clips" / "dance.mp4"
    driving_path.parent.mkdir(parents=True, exist_ok=True)
    driving_path.write_bytes(b"FAKE-MP4")
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)
    _approve(project_dir)

    result = runner.invoke(
        app, ["generate-motion-transfer", "--shot", "S01_SH01", "--path", str(project_dir)]
    )

    assert result.exit_code == 1
    assert "characters[0]" in result.output


def test_generate_motion_transfer_succeeds_with_driving_video_and_reference(
    tmp_path: Path, monkeypatch
):
    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["driving_video"] = {"path": "05_video/reference_clips/dance.mp4"}
    driving_path = project_dir / "05_video" / "reference_clips" / "dance.mp4"
    driving_path.parent.mkdir(parents=True, exist_ok=True)
    driving_path.write_bytes(b"FAKE-MP4")
    ref_path = project_dir / "assets" / "characters" / "girl" / "reference.png"
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_bytes(b"FAKE-PNG")
    shot["characters"] = [{"name": "girl", "reference": "assets/characters/girl/reference.png"}]
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)
    _approve(project_dir)

    provider = _RecordingVideoProvider()  # already defined in this file; records whatever
    # request object it's given, matching the pattern generate-video's own tests use
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(
        app, ["generate-motion-transfer", "--shot", "S01_SH01", "--path", str(project_dir)]
    )

    assert result.exit_code == 0, result.output
    request = provider.requests[-1]
    assert request.image_path == str(ref_path)
    assert request.driving_video_path == str(driving_path)
    assert request.character_orientation == "video"


def test_generate_motion_transfer_uses_config_default_when_config_predates_feature(
    tmp_path: Path, monkeypatch
):
    """A config.json written before this feature shipped has no
    providers.motion_transfer key at all — _stage_config's default=
    fallback must produce the documented default instead of KeyError."""
    project_dir = _init_mock_project(tmp_path)
    config = json.loads((project_dir / "config.json").read_text())
    del config["providers"]["motion_transfer"]
    (project_dir / "config.json").write_text(json.dumps(config))
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["driving_video"] = {"path": "05_video/reference_clips/dance.mp4"}
    driving_path = project_dir / "05_video" / "reference_clips" / "dance.mp4"
    driving_path.parent.mkdir(parents=True, exist_ok=True)
    driving_path.write_bytes(b"FAKE-MP4")
    ref_path = project_dir / "assets" / "characters" / "girl" / "reference.png"
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_bytes(b"FAKE-PNG")
    shot["characters"] = [{"name": "girl", "reference": "assets/characters/girl/reference.png"}]
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)
    _approve(project_dir)
    provider = _RecordingVideoProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(
        app, ["generate-motion-transfer", "--shot", "S01_SH01", "--path", str(project_dir)]
    )

    assert result.exit_code == 0, result.output
    assert provider.requests[-1].model == "kling-motion-control"


def test_stage_config_still_raises_keyerror_for_unknown_stage_with_no_default(tmp_path: Path):
    """Confirms the default= extension doesn't weaken _stage_config's
    existing behavior for the 6 pre-existing stages."""
    from ai_film.cli import _stage_config

    project_dir = _init_mock_project(tmp_path)
    with pytest.raises(KeyError):
        _stage_config(project_dir, "not_a_real_stage")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_cli_generation_commands.py -v -k "motion_transfer or stage_config_still_raises"`
Expected: FAIL — `RuntimeError: No such command 'generate-motion-transfer'`.

- [ ] **Step 3: Implement**

Add `generate_motion_transfer as generate_motion_transfer_service` to the existing `from ai_film.services.generation_service import (...)` block (`src/ai_film/cli.py:30-37`).

Modify `_stage_config` (`src/ai_film/cli.py:137-139`):

```python
def _stage_config(path: Path, stage: str, default: dict | None = None) -> dict:
    config = json.loads((path / "config.json").read_text())
    stage_config = config["providers"].get(stage, default)
    if stage_config is None:
        raise KeyError(stage)
    return stage_config, config["generation"]
```

Add a module-level constant near `_DEFAULT_TARGET_RESOLUTION`/`_DEFAULT_TARGET_FPS` (`src/ai_film/cli.py:142-143`):

```python
_MOTION_TRANSFER_DEFAULT_CONFIG = {
    "provider": "fal", "model": "kling-motion-control", "parameters": {},
}
```

Add, immediately after `generate_lipsync_cmd` (`src/ai_film/cli.py:484-518`):

```python
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
```

Note: no `.claude/settings.json` change — `generate-motion-transfer` is a spend command and deliberately stays un-pre-authorized, matching `generate-video`/`generate-lipsync`'s existing defense-in-depth posture. Do not add it to the allow-list.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_cli_generation_commands.py -v -k "motion_transfer or stage_config_still_raises"`
Expected: PASS, all 6 new tests.

- [ ] **Step 5: Run the full suite**

Run: `./.venv/bin/pytest -q`
Expected: 424 passed (418 + 6 new), 0 failed.

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/cli.py tests/test_cli_generation_commands.py
git commit -m "feat: add generate-motion-transfer CLI command"
```

---

### Task 7: Documentation

**Files:**
- Modify: `README.md`
- Modify: `README.zh-TW.md`
- Modify: `.claude/agents/ai-film-media.md`

**Interfaces:**
- Consumes: everything from Tasks 1-6 (this task only documents already-shipped behavior).
- Produces: no code interfaces — documentation only.

- [ ] **Step 1: Update README.md**

In the "Command reference" section, after the existing `generate-video`/`generate-lipsync`/format-contract paragraphs, add:

```markdown
`generate-motion-transfer --shot <id> [--force]` generates a shot's video by retargeting
a driving reference video's motion onto the shot's locked character reference image
(`kling-video/v2.6/motion-control`), instead of prompt-driven text-to-video — useful when
a text description can't reliably reproduce a specific, precise, counted choreography
(fal.ai's own text-to-video models tend to collapse repetitive micro-gestures into a
generic approximation). Reads both inputs from the shot's own `shot.json` fields, no CLI
flags: `driving_video.path` (`{"driving_video": {"path": "05_video/reference_clips/
dance.mp4"}}`, project-relative like every other reference path) and `characters[0]
.reference` (the shot's first character's locked reference image — motion-transfer is
scoped to a single driving character). Writes into the same `generation.video` artifact
slot `generate-video` uses, with the same idempotent/`--force` semantics — `render`,
`generate-lipsync`, and `mux-audio` all pick it up automatically with no changes needed
there. The generated clip is always silent (`keep_original_sound` is forced off) — attach
dialogue/sfx/music afterward the normal way, via `generate-lipsync`/`mux-audio`.
```

In the "Project layout" section, no changes needed — `driving_video` reference clips live wherever a project chooses to put them (the worked example uses `05_video/reference_clips/`, but nothing enforces that specific path).

- [ ] **Step 2: Update README.zh-TW.md**

Add the equivalent Traditional Chinese paragraph in the same location (`## 指令參考` section, after the existing `generate-video --continue-from-previous`/format-contract paragraphs), translating the README.md addition from Step 1 — commands, flags, JSON, and field names stay verbatim in English/as-is, matching this file's existing translation convention (see the rest of the file for style: prose translated, code/commands untouched).

- [ ] **Step 3: Update `.claude/agents/ai-film-media.md`**

Read the file in full first to match its established prose style and structure (Steps 0-2 plus the human-in-the-loop protocol) before editing. Add a short paragraph near Step 2's generation instructions (after the existing `generate-video --continue-from-previous` guidance, if present, or near the main `generate-video`/`generate-voice` ordering paragraph) noting that `generate-motion-transfer` exists as an alternative to `generate-video` for a shot, but is opt-in and human-directed only — the agent should never switch a shot to motion-transfer on its own initiative, only when the human has explicitly supplied (or asked for) a driving reference clip for that shot and set `driving_video.path` accordingly. Keep this addition light — a few sentences, not a rewrite of Step 2's existing flow, consistent with the spec's non-goal of not deeply rewiring the media agent's default behavior for a still-unproven, human-opt-in-only capability.

- [ ] **Step 4: Run the full suite one final time**

Run: `./.venv/bin/pytest -q`
Expected: 424 passed, 0 failed (docs-only task, no test count change).

- [ ] **Step 5: Commit**

```bash
git add README.md README.zh-TW.md .claude/agents/ai-film-media.md
git commit -m "docs: document generate-motion-transfer command"
```

---

## Final Verification

After Task 7's commit, run the full suite one more time from a clean state and confirm the count:

```bash
./.venv/bin/pytest -q
```

Expected: 424 passed, 0 failed, 0 skipped-that-shouldn't-be (the `ffprobe`-gated `_probe_duration` test in Task 3 only skips if `ffprobe` genuinely isn't on `PATH` — confirm `which ffprobe` succeeds in the environment these tests run in before treating any skip as acceptable).

Confirm `git status --porcelain` shows nothing under `one-more-life/`, `hot-&-fierce-villain/`, or `.env` across every commit in this plan (`git log --stat` for each of Tasks 1-7's commits) — this plan should only ever have touched `src/ai_film/`, `tests/`, `README.md`, `README.zh-TW.md`, and `.claude/agents/ai-film-media.md`.

Per the spec's "Open risk, flagged not resolved": treat the first real (paid) `generate-motion-transfer` call against `kling-motion-control` as a deliberate verification pass, not an assumed success — specifically check whether the output's aspect ratio actually follows the resized reference image as closely as `h3-max`'s documented behavior does, since that assumption is unverified against a real generation. Also run the spec's shot-to-shot continuity end-to-end validation (a real `generate-motion-transfer` shot immediately followed by a real `generate-video --continue-from-previous` shot) once both a driving clip and a locked character reference are available in a real project.
