# AI Film Studio — Core Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the standalone, provider-independent `ai_film` Python package and its `ai-film` CLI — schema, provider abstraction, job/retry engine, cost gate, generation service, project scaffolding, fal.ai backend, and rendering — so it passes the v1 acceptance checklist end-to-end using mock providers, with zero Claude Code or MCP dependency.

**Architecture:** Layered: CLI → services (cost gate + job manager) → provider Protocols (`ImageProvider`/`VideoProvider`/`AudioProvider`) → `Fal*Provider` (real) / `Mock*Provider` (tests). `shot.json` is the canonical per-shot contract (current state); `99_logs/` is immutable execution history. Every provider call is an async job (`submit`/`poll`/`get_result`), never blocking.

**Tech Stack:** Python 3.11+, `typer` (CLI), `jsonschema` (schema validation), `requests` (fal.ai HTTP), `pytest` (tests), `ffmpeg` (external binary, invoked via subprocess for rendering).

**Spec:** `docs/superpowers/specs/2026-08-18-ai-film-studio-design.md` (architecture frozen) — this plan implements §3–§12 of that spec for the Core Engine only. The Claude Code Skill/Agents/Commands layer (§3's `.claude/` tree, §8's agent roles) is a separate follow-up plan, written after this one ships, since agent instructions need this CLI's real flags to reference.

## Global Constraints

- No MCP dependency anywhere in `src/ai_film/` — every provider call is direct HTTP/SDK, per spec §13.
- Every provider call is async (`submit` → `poll` → `get_result`); no blocking generation calls, per spec §5.
- `shot.json`'s top-level `status` field is service-managed derived state — only `shot_store.save_shot()` may compute/write it; no other code path sets it directly, per spec §4.
- Any provider call that may incur external cost must pass the Cost Gate (`approval.is_approved`) first; only `Mock*Provider` calls are exempt, per spec §6.
- All generation is idempotent by default (skip `completed` stages unless `force=True`) and resumable, per spec §7, §10.
- Logs written to `99_logs/` must have secret-shaped values (`Authorization`, `api_key`, `token`, `secret`, signed URLs) redacted before persistence, per spec §10.
- `ai-film render` runs preflight validation before invoking `ffmpeg`, per spec §11.
- Unit tests never make real network calls — `Fal*Provider` tests mock HTTP; real-provider testing is manual only, per spec §12.
- Batch/parallel generation (`generate-all`) is bounded by `config.generation.max_parallel_jobs`, per spec §10.

**Deviation from spec §3's illustrative file tree (implementation detail, not architecture):** the spec sketches `services/image_service.py`, `services/video_service.py`, `services/audio_service.py` as separate files. This plan consolidates them into one `services/generation_service.py` with a single generic `run_generation_stage()` engine and five thin per-capability wrappers, since the three files would otherwise duplicate identical cost-gate/retry/logging/idempotency logic — DRY takes priority over matching the sketch exactly, consistent with the spec's own "architecture frozen, implementation details may evolve" status line.

---

### Task 1: Project scaffolding & CLI skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `src/ai_film/__init__.py`
- Create: `src/ai_film/cli.py`
- Test: `tests/test_cli_skeleton.py`

**Interfaces:**
- Produces: `ai_film.cli.app` (a `typer.Typer` instance), console script `ai-film`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_skeleton.py
from typer.testing import CliRunner

from ai_film.cli import app

runner = CliRunner()


def test_help_lists_program_name():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ai-film" in result.output.lower() or "ai_film" in result.output.lower()


def test_version_command_prints_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_skeleton.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_film'`

- [ ] **Step 3: Write minimal implementation**

```toml
# pyproject.toml
[project]
name = "ai-film"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "typer>=0.12",
    "jsonschema>=4.20",
    "requests>=2.31",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
ai-film = "ai_film.cli:app"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]
```

```python
# src/ai_film/__init__.py
__version__ = "0.1.0"
```

```python
# src/ai_film/cli.py
import typer

from ai_film import __version__

app = typer.Typer(name="ai-film", help="AI Film Studio production engine.")


@app.command()
def version() -> None:
    """Print the ai-film package version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Install the package in editable mode and run tests**

Run: `pip install -e ".[dev]" && pytest tests/test_cli_skeleton.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/ai_film/__init__.py src/ai_film/cli.py tests/test_cli_skeleton.py
git commit -m "feat: scaffold ai_film package and ai-film CLI entrypoint"
```

---

### Task 2: Core models, provider protocols, and mock providers

**Files:**
- Create: `src/ai_film/errors.py`
- Create: `src/ai_film/models.py`
- Create: `src/ai_film/providers/__init__.py`
- Create: `src/ai_film/providers/base.py`
- Create: `src/ai_film/providers/mock/__init__.py`
- Create: `src/ai_film/providers/mock/image.py`
- Create: `src/ai_film/providers/mock/video.py`
- Create: `src/ai_film/providers/mock/audio.py`
- Test: `tests/providers/test_mock_providers.py`

**Interfaces:**
- Produces (`ai_film.errors`): `ProviderError(Exception)`, `CostGateError(Exception)`.
- Produces (`ai_film.models`): `JobStatus` (enum: `QUEUED`, `RUNNING`, `COMPLETED`, `FAILED`), `Capability` (enum: `IMAGE`, `VIDEO`, `VOICE`, `SFX`, `MUSIC`), `GenerationJob(provider: str, id: str, capability: Capability)`, `ModelInfo(provider: str, model: str, capability: Capability, display_name: str)`, `ImageGenerationRequest(prompt, model, reference_paths, output_path)`, `ImageGenerationResult(artifact_path, size_bytes)`, `VideoGenerationRequest(prompt, model, reference_paths, duration_seconds, output_path)`, `VideoGenerationResult(artifact_path, size_bytes, duration_seconds)`, `VoiceGenerationRequest(text, model, speaker, output_path)`, `SfxGenerationRequest(prompt, model, output_path)`, `MusicGenerationRequest(prompt, model, duration_seconds, output_path)`, `AudioGenerationResult(artifact_path, size_bytes, duration_seconds)`.
- Produces (`ai_film.providers.base`): `ImageProvider`, `VideoProvider`, `AudioProvider`, `ProviderCatalog` (all `typing.Protocol`).
- Produces (`ai_film.providers.mock.image.MockImageProvider(fail_first_n_submits: int = 0, polls_until_complete: int = 1)`), `mock.video.MockVideoProvider` (same signature), `mock.audio.MockAudioProvider` (same signature) — each writes a real placeholder file to `request.output_path` on `get_result()`.
- Consumes: nothing (foundational task).

- [ ] **Step 1: Write the failing test**

```python
# tests/providers/test_mock_providers.py
from pathlib import Path

import pytest

from ai_film.errors import ProviderError
from ai_film.models import (
    ImageGenerationRequest,
    JobStatus,
    VoiceGenerationRequest,
)
from ai_film.providers.mock.image import MockImageProvider
from ai_film.providers.mock.audio import MockAudioProvider


def test_mock_image_provider_completes_and_writes_artifact(tmp_path: Path):
    provider = MockImageProvider()
    output_path = tmp_path / "shot.png"
    request = ImageGenerationRequest(
        prompt="a girl in a corridor", model="nano-banana",
        reference_paths=[], output_path=str(output_path),
    )
    job = provider.submit(request)
    assert job.provider == "mock"
    status = provider.poll(job)
    assert status == JobStatus.COMPLETED
    result = provider.get_result(job)
    assert result.artifact_path == str(output_path)
    assert output_path.exists()
    assert result.size_bytes > 0


def test_mock_image_provider_simulates_submit_failures(tmp_path: Path):
    provider = MockImageProvider(fail_first_n_submits=2)
    request = ImageGenerationRequest(
        prompt="x", model="nano-banana", reference_paths=[],
        output_path=str(tmp_path / "shot.png"),
    )
    with pytest.raises(ProviderError):
        provider.submit(request)
    with pytest.raises(ProviderError):
        provider.submit(request)
    job = provider.submit(request)  # third call succeeds
    assert provider.poll(job) == JobStatus.COMPLETED


def test_mock_audio_provider_submit_voice(tmp_path: Path):
    provider = MockAudioProvider()
    request = VoiceGenerationRequest(
        text="你終於來了。", model="csm-1b", speaker="girl",
        output_path=str(tmp_path / "dialogue.wav"),
    )
    job = provider.submit_voice(request)
    assert provider.poll(job) == JobStatus.COMPLETED
    result = provider.get_result(job)
    assert Path(result.artifact_path).exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/providers/test_mock_providers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_film.providers'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ai_film/errors.py
class ProviderError(Exception):
    """Raised when a provider call (submit/poll/get_result) fails."""


class CostGateError(Exception):
    """Raised when generation is attempted without required approval."""
```

```python
# src/ai_film/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Capability(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    VOICE = "voice"
    SFX = "sfx"
    MUSIC = "music"


@dataclass(frozen=True)
class GenerationJob:
    provider: str
    id: str
    capability: Capability


@dataclass(frozen=True)
class ModelInfo:
    provider: str
    model: str
    capability: Capability
    display_name: str


@dataclass
class ImageGenerationRequest:
    prompt: str
    model: str
    reference_paths: list[str] = field(default_factory=list)
    output_path: str = ""


@dataclass
class ImageGenerationResult:
    artifact_path: str
    size_bytes: int


@dataclass
class VideoGenerationRequest:
    prompt: str
    model: str
    reference_paths: list[str] = field(default_factory=list)
    duration_seconds: float = 5.0
    output_path: str = ""


@dataclass
class VideoGenerationResult:
    artifact_path: str
    size_bytes: int
    duration_seconds: float


@dataclass
class VoiceGenerationRequest:
    text: str
    model: str
    speaker: str = ""
    output_path: str = ""


@dataclass
class SfxGenerationRequest:
    prompt: str
    model: str
    output_path: str = ""


@dataclass
class MusicGenerationRequest:
    prompt: str
    model: str
    duration_seconds: float = 30.0
    output_path: str = ""


@dataclass
class AudioGenerationResult:
    artifact_path: str
    size_bytes: int
    duration_seconds: float
```

```python
# src/ai_film/providers/__init__.py
```

```python
# src/ai_film/providers/base.py
from __future__ import annotations

from typing import Protocol

from ai_film.models import (
    AudioGenerationResult,
    Capability,
    GenerationJob,
    ImageGenerationRequest,
    ImageGenerationResult,
    JobStatus,
    ModelInfo,
    MusicGenerationRequest,
    SfxGenerationRequest,
    VideoGenerationRequest,
    VideoGenerationResult,
    VoiceGenerationRequest,
)


class ImageProvider(Protocol):
    def submit(self, request: ImageGenerationRequest) -> GenerationJob: ...
    def poll(self, job: GenerationJob) -> JobStatus: ...
    def get_result(self, job: GenerationJob) -> ImageGenerationResult: ...


class VideoProvider(Protocol):
    def submit(self, request: VideoGenerationRequest) -> GenerationJob: ...
    def poll(self, job: GenerationJob) -> JobStatus: ...
    def get_result(self, job: GenerationJob) -> VideoGenerationResult: ...


class AudioProvider(Protocol):
    def submit_voice(self, request: VoiceGenerationRequest) -> GenerationJob: ...
    def submit_sfx(self, request: SfxGenerationRequest) -> GenerationJob: ...
    def submit_music(self, request: MusicGenerationRequest) -> GenerationJob: ...
    def poll(self, job: GenerationJob) -> JobStatus: ...
    def get_result(self, job: GenerationJob) -> AudioGenerationResult: ...


class ProviderCatalog(Protocol):
    def models(self, capability: Capability) -> list[ModelInfo]: ...
```

```python
# src/ai_film/providers/mock/__init__.py
```

```python
# src/ai_film/providers/mock/image.py
from __future__ import annotations

from pathlib import Path

from ai_film.errors import ProviderError
from ai_film.models import (
    Capability,
    GenerationJob,
    ImageGenerationRequest,
    ImageGenerationResult,
    JobStatus,
)


class MockImageProvider:
    """In-memory image provider for tests. No network calls."""

    def __init__(self, fail_first_n_submits: int = 0, polls_until_complete: int = 1):
        self.fail_first_n_submits = fail_first_n_submits
        self.polls_until_complete = polls_until_complete
        self._submit_calls = 0
        self._poll_counts: dict[str, int] = {}
        self._requests: dict[str, ImageGenerationRequest] = {}

    def submit(self, request: ImageGenerationRequest) -> GenerationJob:
        self._submit_calls += 1
        if self._submit_calls <= self.fail_first_n_submits:
            raise ProviderError("simulated submit failure")
        job_id = f"mock-image-{self._submit_calls}"
        self._requests[job_id] = request
        self._poll_counts[job_id] = 0
        return GenerationJob(provider="mock", id=job_id, capability=Capability.IMAGE)

    def poll(self, job: GenerationJob) -> JobStatus:
        self._poll_counts[job.id] += 1
        if self._poll_counts[job.id] >= self.polls_until_complete:
            return JobStatus.COMPLETED
        return JobStatus.RUNNING

    def get_result(self, job: GenerationJob) -> ImageGenerationResult:
        request = self._requests[job.id]
        output_path = Path(request.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"MOCK-PNG-DATA")
        return ImageGenerationResult(
            artifact_path=str(output_path), size_bytes=output_path.stat().st_size
        )
```

```python
# src/ai_film/providers/mock/video.py
from __future__ import annotations

from pathlib import Path

from ai_film.errors import ProviderError
from ai_film.models import (
    Capability,
    GenerationJob,
    JobStatus,
    VideoGenerationRequest,
    VideoGenerationResult,
)


class MockVideoProvider:
    def __init__(self, fail_first_n_submits: int = 0, polls_until_complete: int = 1):
        self.fail_first_n_submits = fail_first_n_submits
        self.polls_until_complete = polls_until_complete
        self._submit_calls = 0
        self._poll_counts: dict[str, int] = {}
        self._requests: dict[str, VideoGenerationRequest] = {}

    def submit(self, request: VideoGenerationRequest) -> GenerationJob:
        self._submit_calls += 1
        if self._submit_calls <= self.fail_first_n_submits:
            raise ProviderError("simulated submit failure")
        job_id = f"mock-video-{self._submit_calls}"
        self._requests[job_id] = request
        self._poll_counts[job_id] = 0
        return GenerationJob(provider="mock", id=job_id, capability=Capability.VIDEO)

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
            duration_seconds=request.duration_seconds,
        )
```

```python
# src/ai_film/providers/mock/audio.py
from __future__ import annotations

from pathlib import Path

from ai_film.errors import ProviderError
from ai_film.models import (
    AudioGenerationResult,
    Capability,
    GenerationJob,
    JobStatus,
    MusicGenerationRequest,
    SfxGenerationRequest,
    VoiceGenerationRequest,
)

AudioRequest = VoiceGenerationRequest | SfxGenerationRequest | MusicGenerationRequest


class MockAudioProvider:
    def __init__(self, fail_first_n_submits: int = 0, polls_until_complete: int = 1):
        self.fail_first_n_submits = fail_first_n_submits
        self.polls_until_complete = polls_until_complete
        self._submit_calls = 0
        self._poll_counts: dict[str, int] = {}
        self._requests: dict[str, AudioRequest] = {}

    def _submit(self, request: AudioRequest) -> GenerationJob:
        self._submit_calls += 1
        if self._submit_calls <= self.fail_first_n_submits:
            raise ProviderError("simulated submit failure")
        job_id = f"mock-audio-{self._submit_calls}"
        self._requests[job_id] = request
        self._poll_counts[job_id] = 0
        return GenerationJob(provider="mock", id=job_id, capability=Capability.VOICE)

    def submit_voice(self, request: VoiceGenerationRequest) -> GenerationJob:
        return self._submit(request)

    def submit_sfx(self, request: SfxGenerationRequest) -> GenerationJob:
        return self._submit(request)

    def submit_music(self, request: MusicGenerationRequest) -> GenerationJob:
        return self._submit(request)

    def poll(self, job: GenerationJob) -> JobStatus:
        self._poll_counts[job.id] += 1
        if self._poll_counts[job.id] >= self.polls_until_complete:
            return JobStatus.COMPLETED
        return JobStatus.RUNNING

    def get_result(self, job: GenerationJob) -> AudioGenerationResult:
        request = self._requests[job.id]
        output_path = Path(request.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"MOCK-AUDIO-DATA")
        duration = getattr(request, "duration_seconds", 2.0)
        return AudioGenerationResult(
            artifact_path=str(output_path),
            size_bytes=output_path.stat().st_size,
            duration_seconds=duration,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/providers/test_mock_providers.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/errors.py src/ai_film/models.py src/ai_film/providers tests/providers/test_mock_providers.py
git commit -m "feat: add core models, provider protocols, and mock providers"
```

---

### Task 3: Job execution engine with retry/backoff

**Files:**
- Create: `src/ai_film/jobs.py`
- Test: `tests/test_jobs.py`

**Interfaces:**
- Consumes: `ai_film.errors.ProviderError`; `ai_film.models.GenerationJob`, `JobStatus`; `ai_film.providers.mock.image.MockImageProvider` (test only).
- Produces: `ai_film.jobs.JobResult` (dataclass: `job: GenerationJob`, `result: Any`, `attempts: int`), `ai_film.jobs.run_job(submit_fn, poll_fn, get_result_fn, max_attempts=3, poll_interval_seconds=0.0, sleep_fn=time.sleep, on_attempt=None) -> JobResult`. Raises `ProviderError` if all attempts are exhausted.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_jobs.py
import pytest

from ai_film.errors import ProviderError
from ai_film.jobs import run_job
from ai_film.models import GenerationJob, Capability, JobStatus


def _job() -> GenerationJob:
    return GenerationJob(provider="mock", id="j1", capability=Capability.IMAGE)


def test_run_job_succeeds_on_first_attempt():
    result = run_job(
        submit_fn=lambda: _job(),
        poll_fn=lambda job: JobStatus.COMPLETED,
        get_result_fn=lambda job: "artifact",
        max_attempts=3,
    )
    assert result.result == "artifact"
    assert result.attempts == 1


def test_run_job_retries_and_succeeds():
    attempts = {"n": 0}

    def submit_fn():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ProviderError("boom")
        return _job()

    result = run_job(
        submit_fn=submit_fn,
        poll_fn=lambda job: JobStatus.COMPLETED,
        get_result_fn=lambda job: "artifact",
        max_attempts=3,
    )
    assert result.attempts == 3


def test_run_job_raises_after_exhausting_attempts():
    def submit_fn():
        raise ProviderError("always fails")

    with pytest.raises(ProviderError):
        run_job(
            submit_fn=submit_fn,
            poll_fn=lambda job: JobStatus.COMPLETED,
            get_result_fn=lambda job: "artifact",
            max_attempts=3,
        )


def test_run_job_calls_on_attempt_for_each_try():
    log = []

    def submit_fn():
        if len(log) < 2:
            raise ProviderError("boom")
        return _job()

    run_job(
        submit_fn=submit_fn,
        poll_fn=lambda job: JobStatus.COMPLETED,
        get_result_fn=lambda job: "artifact",
        max_attempts=3,
        on_attempt=lambda attempt, job, outcome: log.append((attempt, outcome)),
    )
    assert len(log) == 3
    assert log[-1][1] == "completed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_jobs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_film.jobs'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ai_film/jobs.py
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

from ai_film.errors import ProviderError
from ai_film.models import GenerationJob, JobStatus

T = TypeVar("T")


@dataclass
class JobResult(Generic[T]):
    job: GenerationJob
    result: T
    attempts: int


def run_job(
    submit_fn: Callable[[], GenerationJob],
    poll_fn: Callable[[GenerationJob], JobStatus],
    get_result_fn: Callable[[GenerationJob], T],
    max_attempts: int = 3,
    poll_interval_seconds: float = 0.0,
    sleep_fn: Callable[[float], None] = time.sleep,
    on_attempt: Callable[[int, GenerationJob | None, str], None] | None = None,
) -> JobResult[T]:
    last_error = "unknown error"
    for attempt in range(1, max_attempts + 1):
        job: GenerationJob | None = None
        try:
            job = submit_fn()
            status = poll_fn(job)
            while status in (JobStatus.QUEUED, JobStatus.RUNNING):
                sleep_fn(poll_interval_seconds)
                status = poll_fn(job)
            if status == JobStatus.COMPLETED:
                result = get_result_fn(job)
                if on_attempt:
                    on_attempt(attempt, job, "completed")
                return JobResult(job=job, result=result, attempts=attempt)
            last_error = f"job ended with status {status.value}"
        except ProviderError as exc:
            last_error = str(exc)
        if on_attempt:
            on_attempt(attempt, job, f"failed: {last_error}")
    raise ProviderError(f"generation failed after {max_attempts} attempts: {last_error}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_jobs.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/jobs.py tests/test_jobs.py
git commit -m "feat: add job execution engine with retry/backoff"
```

---

### Task 4: shot.json JSON Schema and validator

**Files:**
- Create: `src/ai_film/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `ai_film.schema.SHOT_SCHEMA` (dict), `ai_film.schema.validate_shot(data: dict) -> list[str]` (empty list = valid).
- Consumes: nothing new (foundational).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_schema.py
from ai_film.schema import validate_shot


def _valid_shot() -> dict:
    return {
        "schema_version": "1.0",
        "id": "S01_SH01",
        "status": "draft",
        "duration_seconds": 5,
        "continuity": {"status": "pending", "checked_at": None, "issues": []},
        "generation": {
            "image": {"status": "pending", "attempts": 0},
            "video": {"status": "pending", "attempts": 0},
            "voice": {"status": "pending", "attempts": 0},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }


def test_valid_shot_has_no_errors():
    assert validate_shot(_valid_shot()) == []


def test_missing_required_field_is_reported():
    shot = _valid_shot()
    del shot["schema_version"]
    errors = validate_shot(shot)
    assert any("schema_version" in e for e in errors)


def test_invalid_status_enum_is_reported():
    shot = _valid_shot()
    shot["status"] = "not-a-real-status"
    errors = validate_shot(shot)
    assert len(errors) == 1


def test_invalid_generation_stage_status_is_reported():
    shot = _valid_shot()
    shot["generation"]["image"]["status"] = "bogus"
    errors = validate_shot(shot)
    assert len(errors) == 1


def test_missing_generation_stage_is_reported():
    shot = _valid_shot()
    del shot["generation"]["music"]
    errors = validate_shot(shot)
    assert any("music" in e for e in errors)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_film.schema'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ai_film/schema.py
from __future__ import annotations

import jsonschema

GENERATION_STAGE_SCHEMA = {
    "type": "object",
    "required": ["status"],
    "properties": {
        "status": {
            "enum": [
                "pending", "queued", "running", "completed", "failed", "not_required",
            ]
        },
        "provider": {"type": ["string", "null"]},
        "model": {"type": ["string", "null"]},
        "job": {"type": ["object", "null"]},
        "inputs": {"type": "array"},
        "artifact": {"type": ["object", "null"]},
        "attempts": {"type": "integer"},
        "created_at": {"type": ["string", "null"]},
        "started_at": {"type": ["string", "null"]},
        "completed_at": {"type": ["string", "null"]},
    },
}

SHOT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["schema_version", "id", "status", "duration_seconds", "generation"],
    "properties": {
        "schema_version": {"type": "string"},
        "id": {"type": "string"},
        "status": {"enum": ["draft", "ready", "generating", "completed", "failed"]},
        "duration_seconds": {"type": "number"},
        "characters": {"type": "array"},
        "inputs": {"type": "object"},
        "camera": {"type": "object"},
        "action": {"type": "string"},
        "dialogue": {"type": "object"},
        "visual": {"type": "object"},
        "continuity": {
            "type": "object",
            "required": ["status"],
            "properties": {
                "status": {"enum": ["pending", "passed", "warning", "failed"]},
                "checked_at": {"type": ["string", "null"]},
                "issues": {"type": "array"},
            },
        },
        "generation": {
            "type": "object",
            "required": ["image", "video", "voice", "sfx", "music"],
            "properties": {
                "image": GENERATION_STAGE_SCHEMA,
                "video": GENERATION_STAGE_SCHEMA,
                "voice": GENERATION_STAGE_SCHEMA,
                "sfx": GENERATION_STAGE_SCHEMA,
                "music": GENERATION_STAGE_SCHEMA,
            },
        },
    },
}


def validate_shot(data: dict) -> list[str]:
    validator = jsonschema.Draft7Validator(SHOT_SCHEMA)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    return [
        f"{'.'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_schema.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/schema.py tests/test_schema.py
git commit -m "feat: add shot.json JSON Schema and validator"
```

---

### Task 5: Shot store — load/save with derived status computation

**Files:**
- Create: `src/ai_film/shot_store.py`
- Test: `tests/test_shot_store.py`

**Interfaces:**
- Consumes: `ai_film.schema.validate_shot`.
- Produces: `ai_film.shot_store.REQUIRED_STAGES` (tuple of 5 stage names), `ai_film.shot_store.compute_status(shot: dict) -> str`, `ai_film.shot_store.load_shot(path: Path) -> dict`, `ai_film.shot_store.save_shot(path: Path, shot: dict) -> None` (raises `ValueError` on schema violation; always overwrites `shot["status"]` with `compute_status(shot)` before validating/writing — per Global Constraints, this is the *only* place `status` is written).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shot_store.py
import json
from pathlib import Path

import pytest

from ai_film.shot_store import compute_status, load_shot, save_shot


def _base_shot(**overrides) -> dict:
    shot = {
        "schema_version": "1.0",
        "id": "S01_SH01",
        "status": "draft",
        "duration_seconds": 5,
        "continuity": {"status": "pending", "checked_at": None, "issues": []},
        "generation": {
            "image": {"status": "pending", "attempts": 0},
            "video": {"status": "pending", "attempts": 0},
            "voice": {"status": "pending", "attempts": 0},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }
    shot.update(overrides)
    return shot


def test_compute_status_draft_when_continuity_pending():
    assert compute_status(_base_shot()) == "draft"


def test_compute_status_ready_when_continuity_passed():
    shot = _base_shot()
    shot["continuity"]["status"] = "passed"
    assert compute_status(shot) == "ready"


def test_compute_status_generating_when_a_stage_is_running():
    shot = _base_shot()
    shot["continuity"]["status"] = "passed"
    shot["generation"]["image"]["status"] = "running"
    assert compute_status(shot) == "generating"


def test_compute_status_failed_when_a_stage_failed():
    shot = _base_shot()
    shot["generation"]["video"]["status"] = "failed"
    assert compute_status(shot) == "failed"


def test_compute_status_completed_when_all_required_stages_done():
    shot = _base_shot()
    shot["continuity"]["status"] = "passed"
    for stage in ("image", "video", "voice"):
        shot["generation"][stage]["status"] = "completed"
    assert compute_status(shot) == "completed"


def test_save_shot_overwrites_hand_authored_status(tmp_path: Path):
    shot = _base_shot(status="completed")  # agent incorrectly hand-set this
    path = tmp_path / "SH01.json"
    save_shot(path, shot)
    saved = load_shot(path)
    assert saved["status"] == "draft"  # recomputed, not trusted


def test_save_shot_raises_on_invalid_schema(tmp_path: Path):
    shot = _base_shot()
    del shot["generation"]["music"]
    with pytest.raises(ValueError):
        save_shot(tmp_path / "SH01.json", shot)


def test_load_shot_round_trips(tmp_path: Path):
    path = tmp_path / "SH01.json"
    save_shot(path, _base_shot())
    loaded = load_shot(path)
    assert loaded["id"] == "S01_SH01"
    assert json.loads(path.read_text())["id"] == "S01_SH01"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_shot_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_film.shot_store'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ai_film/shot_store.py
from __future__ import annotations

import json
from pathlib import Path

from ai_film.schema import validate_shot

REQUIRED_STAGES = ("image", "video", "voice", "sfx", "music")
_ACTIVE = ("queued", "running")
_DONE = ("completed", "not_required")


def compute_status(shot: dict) -> str:
    continuity_status = shot.get("continuity", {}).get("status", "pending")
    generation = shot.get("generation", {})
    stage_statuses = [
        generation.get(stage, {}).get("status", "pending") for stage in REQUIRED_STAGES
    ]

    if any(status == "failed" for status in stage_statuses):
        return "failed"
    if any(status in _ACTIVE for status in stage_statuses):
        return "generating"
    if all(status in _DONE for status in stage_statuses):
        return "completed"
    if continuity_status == "passed":
        return "ready"
    return "draft"


def load_shot(path: Path) -> dict:
    return json.loads(path.read_text())


def save_shot(path: Path, shot: dict) -> None:
    shot = dict(shot)
    shot["status"] = compute_status(shot)
    errors = validate_shot(shot)
    if errors:
        raise ValueError(f"invalid shot.json for {shot.get('id')}: {errors}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(shot, indent=2, ensure_ascii=False))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_shot_store.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/shot_store.py tests/test_shot_store.py
git commit -m "feat: add shot store with service-managed derived status"
```

---

### Task 6: Logging store — redaction and immutable attempt/approval logs

**Files:**
- Create: `src/ai_film/logging_store.py`
- Test: `tests/test_logging_store.py`

**Interfaces:**
- Produces: `ai_film.logging_store.redact(value: Any) -> Any`, `ai_film.logging_store.write_attempt_log(project_dir: Path, shot_id: str, stage: str, attempt: int, job: dict | None, request: dict, response: dict, outcome: str) -> Path`, `ai_film.logging_store.write_approval_log(project_dir: Path, scope: str, target_ids: list[str], estimated_cost: float | None) -> Path`.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_logging_store.py
import json
from pathlib import Path

from ai_film.logging_store import redact, write_approval_log, write_attempt_log


def test_redact_masks_secret_shaped_keys():
    payload = {
        "model": "veo-3",
        "headers": {"Authorization": "Bearer xyz", "Content-Type": "json"},
        "api_key": "sk-123",
        "nested": {"signed_url": "https://example.com/tmp?sig=abc"},
    }
    redacted = redact(payload)
    assert redacted["headers"]["Authorization"] == "[REDACTED]"
    assert redacted["headers"]["Content-Type"] == "json"
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["nested"]["signed_url"] == "[REDACTED]"
    assert redacted["model"] == "veo-3"


def test_write_attempt_log_creates_per_shot_file_with_attempt_and_job(tmp_path: Path):
    log_path = write_attempt_log(
        project_dir=tmp_path,
        shot_id="S01_SH07",
        stage="image",
        attempt=2,
        job={"provider": "fal", "id": "j2"},
        request={"model": "nano-banana", "api_key": "secret"},
        response={"status": "completed"},
        outcome="completed",
    )
    assert log_path.exists()
    assert log_path.parent == tmp_path / "99_logs" / "S01_SH07"
    data = json.loads(log_path.read_text())
    assert data["attempt"] == 2
    assert data["job"] == {"provider": "fal", "id": "j2"}
    assert data["request"]["api_key"] == "[REDACTED]"


def test_write_approval_log_creates_file_under_approvals(tmp_path: Path):
    log_path = write_approval_log(
        project_dir=tmp_path,
        scope="storyboard",
        target_ids=["S01_SH01", "S01_SH02"],
        estimated_cost=4.82,
    )
    assert log_path.parent == tmp_path / "99_logs" / "approvals"
    data = json.loads(log_path.read_text())
    assert data["scope"] == "storyboard"
    assert data["target_ids"] == ["S01_SH01", "S01_SH02"]
    assert data["estimated_cost"] == 4.82
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_logging_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_film.logging_store'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ai_film/logging_store.py
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REDACT_KEY_PATTERN = re.compile(
    r"(authorization|api[_-]?key|token|secret|signed[_-]?url)", re.IGNORECASE
)
_REDACTED = "[REDACTED]"


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _REDACTED if _REDACT_KEY_PATTERN.search(key) else redact(val)
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def write_attempt_log(
    project_dir: Path,
    shot_id: str,
    stage: str,
    attempt: int,
    job: dict | None,
    request: dict,
    response: dict,
    outcome: str,
) -> Path:
    log_dir = project_dir / "99_logs" / shot_id
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{_timestamp()}_{stage}_attempt{attempt:02d}.json"
    log_path.write_text(
        json.dumps(
            {
                "attempt": attempt,
                "stage": stage,
                "job": job,
                "outcome": outcome,
                "request": redact(request),
                "response": redact(response),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return log_path


def write_approval_log(
    project_dir: Path,
    scope: str,
    target_ids: list[str],
    estimated_cost: float | None,
) -> Path:
    log_dir = project_dir / "99_logs" / "approvals"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{_timestamp()}_generation_approved.json"
    log_path.write_text(
        json.dumps(
            {
                "scope": scope,
                "target_ids": target_ids,
                "estimated_cost": estimated_cost,
                "approved_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return log_path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_logging_store.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/logging_store.py tests/test_logging_store.py
git commit -m "feat: add logging store with secret redaction"
```

---

### Task 7: Approval / cost gate core

**Files:**
- Create: `src/ai_film/approval.py`
- Test: `tests/test_approval.py`

**Interfaces:**
- Consumes: `ai_film.logging_store.write_approval_log`.
- Produces: `ai_film.approval.VALID_SCOPES = ("bibles", "storyboard")`, `ai_film.approval.load_config(project_dir: Path) -> dict`, `ai_film.approval.save_config(project_dir: Path, config: dict) -> None`, `ai_film.approval.approve_generation(project_dir: Path, scope: str, target_ids: list[str], estimated_cost: float | None = None, revision: int | None = None) -> dict` (raises `ValueError` for unknown scope), `ai_film.approval.is_approved(project_dir: Path, scope: str, target_id: str) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_approval.py
import json
from pathlib import Path

import pytest

from ai_film.approval import approve_generation, is_approved, load_config


def _init_config(project_dir: Path) -> None:
    (project_dir / "config.json").write_text(json.dumps({"providers": {}, "generation": {}}))


def test_is_approved_false_before_any_approval(tmp_path: Path):
    _init_config(tmp_path)
    assert is_approved(tmp_path, "storyboard", "S01_SH01") is False


def test_approve_generation_marks_listed_targets_approved(tmp_path: Path):
    _init_config(tmp_path)
    approve_generation(tmp_path, "storyboard", ["S01_SH01", "S01_SH02"], estimated_cost=4.82)
    assert is_approved(tmp_path, "storyboard", "S01_SH01") is True
    assert is_approved(tmp_path, "storyboard", "S01_SH02") is True


def test_approve_generation_does_not_cover_ids_outside_snapshot(tmp_path: Path):
    _init_config(tmp_path)
    approve_generation(tmp_path, "storyboard", ["S01_SH01"], estimated_cost=1.0)
    assert is_approved(tmp_path, "storyboard", "S01_SH02") is False


def test_bibles_and_storyboard_scopes_are_independent(tmp_path: Path):
    _init_config(tmp_path)
    approve_generation(tmp_path, "bibles", ["char:girl"], estimated_cost=0.4)
    assert is_approved(tmp_path, "bibles", "char:girl") is True
    assert is_approved(tmp_path, "storyboard", "S01_SH01") is False


def test_approve_generation_rejects_unknown_scope(tmp_path: Path):
    _init_config(tmp_path)
    with pytest.raises(ValueError):
        approve_generation(tmp_path, "not-a-scope", ["x"])


def test_approve_generation_writes_immutable_approval_log(tmp_path: Path):
    _init_config(tmp_path)
    approve_generation(tmp_path, "storyboard", ["S01_SH01"], estimated_cost=1.0)
    approvals_dir = tmp_path / "99_logs" / "approvals"
    assert len(list(approvals_dir.glob("*.json"))) == 1


def test_config_reflects_current_approval_state(tmp_path: Path):
    _init_config(tmp_path)
    approve_generation(tmp_path, "storyboard", ["S01_SH01"], estimated_cost=1.0, revision=1)
    config = load_config(tmp_path)
    record = config["generation_approval"]["storyboard"]
    assert record["approved"] is True
    assert record["scope"]["target_ids"] == ["S01_SH01"]
    assert record["scope"]["revision"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_approval.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_film.approval'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ai_film/approval.py
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ai_film.logging_store import write_approval_log

VALID_SCOPES = ("bibles", "storyboard")


def _config_path(project_dir: Path) -> Path:
    return project_dir / "config.json"


def load_config(project_dir: Path) -> dict:
    return json.loads(_config_path(project_dir).read_text())


def save_config(project_dir: Path, config: dict) -> None:
    _config_path(project_dir).write_text(json.dumps(config, indent=2, ensure_ascii=False))


def approve_generation(
    project_dir: Path,
    scope: str,
    target_ids: list[str],
    estimated_cost: float | None = None,
    revision: int | None = None,
) -> dict:
    if scope not in VALID_SCOPES:
        raise ValueError(f"unknown approval scope: {scope!r}, must be one of {VALID_SCOPES}")

    config = load_config(project_dir)
    scope_record: dict = {
        "type": "bibles" if scope == "bibles" else "storyboard_revision",
        "target_ids": list(target_ids),
    }
    if scope == "storyboard":
        scope_record["revision"] = revision or 1

    config.setdefault("generation_approval", {})[scope] = {
        "approved": True,
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "scope": scope_record,
        "estimated_cost": estimated_cost,
    }
    save_config(project_dir, config)
    write_approval_log(project_dir, scope, list(target_ids), estimated_cost)
    return config["generation_approval"][scope]


def is_approved(project_dir: Path, scope: str, target_id: str) -> bool:
    config = load_config(project_dir)
    record = config.get("generation_approval", {}).get(scope)
    if not record or not record.get("approved"):
        return False
    return target_id in record.get("scope", {}).get("target_ids", [])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_approval.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/approval.py tests/test_approval.py
git commit -m "feat: add scoped, snapshotted cost-gate approval"
```

---

### Task 8: Generation service — cost gate + idempotency + retries + logging

**Files:**
- Create: `src/ai_film/services/__init__.py`
- Create: `src/ai_film/services/generation_service.py`
- Test: `tests/services/test_generation_service.py`

**Interfaces:**
- Consumes: `ai_film.errors.{CostGateError, ProviderError}`; `ai_film.jobs.run_job`; `ai_film.approval.is_approved`; `ai_film.shot_store.{load_shot, save_shot}`; `ai_film.logging_store.write_attempt_log`; `ai_film.models.{ImageGenerationRequest, VideoGenerationRequest, VoiceGenerationRequest, SfxGenerationRequest, MusicGenerationRequest}`; `ai_film.providers.mock.image.MockImageProvider` (test only).
- Produces: `ai_film.services.generation_service.run_generation_stage(project_dir, shot_path, stage, scope, submit_fn, poll_fn, get_result_fn, result_to_artifact, provider_name, model_name, max_attempts, poll_interval_seconds, force=False) -> dict` (returns the updated `generation.<stage>` dict; raises `CostGateError` if not approved), and five thin wrappers with this exact shape:
  - `generate_image(project_dir, shot_path, provider: ImageProvider, prompt, model, reference_paths, output_path, provider_name, max_attempts=3, poll_interval_seconds=0.0, force=False) -> dict`
  - `generate_video(...)` (same shape, `provider: VideoProvider`, adds `duration_seconds`)
  - `generate_voice(...)`, `generate_sfx(...)`, `generate_music(...)` (`provider: AudioProvider`)

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_generation_service.py
from pathlib import Path

import pytest

from ai_film.approval import approve_generation
from ai_film.errors import CostGateError
from ai_film.providers.mock.image import MockImageProvider
from ai_film.services.generation_service import generate_image
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
    with pytest.raises(CostGateError):
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
    assert Path(stage["artifact"]["path"]).exists()

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/services/test_generation_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_film.services'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ai_film/services/__init__.py
```

```python
# src/ai_film/services/generation_service.py
from __future__ import annotations

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
    except ProviderError:
        shot["generation"][stage] = {
            **stage_data,
            "provider": provider_name,
            "model": model_name,
            "status": "failed",
            "attempts": max_attempts,
        }
        save_shot(shot_path, shot)
        raise

    artifact = result_to_artifact(job_result.result)
    shot["generation"][stage] = {
        "provider": provider_name,
        "model": model_name,
        "status": "completed",
        "job": {"provider": job_result.job.provider, "id": job_result.job.id},
        "inputs": stage_data.get("inputs", []),
        "artifact": artifact,
        "attempts": job_result.attempts,
    }
    save_shot(shot_path, shot)
    return shot["generation"][stage]


def _image_artifact(result) -> dict:
    return {"path": result.artifact_path, "size_bytes": result.size_bytes, "sha256": None}


def _video_or_audio_artifact(result) -> dict:
    return {
        "path": result.artifact_path,
        "size_bytes": result.size_bytes,
        "sha256": None,
        "duration_seconds": result.duration_seconds,
    }


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
) -> dict:
    request = ImageGenerationRequest(
        prompt=prompt, model=model, reference_paths=reference_paths,
        output_path=str(output_path),
    )
    return run_generation_stage(
        project_dir=project_dir, shot_path=shot_path, stage="image",
        scope=_SCOPE_BY_STAGE["image"],
        submit_fn=lambda: provider.submit(request),
        poll_fn=provider.poll, get_result_fn=provider.get_result,
        result_to_artifact=_image_artifact,
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
) -> dict:
    request = VideoGenerationRequest(
        prompt=prompt, model=model, reference_paths=reference_paths,
        duration_seconds=duration_seconds, output_path=str(output_path),
    )
    return run_generation_stage(
        project_dir=project_dir, shot_path=shot_path, stage="video",
        scope=_SCOPE_BY_STAGE["video"],
        submit_fn=lambda: provider.submit(request),
        poll_fn=provider.poll, get_result_fn=provider.get_result,
        result_to_artifact=_video_or_audio_artifact,
        provider_name=provider_name, model_name=model,
        max_attempts=max_attempts, poll_interval_seconds=poll_interval_seconds,
        force=force,
    )


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
) -> dict:
    request = VoiceGenerationRequest(
        text=text, model=model, speaker=speaker, output_path=str(output_path)
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
    output_path: Path,
    provider_name: str,
    max_attempts: int = 3,
    poll_interval_seconds: float = 0.0,
    force: bool = False,
) -> dict:
    request = SfxGenerationRequest(prompt=prompt, model=model, output_path=str(output_path))
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/services/test_generation_service.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/services tests/services/test_generation_service.py
git commit -m "feat: add generation service with cost gate, idempotency, and retries"
```

---

### Task 9: Project scaffolding (`ai-film init`'s backing logic)

**Files:**
- Create: `src/ai_film/project.py`
- Test: `tests/test_project.py`

**Interfaces:**
- Produces: `ai_film.project.PROJECT_DIRS` (tuple of relative dir paths), `ai_film.project.DEFAULT_CONFIG` (dict), `ai_film.project.init_project(project_dir: Path, title: str) -> Path`. Never overwrites an existing `config.json`.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_project.py
import json
from pathlib import Path

from ai_film.project import PROJECT_DIRS, init_project


def test_init_project_creates_all_directories(tmp_path: Path):
    project_dir = tmp_path / "project"
    init_project(project_dir, title="The Last Ship")
    for rel in PROJECT_DIRS:
        assert (project_dir / rel).is_dir(), f"missing {rel}"


def test_init_project_writes_default_config(tmp_path: Path):
    project_dir = tmp_path / "project"
    init_project(project_dir, title="The Last Ship")
    config = json.loads((project_dir / "config.json").read_text())
    assert config["title"] == "The Last Ship"
    assert "image" in config["providers"]
    assert config["generation"]["max_attempts"] == 3
    assert config["generation_approval"] == {}


def test_init_project_does_not_overwrite_existing_config(tmp_path: Path):
    project_dir = tmp_path / "project"
    init_project(project_dir, title="The Last Ship")
    config_path = project_dir / "config.json"
    config = json.loads(config_path.read_text())
    config["generation"]["max_attempts"] = 7
    config_path.write_text(json.dumps(config))

    init_project(project_dir, title="The Last Ship")  # re-init

    reloaded = json.loads(config_path.read_text())
    assert reloaded["generation"]["max_attempts"] == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_project.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_film.project'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ai_film/project.py
from __future__ import annotations

import json
from pathlib import Path

PROJECT_DIRS = (
    "assets/characters", "assets/environments", "assets/props",
    "assets/reference-images", "assets/fonts",
    "00_story", "01_bibles", "02_scenes", "03_shots",
    "04_storyboard", "05_video",
    "06_audio/dialogue", "06_audio/sfx", "06_audio/music",
    "final", "99_logs",
)

DEFAULT_CONFIG = {
    "providers": {
        "image": {"provider": "fal", "model": "nano-banana", "parameters": {}},
        "video": {"provider": "fal", "model": "veo-3", "parameters": {}},
        "voice": {"provider": "fal", "model": "csm-1b", "parameters": {}},
        "sfx": {"provider": "fal", "model": "thinksound", "parameters": {}},
        "music": {"provider": "fal", "model": "csm-1b", "parameters": {}},
    },
    "generation": {"max_attempts": 3, "max_parallel_jobs": 3, "poll_interval_seconds": 5},
    "render": {},
    "generation_approval": {},
}


def init_project(project_dir: Path, title: str) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    for rel in PROJECT_DIRS:
        (project_dir / rel).mkdir(parents=True, exist_ok=True)

    config_path = project_dir / "config.json"
    if not config_path.exists():
        config = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
        config["title"] = title
        config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False))
    return project_dir
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_project.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/project.py tests/test_project.py
git commit -m "feat: add project scaffolding with non-destructive re-init"
```

---

### Task 10: fal.ai provider backends and catalog

**Files:**
- Create: `src/ai_film/providers/fal/client.py`
- Create: `src/ai_film/providers/fal/image.py`
- Create: `src/ai_film/providers/fal/video.py`
- Create: `src/ai_film/providers/fal/audio.py`
- Create: `src/ai_film/providers/fal/catalog.py`
- Test: `tests/providers/test_fal_providers.py`

**Interfaces:**
- Consumes: `ai_film.errors.ProviderError`; `ai_film.models.*`.
- Produces: `ai_film.providers.fal.client.{submit, poll, result, download}` (internal HTTP helpers), `ai_film.providers.fal.image.FalImageProvider`, `ai_film.providers.fal.video.FalVideoProvider`, `ai_film.providers.fal.audio.FalAudioProvider` (each implementing the matching Protocol from Task 2), `ai_film.providers.fal.catalog.FalProviderCatalog` (implements `ProviderCatalog`).

- [ ] **Step 1: Write the failing test**

```python
# tests/providers/test_fal_providers.py
from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_film.models import Capability, ImageGenerationRequest, JobStatus
from ai_film.providers.fal.catalog import FalProviderCatalog
from ai_film.providers.fal.image import FalImageProvider


def test_catalog_lists_image_models_only_for_image_capability():
    catalog = FalProviderCatalog()
    models = catalog.models(Capability.IMAGE)
    assert models, "expected at least one image model"
    assert all(m.capability == Capability.IMAGE and m.provider == "fal" for m in models)


@patch("ai_film.providers.fal.client.requests")
def test_fal_image_provider_full_lifecycle(mock_requests, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")

    submit_response = MagicMock(status_code=200)
    submit_response.json.return_value = {
        "request_id": "req-1",
        "status_url": "https://queue.fal.run/status/req-1",
        "response_url": "https://queue.fal.run/result/req-1",
    }
    status_response = MagicMock(status_code=200)
    status_response.json.return_value = {"status": "COMPLETED"}
    result_response = MagicMock(status_code=200)
    result_response.json.return_value = {"images": [{"url": "https://cdn.fal.run/out.png"}]}
    download_response = MagicMock(status_code=200, content=b"PNG-BYTES")

    mock_requests.post.return_value = submit_response
    mock_requests.get.side_effect = [status_response, result_response, download_response]

    provider = FalImageProvider()
    output_path = tmp_path / "SH01.png"
    request = ImageGenerationRequest(
        prompt="a girl in a corridor", model="nano-banana",
        reference_paths=[], output_path=str(output_path),
    )
    job = provider.submit(request)
    assert job.provider == "fal"
    assert job.id == "req-1"
    assert provider.poll(job) == JobStatus.COMPLETED
    result = provider.get_result(job)
    assert result.artifact_path == str(output_path)
    assert output_path.read_bytes() == b"PNG-BYTES"


@patch("ai_film.providers.fal.client.requests")
def test_fal_client_raises_provider_error_without_fal_key(mock_requests, tmp_path, monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    from ai_film.errors import ProviderError

    provider = FalImageProvider()
    request = ImageGenerationRequest(
        prompt="x", model="nano-banana", reference_paths=[],
        output_path=str(tmp_path / "x.png"),
    )
    try:
        provider.submit(request)
        assert False, "expected ProviderError"
    except ProviderError as exc:
        assert "FAL_KEY" in str(exc)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/providers/test_fal_providers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_film.providers.fal'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ai_film/providers/fal/__init__.py
```

```python
# src/ai_film/providers/fal/client.py
from __future__ import annotations

import os
from pathlib import Path

import requests

from ai_film.errors import ProviderError
from ai_film.models import Capability, GenerationJob, JobStatus

FAL_QUEUE_BASE = "https://queue.fal.run"

_STATUS_MAP = {
    "IN_QUEUE": JobStatus.QUEUED,
    "IN_PROGRESS": JobStatus.RUNNING,
    "COMPLETED": JobStatus.COMPLETED,
}


def _headers() -> dict:
    key = os.environ.get("FAL_KEY")
    if not key:
        raise ProviderError("FAL_KEY environment variable is not set")
    return {"Authorization": f"Key {key}", "Content-Type": "application/json"}


def submit(app_id: str, input_data: dict, capability: Capability) -> tuple[GenerationJob, str, str]:
    response = requests.post(
        f"{FAL_QUEUE_BASE}/{app_id}", json=input_data, headers=_headers(), timeout=30
    )
    if response.status_code >= 400:
        raise ProviderError(f"fal submit failed ({response.status_code}): {response.text}")
    body = response.json()
    job = GenerationJob(provider="fal", id=body["request_id"], capability=capability)
    return job, body["status_url"], body["response_url"]


def poll(status_url: str) -> JobStatus:
    response = requests.get(status_url, headers=_headers(), timeout=30)
    if response.status_code >= 400:
        raise ProviderError(f"fal status check failed ({response.status_code}): {response.text}")
    status = response.json().get("status", "IN_QUEUE")
    if status not in _STATUS_MAP:
        raise ProviderError(f"fal job failed with status: {status}")
    return _STATUS_MAP[status]


def result(response_url: str) -> dict:
    response = requests.get(response_url, headers=_headers(), timeout=30)
    if response.status_code >= 400:
        raise ProviderError(f"fal result fetch failed ({response.status_code}): {response.text}")
    return response.json()


def download(url: str, output_path: str) -> int:
    response = requests.get(url, timeout=60)
    if response.status_code >= 400:
        raise ProviderError(f"fal artifact download failed ({response.status_code})")
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return len(response.content)
```

```python
# src/ai_film/providers/fal/image.py
from __future__ import annotations

from ai_film.models import (
    Capability, GenerationJob, ImageGenerationRequest, ImageGenerationResult, JobStatus,
)
from ai_film.providers.fal import client

MODEL_TO_APP_ID = {
    "nano-banana": "fal-ai/nano-banana-2",
    "nano-banana-pro": "fal-ai/nano-banana-pro",
}


class FalImageProvider:
    def __init__(self):
        self._jobs: dict[str, tuple[str, str, ImageGenerationRequest]] = {}

    def submit(self, request: ImageGenerationRequest) -> GenerationJob:
        app_id = MODEL_TO_APP_ID[request.model]
        input_data = {"prompt": request.prompt, "num_images": 1}
        if request.reference_paths:
            input_data["image_urls"] = request.reference_paths
        job, status_url, response_url = client.submit(app_id, input_data, Capability.IMAGE)
        self._jobs[job.id] = (status_url, response_url, request)
        return job

    def poll(self, job: GenerationJob) -> JobStatus:
        status_url, _, _ = self._jobs[job.id]
        return client.poll(status_url)

    def get_result(self, job: GenerationJob) -> ImageGenerationResult:
        _, response_url, request = self._jobs[job.id]
        body = client.result(response_url)
        image_url = body["images"][0]["url"]
        size_bytes = client.download(image_url, request.output_path)
        return ImageGenerationResult(artifact_path=request.output_path, size_bytes=size_bytes)
```

```python
# src/ai_film/providers/fal/video.py
from __future__ import annotations

from ai_film.models import (
    Capability, GenerationJob, JobStatus, VideoGenerationRequest, VideoGenerationResult,
)
from ai_film.providers.fal import client

MODEL_TO_APP_ID = {
    "veo-3": "fal-ai/veo-3",
    "seedance-1-0-pro": "fal-ai/seedance-1-0-pro",
    "kling-v3-pro": "fal-ai/kling-video/v3/pro",
}


class FalVideoProvider:
    def __init__(self):
        self._jobs: dict[str, tuple[str, str, VideoGenerationRequest]] = {}

    def submit(self, request: VideoGenerationRequest) -> GenerationJob:
        app_id = MODEL_TO_APP_ID[request.model]
        input_data = {
            "prompt": request.prompt,
            "duration": f"{int(request.duration_seconds)}s",
        }
        if request.reference_paths:
            input_data["image_url"] = request.reference_paths[0]
        job, status_url, response_url = client.submit(app_id, input_data, Capability.VIDEO)
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
        return VideoGenerationResult(
            artifact_path=request.output_path,
            size_bytes=size_bytes,
            duration_seconds=request.duration_seconds,
        )
```

```python
# src/ai_film/providers/fal/audio.py
from __future__ import annotations

from ai_film.models import (
    AudioGenerationResult, Capability, GenerationJob, JobStatus,
    MusicGenerationRequest, SfxGenerationRequest, VoiceGenerationRequest,
)
from ai_film.providers.fal import client

VOICE_MODEL_TO_APP_ID = {"csm-1b": "fal-ai/csm-1b"}
SFX_MODEL_TO_APP_ID = {"thinksound": "fal-ai/thinksound"}
MUSIC_MODEL_TO_APP_ID = {"csm-1b": "fal-ai/csm-1b"}

AudioRequest = VoiceGenerationRequest | SfxGenerationRequest | MusicGenerationRequest


class FalAudioProvider:
    def __init__(self):
        self._jobs: dict[str, tuple[str, str, AudioRequest, float]] = {}

    def submit_voice(self, request: VoiceGenerationRequest) -> GenerationJob:
        app_id = VOICE_MODEL_TO_APP_ID[request.model]
        input_data = {"text": request.text, "speaker_id": request.speaker or "0"}
        return self._submit(app_id, input_data, request, duration_seconds=2.0)

    def submit_sfx(self, request: SfxGenerationRequest) -> GenerationJob:
        app_id = SFX_MODEL_TO_APP_ID[request.model]
        input_data = {"prompt": request.prompt}
        return self._submit(app_id, input_data, request, duration_seconds=2.0)

    def submit_music(self, request: MusicGenerationRequest) -> GenerationJob:
        app_id = MUSIC_MODEL_TO_APP_ID[request.model]
        input_data = {"prompt": request.prompt, "duration": request.duration_seconds}
        return self._submit(app_id, input_data, request, duration_seconds=request.duration_seconds)

    def _submit(
        self, app_id: str, input_data: dict, request: AudioRequest, duration_seconds: float
    ) -> GenerationJob:
        job, status_url, response_url = client.submit(app_id, input_data, Capability.VOICE)
        self._jobs[job.id] = (status_url, response_url, request, duration_seconds)
        return job

    def poll(self, job: GenerationJob) -> JobStatus:
        status_url, _, _, _ = self._jobs[job.id]
        return client.poll(status_url)

    def get_result(self, job: GenerationJob) -> AudioGenerationResult:
        _, response_url, request, duration_seconds = self._jobs[job.id]
        body = client.result(response_url)
        audio_url = body["audio"]["url"]
        size_bytes = client.download(audio_url, request.output_path)
        return AudioGenerationResult(
            artifact_path=request.output_path,
            size_bytes=size_bytes,
            duration_seconds=duration_seconds,
        )
```

```python
# src/ai_film/providers/fal/catalog.py
from __future__ import annotations

from ai_film.models import Capability, ModelInfo

_MODELS = [
    ModelInfo("fal", "nano-banana", Capability.IMAGE, "Nano Banana 2 (fast)"),
    ModelInfo("fal", "nano-banana-pro", Capability.IMAGE, "Nano Banana Pro (high fidelity)"),
    ModelInfo("fal", "veo-3", Capability.VIDEO, "Veo 3 (Google DeepMind)"),
    ModelInfo("fal", "seedance-1-0-pro", Capability.VIDEO, "Seedance 1.0 Pro"),
    ModelInfo("fal", "kling-v3-pro", Capability.VIDEO, "Kling Video v3 Pro"),
    ModelInfo("fal", "csm-1b", Capability.VOICE, "CSM-1B conversational speech"),
    ModelInfo("fal", "thinksound", Capability.SFX, "ThinkSound (video-to-audio)"),
    ModelInfo("fal", "csm-1b", Capability.MUSIC, "CSM-1B (placeholder music backend)"),
]


class FalProviderCatalog:
    def models(self, capability: Capability) -> list[ModelInfo]:
        return [m for m in _MODELS if m.capability == capability]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/providers/test_fal_providers.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/providers/fal tests/providers/test_fal_providers.py
git commit -m "feat: add fal.ai provider backends and static model catalog"
```

---

### Task 11: Render — manifest builder, preflight, and ffmpeg invocation

**Files:**
- Create: `src/ai_film/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `ai_film.shot_store.{load_shot, save_shot}`.
- Produces: `ai_film.render.build_manifest(project_dir: Path) -> dict`, `ai_film.render.preflight(manifest: dict, project_dir: Path) -> list[str]` (empty list = passes), `ai_film.render.RenderPreflightError(Exception)` (has `.errors: list[str]`), `ai_film.render.render(project_dir: Path, manifest: dict, output_name: str = "reel_001.mp4") -> Path` (raises `RenderPreflightError` if preflight fails; raises `RuntimeError` if `ffmpeg` is not on `PATH`).

`preflight` checks (per spec §11): every shot in the manifest has a `03_shots/<id>.json`; the shot's `generation.video.status` is not `"failed"`; the artifact path exists, is non-empty, and resolves inside `project_dir` (no path escapes). Video duration is already known from `generation.video.artifact.duration_seconds` recorded at generation time (Task 8) — no separate duration probe is needed at render time.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_render.py
import shutil
import subprocess
from pathlib import Path

import pytest

from ai_film.render import RenderPreflightError, build_manifest, preflight, render
from ai_film.shot_store import save_shot


def _shot(shot_id: str, video_path: str | None, video_status: str = "completed") -> dict:
    return {
        "schema_version": "1.0",
        "id": shot_id,
        "status": "draft",
        "duration_seconds": 2,
        "continuity": {"status": "passed", "checked_at": None, "issues": []},
        "generation": {
            "image": {"status": "not_required"},
            "video": {
                "status": video_status,
                "attempts": 1,
                "artifact": (
                    {"path": video_path, "size_bytes": 10, "sha256": None, "duration_seconds": 2.0}
                    if video_path else None
                ),
            },
            "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }


def _make_tiny_mp4(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=32x32:d=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "1", str(path),
        ],
        check=True, capture_output=True,
    )


def test_build_manifest_orders_shots_and_includes_duration(tmp_path: Path):
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01", "05_video/S01_SH01.mp4"))
    save_shot(tmp_path / "03_shots" / "S01_SH02.json", _shot("S01_SH02", "05_video/S01_SH02.mp4"))
    manifest = build_manifest(tmp_path)
    assert [s["id"] for s in manifest["shots"]] == ["S01_SH01", "S01_SH02"]
    assert manifest["shots"][0]["duration"] == 2


def test_preflight_reports_missing_artifact(tmp_path: Path):
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01", "05_video/missing.mp4"))
    manifest = build_manifest(tmp_path)
    errors = preflight(manifest, tmp_path)
    assert any("does not exist" in e for e in errors)


def test_preflight_reports_failed_generation_status(tmp_path: Path):
    video_path = tmp_path / "05_video" / "S01_SH01.mp4"
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"data")
    save_shot(
        tmp_path / "03_shots" / "S01_SH01.json",
        _shot("S01_SH01", "05_video/S01_SH01.mp4", video_status="failed"),
    )
    manifest = build_manifest(tmp_path)
    errors = preflight(manifest, tmp_path)
    assert any("failed" in e for e in errors)


def test_preflight_rejects_path_escaping_project_dir(tmp_path: Path):
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01", "../../etc/passwd"))
    manifest = build_manifest(tmp_path)
    errors = preflight(manifest, tmp_path)
    assert any("escapes project directory" in e for e in errors)


def test_render_raises_preflight_error_when_artifact_missing(tmp_path: Path):
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01", "05_video/missing.mp4"))
    manifest = build_manifest(tmp_path)
    with pytest.raises(RenderPreflightError):
        render(tmp_path, manifest)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_render_produces_playable_final_video(tmp_path: Path):
    _make_tiny_mp4(tmp_path / "05_video" / "S01_SH01.mp4")
    _make_tiny_mp4(tmp_path / "05_video" / "S01_SH02.mp4")
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01", "05_video/S01_SH01.mp4"))
    save_shot(tmp_path / "03_shots" / "S01_SH02.json", _shot("S01_SH02", "05_video/S01_SH02.mp4"))

    manifest = build_manifest(tmp_path)
    output_path = render(tmp_path, manifest)

    assert output_path == tmp_path / "final" / "reel_001.mp4"
    assert output_path.exists()
    assert output_path.stat().st_size > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", str(output_path)],
        capture_output=True, text=True,
    )
    assert probe.returncode == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_render.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_film.render'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ai_film/render.py
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ai_film.shot_store import load_shot


def build_manifest(project_dir: Path) -> dict:
    shots_dir = project_dir / "03_shots"
    shot_paths = sorted(shots_dir.glob("*.json"))
    shots = []
    for path in shot_paths:
        shot = load_shot(path)
        video = shot["generation"]["video"]
        artifact = video.get("artifact")
        shots.append({
            "id": shot["id"],
            "video": artifact["path"] if artifact else None,
            "duration": shot.get("duration_seconds"),
        })
    return {"shots": shots, "audio": [], "captions": []}


def preflight(manifest: dict, project_dir: Path) -> list[str]:
    errors: list[str] = []
    shots_dir = project_dir / "03_shots"
    resolved_root = project_dir.resolve()

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


class RenderPreflightError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def render(project_dir: Path, manifest: dict, output_name: str = "reel_001.mp4") -> Path:
    errors = preflight(manifest, project_dir)
    if errors:
        raise RenderPreflightError(errors)

    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not installed or not on PATH")

    concat_list_path = project_dir / "99_logs" / "_render_concat_list.txt"
    concat_list_path.parent.mkdir(parents=True, exist_ok=True)
    with concat_list_path.open("w") as handle:
        for entry in manifest["shots"]:
            video_path = (project_dir / entry["video"]).resolve()
            handle.write(f"file '{video_path.as_posix()}'\n")

    output_path = project_dir / "final" / output_name
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_list_path), "-c", "copy", str(output_path),
        ],
        check=True, capture_output=True,
    )
    return output_path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_render.py -v`
Expected: PASS (6 tests; the real-ffmpeg test skips if `ffmpeg` isn't installed)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/render.py tests/test_render.py
git commit -m "feat: add render manifest, preflight validation, and ffmpeg render"
```

---

### Task 12: CLI — project, catalog, status, and validate commands

**Files:**
- Modify: `src/ai_film/cli.py` (replace entire file with the version below — it now imports project/schema/shot_store modules from Tasks 4, 5, 9)
- Test: `tests/test_cli_project_commands.py`

**Interfaces:**
- Consumes: `ai_film.project.init_project`; `ai_film.providers.fal.catalog.FalProviderCatalog`; `ai_film.models.Capability`; `ai_film.shot_store.load_shot`; `ai_film.schema.validate_shot`.
- Produces: CLI commands `init`, `models`, `status`, `validate` on `ai_film.cli.app`. Every project-scoped command takes `--path` (default `Path("project")`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_project_commands.py
import json
from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.shot_store import save_shot

runner = CliRunner()


def _shot(shot_id: str) -> dict:
    return {
        "schema_version": "1.0",
        "id": shot_id,
        "status": "draft",
        "duration_seconds": 5,
        "continuity": {"status": "pending", "checked_at": None, "issues": []},
        "generation": {
            "image": {"status": "pending", "attempts": 0},
            "video": {"status": "pending", "attempts": 0},
            "voice": {"status": "pending", "attempts": 0},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }


def test_init_creates_project_structure(tmp_path: Path):
    project_dir = tmp_path / "project"
    result = runner.invoke(app, ["init", "The Last Ship", "--path", str(project_dir)])
    assert result.exit_code == 0
    assert (project_dir / "config.json").exists()
    assert (project_dir / "03_shots").is_dir()


def test_models_lists_image_catalog():
    result = runner.invoke(app, ["models", "--capability", "image"])
    assert result.exit_code == 0
    assert "nano-banana" in result.output


def test_status_reports_shot_id_and_status(tmp_path: Path):
    project_dir = tmp_path / "project"
    save_shot(project_dir / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    result = runner.invoke(app, ["status", "--path", str(project_dir)])
    assert result.exit_code == 0
    assert "S01_SH01" in result.output
    assert "draft" in result.output


def test_validate_reports_invalid_shot_and_exits_nonzero(tmp_path: Path):
    project_dir = tmp_path / "project"
    shots_dir = project_dir / "03_shots"
    shots_dir.mkdir(parents=True)
    (shots_dir / "S01_SH01.json").write_text(json.dumps({"id": "S01_SH01"}))
    result = runner.invoke(app, ["validate", "--path", str(project_dir)])
    assert result.exit_code == 1
    assert "INVALID" in result.output


def test_validate_passes_on_well_formed_shot(tmp_path: Path):
    project_dir = tmp_path / "project"
    save_shot(project_dir / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    result = runner.invoke(app, ["validate", "--path", str(project_dir)])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_project_commands.py -v`
Expected: FAIL — `init`/`models`/`status`/`validate` are not recognized commands (`Error: No such command`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/ai_film/cli.py
from __future__ import annotations

import json
from pathlib import Path

import typer

from ai_film import __version__
from ai_film.models import Capability
from ai_film.project import init_project
from ai_film.providers.fal.catalog import FalProviderCatalog
from ai_film.schema import validate_shot
from ai_film.shot_store import load_shot

app = typer.Typer(name="ai-film", help="AI Film Studio production engine.")

DEFAULT_PROJECT_PATH = Path("project")


@app.command()
def version() -> None:
    """Print the ai-film package version."""
    typer.echo(__version__)


@app.command(name="init")
def init_cmd(
    title: str,
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Scaffold a new project directory tree and default config.json."""
    init_project(path, title)
    typer.echo(f"Initialized project '{title}' at {path}")


@app.command(name="models")
def models_cmd(
    capability: str = typer.Option(..., "--capability", help="image|video|voice|sfx|music"),
) -> None:
    """List the provider/model catalog for a capability (v1: fal.ai only)."""
    cap = Capability(capability)
    catalog = FalProviderCatalog()
    for model in catalog.models(cap):
        typer.echo(f"{model.provider}/{model.model}  {model.display_name}")


@app.command(name="status")
def status_cmd(path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path")) -> None:
    """Report every shot's aggregate status and a project-wide summary."""
    shots_dir = path / "03_shots"
    counts: dict[str, int] = {}
    for shot_path in sorted(shots_dir.glob("*.json")):
        shot = load_shot(shot_path)
        counts[shot["status"]] = counts.get(shot["status"], 0) + 1
        typer.echo(f"{shot['id']}  {shot['status']}")
    if counts:
        summary = ", ".join(f"{status}: {count}" for status, count in counts.items())
        typer.echo(f"\n{sum(counts.values())} shots — {summary}")


@app.command(name="validate")
def validate_cmd(path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path")) -> None:
    """Validate every shot.json against the schema; exit 1 if any are invalid."""
    shots_dir = path / "03_shots"
    had_errors = False
    for shot_path in sorted(shots_dir.glob("*.json")):
        shot = json.loads(shot_path.read_text())
        errors = validate_shot(shot)
        if errors:
            had_errors = True
            typer.echo(f"{shot_path.name}: INVALID")
            for error in errors:
                typer.echo(f"  - {error}")
        else:
            typer.echo(f"{shot_path.name}: valid")
    if had_errors:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_project_commands.py tests/test_cli_skeleton.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/cli.py tests/test_cli_project_commands.py
git commit -m "feat: add init, models, status, and validate CLI commands"
```

---

### Task 13: CLI — generation, approval, continuity, and render commands

**Files:**
- Create: `src/ai_film/prompts.py`
- Create: `src/ai_film/providers/registry.py`
- Modify: `src/ai_film/cli.py` (replace entire file with the version below)
- Test: `tests/test_prompts.py`
- Test: `tests/providers/test_registry.py`
- Test: `tests/test_cli_generation_commands.py`

**Interfaces:**
- Consumes: everything from Tasks 2–11 (`generate_image/video/voice/sfx/music` from Task 8; `approve_generation`, `is_approved` from Task 7; `build_manifest`, `render`, `RenderPreflightError` from Task 11; `CostGateError`, `ProviderError` from Task 2; `save_shot` from Task 5).
- Produces: `ai_film.prompts.build_image_prompt(shot: dict) -> str`, `ai_film.prompts.build_video_prompt(shot: dict) -> str`; `ai_film.providers.registry.resolve_provider(capability: Capability, provider_name: str)` (raises `ValueError` for an unknown name); CLI commands `generate-image`, `generate-video`, `generate-voice`, `generate-sfx`, `generate-music`, `check-continuity`, `approve-generation`, `render`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_prompts.py
from ai_film.prompts import build_image_prompt, build_video_prompt


def test_build_image_prompt_combines_action_and_visual():
    shot = {
        "action": "girl steps out of darkness",
        "visual": {"style": "cinematic sci-fi", "lighting": "blue rim light"},
        "camera": {"shot": "close_up"},
    }
    prompt = build_image_prompt(shot)
    assert "girl steps out of darkness" in prompt
    assert "cinematic sci-fi" in prompt
    assert "close_up shot" in prompt


def test_build_video_prompt_adds_camera_movement():
    shot = {"action": "girl steps out of darkness", "visual": {}, "camera": {"movement": "slow_push_in"}}
    prompt = build_video_prompt(shot)
    assert "slow_push_in" in prompt
```

```python
# tests/providers/test_registry.py
import pytest

from ai_film.models import Capability
from ai_film.providers.mock.image import MockImageProvider
from ai_film.providers.registry import resolve_provider


def test_resolve_provider_returns_mock_image_provider():
    provider = resolve_provider(Capability.IMAGE, "mock")
    assert isinstance(provider, MockImageProvider)


def test_resolve_provider_rejects_unknown_provider_name():
    with pytest.raises(ValueError):
        resolve_provider(Capability.IMAGE, "not-a-provider")
```

```python
# tests/test_cli_generation_commands.py
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.shot_store import load_shot, save_shot

runner = CliRunner()


def _shot(shot_id: str) -> dict:
    return {
        "schema_version": "1.0",
        "id": shot_id,
        "status": "draft",
        "duration_seconds": 2,
        "continuity": {"status": "pending", "checked_at": None, "issues": []},
        "action": "girl steps out of darkness",
        "visual": {"style": "cinematic sci-fi"},
        "camera": {"shot": "close_up", "movement": "slow_push_in"},
        "characters": [],
        "generation": {
            "image": {"status": "pending", "attempts": 0},
            "video": {"status": "pending", "attempts": 0},
            "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }


def _init_mock_project(tmp_path: Path) -> Path:
    project_dir = tmp_path / "project"
    runner.invoke(app, ["init", "Test Film", "--path", str(project_dir)])
    config = json.loads((project_dir / "config.json").read_text())
    for stage in config["providers"]:
        config["providers"][stage]["provider"] = "mock"
    (project_dir / "config.json").write_text(json.dumps(config))
    save_shot(project_dir / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    return project_dir


def test_generate_image_blocked_without_approval(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    result = runner.invoke(
        app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir)]
    )
    assert result.exit_code == 1


def test_generate_image_succeeds_after_approval(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    runner.invoke(
        app,
        ["approve-generation", "--scope", "storyboard", "--targets", "S01_SH01", "--path", str(project_dir)],
    )
    result = runner.invoke(
        app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir)]
    )
    assert result.exit_code == 0
    assert "completed" in result.output
    assert (project_dir / "04_storyboard" / "S01_SH01.png").exists()


def test_check_continuity_updates_shot(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    result = runner.invoke(
        app,
        [
            "check-continuity", "--shot", "S01_SH01", "--status", "passed",
            "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 0
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    assert shot["continuity"]["status"] == "passed"


def test_check_continuity_records_issues(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    runner.invoke(
        app,
        [
            "check-continuity", "--shot", "S01_SH01", "--status", "failed",
            "--issue", "jacket color mismatch", "--path", str(project_dir),
        ],
    )
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    assert shot["continuity"]["issues"] == ["jacket color mismatch"]


def test_approve_generation_rejects_unknown_scope(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    result = runner.invoke(
        app,
        ["approve-generation", "--scope", "bogus", "--targets", "S01_SH01", "--path", str(project_dir)],
    )
    assert result.exit_code == 1


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_render_reports_preflight_failure_when_no_shots_generated(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    result = runner.invoke(app, ["render", "--path", str(project_dir)])
    assert result.exit_code == 1
    assert "preflight" in result.output.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_prompts.py tests/providers/test_registry.py tests/test_cli_generation_commands.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_film.prompts'` (and similarly for `ai_film.providers.registry`; the CLI tests fail with `Error: No such command 'generate-image'`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/ai_film/prompts.py
from __future__ import annotations


def build_image_prompt(shot: dict) -> str:
    parts = [shot.get("action", "")]
    visual = shot.get("visual", {})
    if visual.get("style"):
        parts.append(f"style: {visual['style']}")
    if visual.get("lighting"):
        parts.append(f"lighting: {visual['lighting']}")
    camera = shot.get("camera", {})
    if camera.get("shot"):
        parts.append(f"{camera['shot']} shot")
    return ", ".join(part for part in parts if part)


def build_video_prompt(shot: dict) -> str:
    parts = [build_image_prompt(shot)]
    camera = shot.get("camera", {})
    if camera.get("movement"):
        parts.append(f"camera movement: {camera['movement']}")
    return ", ".join(part for part in parts if part)
```

```python
# src/ai_film/providers/registry.py
from __future__ import annotations

from ai_film.models import Capability
from ai_film.providers.fal.audio import FalAudioProvider
from ai_film.providers.fal.image import FalImageProvider
from ai_film.providers.fal.video import FalVideoProvider
from ai_film.providers.mock.audio import MockAudioProvider
from ai_film.providers.mock.image import MockImageProvider
from ai_film.providers.mock.video import MockVideoProvider

_REGISTRIES = {
    Capability.IMAGE: {"fal": FalImageProvider, "mock": MockImageProvider},
    Capability.VIDEO: {"fal": FalVideoProvider, "mock": MockVideoProvider},
    Capability.VOICE: {"fal": FalAudioProvider, "mock": MockAudioProvider},
    Capability.SFX: {"fal": FalAudioProvider, "mock": MockAudioProvider},
    Capability.MUSIC: {"fal": FalAudioProvider, "mock": MockAudioProvider},
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

```python
# src/ai_film/cli.py
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import typer

from ai_film import __version__
from ai_film.approval import approve_generation as approve_generation_service
from ai_film.errors import CostGateError, ProviderError
from ai_film.models import Capability
from ai_film.project import init_project
from ai_film.prompts import build_image_prompt, build_video_prompt
from ai_film.providers.fal.catalog import FalProviderCatalog
from ai_film.providers.registry import resolve_provider
from ai_film.render import RenderPreflightError, build_manifest
from ai_film.render import render as render_engine
from ai_film.schema import validate_shot
from ai_film.services.generation_service import (
    generate_image as generate_image_service,
    generate_music as generate_music_service,
    generate_sfx as generate_sfx_service,
    generate_video as generate_video_service,
    generate_voice as generate_voice_service,
)
from ai_film.shot_store import load_shot, save_shot

app = typer.Typer(name="ai-film", help="AI Film Studio production engine.")

DEFAULT_PROJECT_PATH = Path("project")


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
    capability: str = typer.Option(..., "--capability", help="image|video|voice|sfx|music"),
) -> None:
    """List the provider/model catalog for a capability (v1: fal.ai only)."""
    catalog = FalProviderCatalog()
    for model in catalog.models(Capability(capability)):
        typer.echo(f"{model.provider}/{model.model}  {model.display_name}")


@app.command(name="status")
def status_cmd(path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path")) -> None:
    """Report every shot's aggregate status and a project-wide summary."""
    counts: dict[str, int] = {}
    for shot_path in sorted((path / "03_shots").glob("*.json")):
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
    for shot_path in sorted((path / "03_shots").glob("*.json")):
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


def _stage_config(path: Path, stage: str) -> dict:
    config = json.loads((path / "config.json").read_text())
    return config["providers"][stage], config["generation"]


def _run_generation(capability: Capability, stage: str, run_fn) -> None:
    try:
        result = run_fn()
    except CostGateError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    except ProviderError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{stage}: {result['status']}")


@app.command(name="generate-image")
def generate_image_cmd(
    shot: str = typer.Option(..., "--shot"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    stage_config, gen_config = _stage_config(path, "image")
    shot_path = path / "03_shots" / f"{shot}.json"
    shot_data = load_shot(shot_path)
    provider = resolve_provider(Capability.IMAGE, stage_config["provider"])
    references = [c["reference"] for c in shot_data.get("characters", []) if c.get("reference")]
    _run_generation(
        Capability.IMAGE, shot,
        lambda: generate_image_service(
            project_dir=path, shot_path=shot_path, provider=provider,
            prompt=build_image_prompt(shot_data), model=stage_config["model"],
            reference_paths=references, output_path=path / "04_storyboard" / f"{shot}.png",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
        ),
    )


@app.command(name="generate-video")
def generate_video_cmd(
    shot: str = typer.Option(..., "--shot"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    stage_config, gen_config = _stage_config(path, "video")
    shot_path = path / "03_shots" / f"{shot}.json"
    shot_data = load_shot(shot_path)
    provider = resolve_provider(Capability.VIDEO, stage_config["provider"])
    references = [c["reference"] for c in shot_data.get("characters", []) if c.get("reference")]
    _run_generation(
        Capability.VIDEO, shot,
        lambda: generate_video_service(
            project_dir=path, shot_path=shot_path, provider=provider,
            prompt=build_video_prompt(shot_data), model=stage_config["model"],
            reference_paths=references, duration_seconds=shot_data["duration_seconds"],
            output_path=path / "05_video" / f"{shot}.mp4",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
        ),
    )


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
    provider = resolve_provider(Capability.VOICE, stage_config["provider"])
    _run_generation(
        Capability.VOICE, shot,
        lambda: generate_voice_service(
            project_dir=path, shot_path=shot_path, provider=provider,
            text=dialogue.get("text", ""), model=stage_config["model"],
            speaker=dialogue.get("speaker", ""),
            output_path=path / "06_audio" / "dialogue" / f"{shot}.wav",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
        ),
    )


@app.command(name="generate-sfx")
def generate_sfx_cmd(
    shot: str = typer.Option(..., "--shot"),
    prompt: str = typer.Option(..., "--prompt"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    stage_config, gen_config = _stage_config(path, "sfx")
    shot_path = path / "03_shots" / f"{shot}.json"
    provider = resolve_provider(Capability.SFX, stage_config["provider"])
    _run_generation(
        Capability.SFX, shot,
        lambda: generate_sfx_service(
            project_dir=path, shot_path=shot_path, provider=provider,
            prompt=prompt, model=stage_config["model"],
            output_path=path / "06_audio" / "sfx" / f"{shot}.wav",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
        ),
    )


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
    provider = resolve_provider(Capability.MUSIC, stage_config["provider"])
    _run_generation(
        Capability.MUSIC, shot,
        lambda: generate_music_service(
            project_dir=path, shot_path=shot_path, provider=provider,
            prompt=prompt, model=stage_config["model"], duration_seconds=duration_seconds,
            output_path=path / "06_audio" / "music" / f"{shot}.wav",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
        ),
    )


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
    save_shot(shot_path, shot_data)
    typer.echo(f"{shot}: continuity {status}")


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


@app.command(name="render")
def render_cmd(path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path")) -> None:
    manifest = build_manifest(path)
    try:
        output_path = render_engine(path, manifest)
    except RenderPreflightError as exc:
        typer.echo("render preflight failed:", err=True)
        for error in exc.errors:
            typer.echo(f"  - {error}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"rendered {output_path}")


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_prompts.py tests/providers/test_registry.py tests/test_cli_generation_commands.py -v`
Expected: PASS (9 tests; the render test skips if `ffmpeg` isn't installed)

- [ ] **Step 5: Run the full test suite so far**

Run: `pytest -v`
Expected: PASS (all tests from Tasks 1–13)

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/prompts.py src/ai_film/providers/registry.py src/ai_film/cli.py \
        tests/test_prompts.py tests/providers/test_registry.py tests/test_cli_generation_commands.py
git commit -m "feat: add generation, approval, continuity, and render CLI commands"
```

---

### Task 14: Batch generation with bounded concurrency

**Files:**
- Create: `src/ai_film/batch.py`
- Modify: `src/ai_film/cli.py` (add `generate-all` command and its supporting helpers)
- Test: `tests/test_batch.py`
- Test: `tests/test_cli_batch.py`

**Interfaces:**
- Consumes: `ai_film.services.generation_service.{generate_image, generate_video, generate_voice}`; `ai_film.models.Capability`; everything already imported by `cli.py` in Task 13.
- Produces: `ai_film.batch.run_bounded(tasks: list[Callable[[], None]], max_workers: int) -> list[Exception | None]` (index-aligned with `tasks`; never raises — per-task exceptions are captured, not propagated, so one failing shot doesn't stop the others); CLI command `generate-all --stage image|video|voice` reading `config.json`'s `generation.max_parallel_jobs` to bound concurrency.

`generate-all` is scoped to `image`/`video`/`voice` — the three stages whose generation request is fully derivable from `shot.json` alone. `sfx`/`music` stay single-shot-only (Task 13), since they require a human-specified `--prompt` with no natural per-shot default, and most shots leave them `"not_required"` anyway.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_batch.py
import threading
import time

from ai_film.batch import run_bounded


def test_run_bounded_executes_all_tasks():
    calls: list[int] = []
    lock = threading.Lock()

    def make_task(n: int):
        def task() -> None:
            with lock:
                calls.append(n)
        return task

    results = run_bounded([make_task(i) for i in range(5)], max_workers=2)
    assert sorted(calls) == [0, 1, 2, 3, 4]
    assert results == [None] * 5


def test_run_bounded_never_exceeds_max_workers():
    current = {"value": 0}
    peak = {"value": 0}
    lock = threading.Lock()

    def task() -> None:
        with lock:
            current["value"] += 1
            peak["value"] = max(peak["value"], current["value"])
        time.sleep(0.05)
        with lock:
            current["value"] -= 1

    run_bounded([task for _ in range(6)], max_workers=2)
    assert peak["value"] <= 2


def test_run_bounded_captures_exceptions_without_stopping_other_tasks():
    def failing() -> None:
        raise ValueError("boom")

    def succeeding() -> None:
        pass

    results = run_bounded([failing, succeeding, failing], max_workers=3)
    assert isinstance(results[0], ValueError)
    assert results[1] is None
    assert isinstance(results[2], ValueError)
```

```python
# tests/test_cli_batch.py
import json
from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.shot_store import load_shot, save_shot

runner = CliRunner()


def _shot(shot_id: str) -> dict:
    return {
        "schema_version": "1.0", "id": shot_id, "status": "draft", "duration_seconds": 2,
        "continuity": {"status": "pending", "checked_at": None, "issues": []},
        "action": "a", "visual": {}, "camera": {}, "characters": [],
        "generation": {
            "image": {"status": "pending", "attempts": 0},
            "video": {"status": "pending", "attempts": 0},
            "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }


def _init_mock_project(tmp_path: Path) -> Path:
    project_dir = tmp_path / "project"
    runner.invoke(app, ["init", "Test Film", "--path", str(project_dir)])
    config = json.loads((project_dir / "config.json").read_text())
    for stage in config["providers"]:
        config["providers"][stage]["provider"] = "mock"
    (project_dir / "config.json").write_text(json.dumps(config))
    for shot_id in ("S01_SH01", "S01_SH02", "S01_SH03"):
        save_shot(project_dir / "03_shots" / f"{shot_id}.json", _shot(shot_id))
    return project_dir


def test_generate_all_generates_every_approved_shot(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    shot_ids = ["S01_SH01", "S01_SH02", "S01_SH03"]
    runner.invoke(
        app,
        ["approve-generation", "--scope", "storyboard", "--targets", ",".join(shot_ids), "--path", str(project_dir)],
    )
    result = runner.invoke(app, ["generate-all", "--stage", "image", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    for shot_id in shot_ids:
        shot = load_shot(project_dir / "03_shots" / f"{shot_id}.json")
        assert shot["generation"]["image"]["status"] == "completed"


def test_generate_all_reports_failures_without_stopping_other_shots(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    runner.invoke(
        app,
        ["approve-generation", "--scope", "storyboard", "--targets", "S01_SH01,S01_SH02", "--path", str(project_dir)],
    )
    result = runner.invoke(app, ["generate-all", "--stage", "image", "--path", str(project_dir)])
    assert result.exit_code == 1
    assert "S01_SH03" in result.output
    shot1 = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    assert shot1["generation"]["image"]["status"] == "completed"


def test_generate_all_rejects_unsupported_stage(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    result = runner.invoke(app, ["generate-all", "--stage", "sfx", "--path", str(project_dir)])
    assert result.exit_code == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_batch.py tests/test_cli_batch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_film.batch'` (and `Error: No such command 'generate-all'`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/ai_film/batch.py
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable


def run_bounded(tasks: list[Callable[[], None]], max_workers: int) -> list[Exception | None]:
    """Run each zero-arg callable with bounded concurrency.

    Returns per-task exceptions (or None on success), index-aligned with `tasks`.
    A failing task never stops or cancels the others.
    """
    results: list[Exception | None] = [None] * len(tasks)
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        future_to_index = {executor.submit(task): i for i, task in enumerate(tasks)}
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001 - captured per-task, not re-raised
                results[index] = exc
    return results
```

Add the following to `src/ai_film/cli.py`: one new import line, one new module-level dict, one new helper, and one new command.

```python
# Add to the imports block near the top of src/ai_film/cli.py:
from ai_film.batch import run_bounded

# Add near the bottom of src/ai_film/cli.py, after the other command functions:

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
    references = [c["reference"] for c in shot_data.get("characters", []) if c.get("reference")]

    if stage == "image":
        return lambda: service_fn(
            project_dir=path, shot_path=shot_path, provider=provider,
            prompt=build_image_prompt(shot_data), model=stage_config["model"],
            reference_paths=references, output_path=path / "04_storyboard" / f"{shot_id}.png",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
        )
    if stage == "video":
        return lambda: service_fn(
            project_dir=path, shot_path=shot_path, provider=provider,
            prompt=build_video_prompt(shot_data), model=stage_config["model"],
            reference_paths=references, duration_seconds=shot_data["duration_seconds"],
            output_path=path / "05_video" / f"{shot_id}.mp4",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
        )
    dialogue = shot_data.get("dialogue", {})
    return lambda: service_fn(
        project_dir=path, shot_path=shot_path, provider=provider,
        text=dialogue.get("text", ""), model=stage_config["model"],
        speaker=dialogue.get("speaker", ""),
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
    shot_ids = [p.stem for p in sorted((path / "03_shots").glob("*.json"))]
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_batch.py tests/test_cli_batch.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run the full test suite so far**

Run: `pytest -v`
Expected: PASS (all tests from Tasks 1–14)

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/batch.py src/ai_film/cli.py tests/test_batch.py tests/test_cli_batch.py
git commit -m "feat: add bounded-concurrency batch generation (generate-all)"
```

---

### Task 15: End-to-end golden-path integration test

**Files:**
- Test: `tests/test_golden_path.py`

**Interfaces:**
- Consumes: the full CLI surface from Tasks 12–13 (`init`, `check-continuity`, `approve-generation`, `generate-image`, `generate-video`, `status`, `render`).
- Produces: nothing new — this task adds no source code, only the automated proxy for the spec's v1 acceptance checklist (§12), using mock providers for job/cost-gate/logging correctness and real tiny `ffmpeg`-generated video files for the render step (mock providers write placeholder bytes that aren't valid video, so this test substitutes real media before rendering — mocks prove the bookkeeping is correct, not media validity, which is `ffmpeg`'s job to validate).

This is the last task in this plan. It exercises the whole pipeline in one run and is the automated stand-in for manually walking through the spec's acceptance checklist end-to-end.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_golden_path.py
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.shot_store import load_shot, save_shot

runner = CliRunner()


def _shot(shot_id: str, duration_seconds: float = 1.0) -> dict:
    return {
        "schema_version": "1.0",
        "id": shot_id,
        "status": "draft",
        "duration_seconds": duration_seconds,
        "continuity": {"status": "pending", "checked_at": None, "issues": []},
        "action": "girl steps out of darkness",
        "visual": {"style": "cinematic sci-fi"},
        "camera": {"shot": "close_up", "movement": "slow_push_in"},
        "characters": [],
        "generation": {
            "image": {"status": "pending", "attempts": 0},
            "video": {"status": "pending", "attempts": 0},
            "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }


def _overwrite_with_real_mp4(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=32x32:d=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "1", str(path),
        ],
        check=True, capture_output=True,
    )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_golden_path_produces_playable_final_video(tmp_path: Path):
    project_dir = tmp_path / "project"

    # 1. project initialized
    result = runner.invoke(app, ["init", "The Last Ship", "--path", str(project_dir)])
    assert result.exit_code == 0
    assert (project_dir / "config.json").exists()

    config = json.loads((project_dir / "config.json").read_text())
    for stage in config["providers"]:
        config["providers"][stage]["provider"] = "mock"
    (project_dir / "config.json").write_text(json.dumps(config))

    shot_ids = ["S01_SH01", "S01_SH02"]
    for shot_id in shot_ids:
        save_shot(project_dir / "03_shots" / f"{shot_id}.json", _shot(shot_id))

    # 2. shot.json validates against schema
    result = runner.invoke(app, ["validate", "--path", str(project_dir)])
    assert result.exit_code == 0

    # 3. continuity check passes
    for shot_id in shot_ids:
        result = runner.invoke(
            app, ["check-continuity", "--shot", shot_id, "--status", "passed", "--path", str(project_dir)]
        )
        assert result.exit_code == 0

    # 4. cost gate blocks generation until approved
    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 1

    # 5. approval unblocks it, and is recorded immutably
    result = runner.invoke(
        app,
        ["approve-generation", "--scope", "storyboard", "--targets", ",".join(shot_ids),
         "--estimated-cost", "0.50", "--path", str(project_dir)],
    )
    assert result.exit_code == 0
    approval_logs = list((project_dir / "99_logs" / "approvals").glob("*.json"))
    assert len(approval_logs) == 1

    # 6. provider jobs submit and complete for every shot
    for shot_id in shot_ids:
        result = runner.invoke(app, ["generate-image", "--shot", shot_id, "--path", str(project_dir)])
        assert result.exit_code == 0, result.output
        result = runner.invoke(app, ["generate-video", "--shot", shot_id, "--path", str(project_dir)])
        assert result.exit_code == 0, result.output

    # 7. artifacts exist; substitute real, valid video bytes for the render step
    #    (mock providers proved the job/cost-gate/logging bookkeeping above; ffmpeg
    #    now proves the render step works against real media)
    for shot_id in shot_ids:
        shot = load_shot(project_dir / "03_shots" / f"{shot_id}.json")
        video_path = Path(shot["generation"]["video"]["artifact"]["path"])
        assert video_path.exists()
        _overwrite_with_real_mp4(project_dir / video_path if not video_path.is_absolute() else video_path)

    # 8. ai-film status reports all shots completed
    result = runner.invoke(app, ["status", "--path", str(project_dir)])
    assert result.exit_code == 0
    assert result.output.count("completed") >= len(shot_ids)

    # 9. render preflight passes, render succeeds, final video exists and is playable
    result = runner.invoke(app, ["render", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output

    final_path = project_dir / "final" / "reel_001.mp4"
    assert final_path.exists()
    assert final_path.stat().st_size > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", str(final_path)],
        capture_output=True, text=True,
    )
    assert probe.returncode == 0
```

- [ ] **Step 2: Run test to verify it fails or is skipped**

Run: `pytest tests/test_golden_path.py -v`
Expected: If `ffmpeg` is installed, this should already PASS at this point, since every command it exercises was implemented in Tasks 1–13 — this task adds no new source code, only the integration proof. If it fails, that means an earlier task's CLI wiring has a bug; fix the earlier task, don't patch around it here. If `ffmpeg` isn't installed, it SKIPS.

- [ ] **Step 3: No implementation step — this task is verification only**

If Step 2 failed instead of passing or skipping, identify which earlier task's command produced the wrong output (`generate-image`/`generate-video` exit codes, `approve-generation` scope handling, or `status`/`render` output format) and fix that task's `cli.py` code directly, then re-run this test.

- [ ] **Step 4: Run the full test suite one final time**

Run: `pytest -v`
Expected: PASS (every test from Tasks 1–15; the handful of `ffmpeg`-dependent tests skip cleanly if `ffmpeg` isn't installed)

- [ ] **Step 5: Commit**

```bash
git add tests/test_golden_path.py
git commit -m "test: add end-to-end golden-path integration test"
```

---

## Follow-up Plan (not in scope here)

Once this plan ships and `ai-film`'s CLI contract is stable, write a second plan for the Claude Code integration layer: `.claude/skills/ai-film/SKILL.md`, the eight agents in `.claude/agents/`, and the `/ai-film-setup` / `/create-film` commands in `.claude/commands/` (spec §3, §8). That plan is prompt-authoring work verified against the golden path manually (per spec §12's acceptance checklist run through Claude Code itself), not pytest — a different plan shape, deliberately sequenced after this one so agent instructions can reference this CLI's real, tested flags instead of guessed ones.
