# Human Interaction Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `propose → review → refine → lock` candidate loop (multi-candidate image generation, a local HTML review gallery, image editing with prompt-regeneration fallback, and locking a pick into the existing artifact system) as the first implementation slice of the human interaction model, scoped to character/environment reference images and shot storyboard images.

**Architecture:** A new `CandidateSet` storage layer (`candidate_store.py`) sits entirely upstream of the existing, already-tested artifact-write path (`generation.<stage>.artifact` in `shot.json`, or `reference.png` for characters/environments). Two new provider-Protocol methods (`get_results`, required; `submit_edit`/`supports_edit`, optional-with-explicit-capability-check) extend `ImageProvider` additively — no existing method signature changes. A new service module (`candidate_service.py`) reuses the existing cost gate (`is_approved`), retry engine (`run_job`), and attempt logging (`write_attempt_log`) exactly as `generation_service.py` already does, writing to the candidate pool instead of `shot.json`. Four new CLI commands (`generate-candidates`, `review`, `select-candidate`, `edit-candidate`) wire this into the existing `ai-film` CLI following its established command-writing pattern.

**Tech Stack:** Python 3.11+, existing `ai_film` package (typer, jsonschema, requests), stdlib `webbrowser` for opening the review gallery, stdlib `tempfile`/`os.replace` for atomic writes. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-22-human-interaction-model-design.md` (approved for implementation) — this plan implements that spec's first slice (image candidates only: characters, environments, shot storyboards). It builds on the already-merged core engine at `src/ai_film/` (93 tests passing on `master` as of this plan's authoring).

## Global Constraints

- No existing `ImageProvider`/`VideoProvider`/`AudioProvider` method signature changes — only additive new methods (spec §4).
- No changes to `generate-image`/`generate-video`/`generate-voice`/`generate-sfx`/`generate-music`/`render`/`status`/`validate` behavior — all 93 existing tests must keep passing unmodified (spec §5).
- Candidate generation and editing go through the same cost gate (`is_approved`/`CostGateError`) every existing `generate-*` command already uses — no bypass path (spec Goals, §5).
- `--count N` means one `GenerationJob` returning N images (matches fal's `num_images` semantics), not N independent requests — all N candidates from one call share the same `job.id` and the same (currently-null) `estimated_cost` (spec §3).
- `candidates.json` writes are atomic: write-to-temp-file + `os.replace()`, no lock file (spec §3).
- `selected` on a `CandidateSet` is a current-selection pointer, not an immutable lock — `select-candidate` can be re-run to change a prior pick, no `--force` flag needed (spec §3, §7).
- Video/audio candidate review, whole-film preview, interactive/clickable browser review, publishing, full selection history, a general provider `capabilities()` registry, and mask/multi-image editing are explicitly out of scope for this plan (spec §9) — do not build them.

---

### Task 1: `ImageEditRequest` dataclass and `num_candidates` field

**Files:**
- Modify: `src/ai_film/models.py`
- Test: `tests/test_models.py` (new file)

**Interfaces:**
- Consumes: nothing new (foundational).
- Produces: `ai_film.models.ImageEditRequest(base_image_path: str, instruction: str, mask_path: str | None = None, reference_paths: list[str] = [])`; `ImageGenerationRequest` gains `num_candidates: int = 1` as a new field (all other fields and their order unchanged, so every existing call site that constructs `ImageGenerationRequest` with keyword arguments keeps working).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
from ai_film.models import ImageEditRequest, ImageGenerationRequest


def test_image_generation_request_defaults_num_candidates_to_one():
    request = ImageGenerationRequest(prompt="a girl", model="nano-banana")
    assert request.num_candidates == 1


def test_image_generation_request_accepts_explicit_num_candidates():
    request = ImageGenerationRequest(prompt="a girl", model="nano-banana", num_candidates=4)
    assert request.num_candidates == 4


def test_image_edit_request_defaults():
    request = ImageEditRequest(base_image_path="candidates/001.png", instruction="warmer lighting")
    assert request.mask_path is None
    assert request.reference_paths == []


def test_image_edit_request_is_immutable_reference_paths_per_instance():
    a = ImageEditRequest(base_image_path="x.png", instruction="a")
    b = ImageEditRequest(base_image_path="y.png", instruction="b")
    a.reference_paths.append("z.png")
    assert b.reference_paths == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'ImageEditRequest' from 'ai_film.models'` (and `TypeError: __init__() got an unexpected keyword argument 'num_candidates'` once that import is fixed)

- [ ] **Step 3: Write minimal implementation**

In `src/ai_film/models.py`, change the `ImageGenerationRequest` dataclass to:

```python
@dataclass
class ImageGenerationRequest:
    prompt: str
    model: str
    num_candidates: int = 1
    reference_paths: list[str] = field(default_factory=list)
    output_path: str = ""
```

Add a new dataclass immediately after `ImageGenerationResult`:

```python
@dataclass
class ImageEditRequest:
    base_image_path: str
    instruction: str
    mask_path: str | None = None
    reference_paths: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `pytest -q`
Expected: PASS (93 existing + 4 new = 97 passed). `ImageGenerationRequest`'s existing call sites (in `generation_service.py`, `cli.py`, and their tests) all use keyword arguments, so inserting `num_candidates` before `reference_paths` does not break them.

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/models.py tests/test_models.py
git commit -m "feat: add ImageEditRequest and num_candidates field"
```

---

### Task 2: `ImageProvider` Protocol additions + `MockImageProvider` implementation

**Files:**
- Modify: `src/ai_film/providers/base.py`
- Modify: `src/ai_film/providers/mock/image.py`
- Test: `tests/providers/test_mock_providers.py`

**Interfaces:**
- Consumes: `ai_film.models.{ImageEditRequest, ImageGenerationRequest, ImageGenerationResult, GenerationJob, Capability}` (Task 1 + existing).
- Produces: `ImageProvider` Protocol gains `get_results(self, job: GenerationJob, output_dir: str) -> list[ImageGenerationResult]` (required), `supports_edit(self) -> bool` (required), `submit_edit(self, request: ImageEditRequest) -> GenerationJob` (required on the Protocol, but only ever called after `supports_edit()` returns `True`). `MockImageProvider.__init__` gains `supports_edit: bool = False` so tests can configure it; `MockImageProvider.get_results(job, output_dir)` writes `request.num_candidates` placeholder files into `output_dir` named `result_1.png`, `result_2.png`, ...; `MockImageProvider.submit_edit(request)` raises `NotImplementedError` if `supports_edit=False`, otherwise behaves like `submit()` but records the edit request for `get_result` to consume.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/providers/test_mock_providers.py
from ai_film.models import ImageEditRequest


def test_mock_image_provider_get_results_writes_num_candidates_files(tmp_path: Path):
    provider = MockImageProvider()
    output_dir = tmp_path / "candidates"
    request = ImageGenerationRequest(
        prompt="a girl", model="nano-banana", num_candidates=3, reference_paths=[],
    )
    job = provider.submit(request)
    provider.poll(job)
    results = provider.get_results(job, str(output_dir))
    assert len(results) == 3
    for result in results:
        assert Path(result.artifact_path).exists()
        assert Path(result.artifact_path).parent == output_dir
        assert result.size_bytes > 0


def test_mock_image_provider_supports_edit_defaults_false():
    provider = MockImageProvider()
    assert provider.supports_edit() is False


def test_mock_image_provider_submit_edit_raises_when_unsupported(tmp_path: Path):
    provider = MockImageProvider()
    request = ImageEditRequest(base_image_path=str(tmp_path / "001.png"), instruction="warmer")
    with pytest.raises(NotImplementedError):
        provider.submit_edit(request)


def test_mock_image_provider_submit_edit_completes_when_supported(tmp_path: Path):
    provider = MockImageProvider(supports_edit=True)
    assert provider.supports_edit() is True
    base_image = tmp_path / "001.png"
    base_image.write_bytes(b"MOCK-PNG-DATA")
    request = ImageEditRequest(base_image_path=str(base_image), instruction="warmer lighting")
    job = provider.submit_edit(request)
    assert provider.poll(job) == JobStatus.COMPLETED
    result = provider.get_result(job)
    assert Path(result.artifact_path).exists()
```

`tests/providers/test_mock_providers.py` already imports `pytest`, `Path`, `JobStatus`, `ImageGenerationRequest`, and `MockImageProvider` at module scope — only the `ImageEditRequest` import above is new.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/providers/test_mock_providers.py -v`
Expected: FAIL — `AttributeError: 'MockImageProvider' object has no attribute 'get_results'`

- [ ] **Step 3: Write minimal implementation**

In `src/ai_film/providers/base.py`, replace the `ImageProvider` Protocol with:

```python
from ai_film.models import ImageEditRequest  # add to the existing import block


class ImageProvider(Protocol):
    def submit(self, request: ImageGenerationRequest) -> GenerationJob: ...
    def poll(self, job: GenerationJob) -> JobStatus: ...
    def get_result(self, job: GenerationJob) -> ImageGenerationResult: ...
    def get_results(self, job: GenerationJob, output_dir: str) -> list[ImageGenerationResult]: ...
    def supports_edit(self) -> bool: ...
    def submit_edit(self, request: ImageEditRequest) -> GenerationJob: ...
```

In `src/ai_film/providers/mock/image.py`, replace the whole file with:

```python
from __future__ import annotations

from pathlib import Path

from ai_film.errors import ProviderError
from ai_film.models import (
    Capability,
    GenerationJob,
    ImageEditRequest,
    ImageGenerationRequest,
    ImageGenerationResult,
    JobStatus,
)


class MockImageProvider:
    """In-memory image provider for tests. No network calls."""

    def __init__(
        self,
        fail_first_n_submits: int = 0,
        polls_until_complete: int = 1,
        supports_edit: bool = False,
    ):
        self.fail_first_n_submits = fail_first_n_submits
        self.polls_until_complete = polls_until_complete
        self._supports_edit = supports_edit
        self._submit_calls = 0
        self._poll_counts: dict[str, int] = {}
        self._requests: dict[str, ImageGenerationRequest] = {}
        self._edits: dict[str, ImageEditRequest] = {}

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
        if job.id in self._edits:
            request = self._edits[job.id]
            output_path = Path(request.base_image_path).with_name(
                Path(request.base_image_path).stem + "_edited.png"
            )
        else:
            request = self._requests[job.id]
            output_path = Path(request.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"MOCK-PNG-DATA")
        return ImageGenerationResult(
            artifact_path=str(output_path), size_bytes=output_path.stat().st_size
        )

    def get_results(self, job: GenerationJob, output_dir: str) -> list[ImageGenerationResult]:
        request = self._requests[job.id]
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for i in range(1, request.num_candidates + 1):
            path = out_dir / f"result_{i}.png"
            path.write_bytes(b"MOCK-PNG-DATA")
            results.append(
                ImageGenerationResult(artifact_path=str(path), size_bytes=path.stat().st_size)
            )
        return results

    def supports_edit(self) -> bool:
        return self._supports_edit

    def submit_edit(self, request: ImageEditRequest) -> GenerationJob:
        if not self._supports_edit:
            raise NotImplementedError("MockImageProvider(supports_edit=False) cannot edit")
        self._submit_calls += 1
        job_id = f"mock-edit-{self._submit_calls}"
        self._edits[job_id] = request
        self._poll_counts[job_id] = 0
        return GenerationJob(provider="mock", id=job_id, capability=Capability.IMAGE)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/providers/test_mock_providers.py -v`
Expected: PASS (all tests in the file, including the 4 new ones)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `pytest -q`
Expected: PASS. `get_results`/`supports_edit`/`submit_edit` are new methods; `submit`/`poll`/`get_result` are unchanged in signature and existing behavior (the `get_result` body's new `if job.id in self._edits` branch is additive — for every job created via the pre-existing `submit()` path, `self._edits` never contains that job's id, so the `else` branch runs exactly as before).

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/providers/base.py src/ai_film/providers/mock/image.py tests/providers/test_mock_providers.py
git commit -m "feat: add get_results/supports_edit/submit_edit to ImageProvider"
```

---

### Task 3: `FalImageProvider` implementation

**Files:**
- Modify: `src/ai_film/providers/fal/image.py`
- Test: `tests/providers/test_fal_providers.py`

**Interfaces:**
- Consumes: `ai_film.providers.fal.client.{submit, poll, result, download}` (existing, unchanged); `ImageEditRequest` (Task 1); `ImageProvider` Protocol (Task 2).
- Produces: `FalImageProvider.get_results(job, output_dir) -> list[ImageGenerationResult]`, `FalImageProvider.supports_edit() -> bool` (returns `True`), `FalImageProvider.submit_edit(request: ImageEditRequest) -> GenerationJob`.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/providers/test_fal_providers.py
from unittest.mock import MagicMock, patch

from ai_film.models import ImageEditRequest


def test_fal_image_provider_supports_edit_returns_true():
    provider = FalImageProvider()
    assert provider.supports_edit() is True


@patch("ai_film.providers.fal.client.requests")
def test_fal_image_provider_get_results_downloads_all_images(mock_requests, tmp_path: Path, monkeypatch):
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
    result_response.json.return_value = {
        "images": [
            {"url": "https://cdn.fal.run/a.png"},
            {"url": "https://cdn.fal.run/b.png"},
            {"url": "https://cdn.fal.run/c.png"},
        ]
    }
    download_responses = [
        MagicMock(status_code=200, content=b"IMG-A"),
        MagicMock(status_code=200, content=b"IMG-B"),
        MagicMock(status_code=200, content=b"IMG-C"),
    ]

    mock_requests.post.return_value = submit_response
    mock_requests.get.side_effect = [status_response, result_response, *download_responses]

    provider = FalImageProvider()
    request = ImageGenerationRequest(
        prompt="a girl", model="nano-banana", num_candidates=3, reference_paths=[],
    )
    job = provider.submit(request)
    provider.poll(job)
    output_dir = tmp_path / "candidates"
    results = provider.get_results(job, str(output_dir))

    assert len(results) == 3
    assert {r.artifact_path for r in results} == {
        str(output_dir / "result_1.png"),
        str(output_dir / "result_2.png"),
        str(output_dir / "result_3.png"),
    }
    assert (output_dir / "result_1.png").read_bytes() == b"IMG-A"
    assert (output_dir / "result_2.png").read_bytes() == b"IMG-B"
    assert (output_dir / "result_3.png").read_bytes() == b"IMG-C"


@patch("ai_film.providers.fal.client.requests")
def test_fal_image_provider_submit_edit_full_lifecycle(mock_requests, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")

    submit_response = MagicMock(status_code=200)
    submit_response.json.return_value = {
        "request_id": "req-edit-1",
        "status_url": "https://queue.fal.run/status/req-edit-1",
        "response_url": "https://queue.fal.run/result/req-edit-1",
    }
    status_response = MagicMock(status_code=200)
    status_response.json.return_value = {"status": "COMPLETED"}
    result_response = MagicMock(status_code=200)
    result_response.json.return_value = {"images": [{"url": "https://cdn.fal.run/edited.png"}]}
    download_response = MagicMock(status_code=200, content=b"EDITED-PNG")

    mock_requests.post.return_value = submit_response
    mock_requests.get.side_effect = [status_response, result_response, download_response]

    provider = FalImageProvider()
    base_image = tmp_path / "001.png"
    base_image.write_bytes(b"ORIGINAL-PNG")
    output_path = tmp_path / "candidates" / "edited.png"
    request = ImageEditRequest(
        base_image_path=str(base_image), instruction="black jacket instead of white",
    )
    job = provider.submit_edit(request)
    assert job.provider == "fal"
    assert provider.poll(job) == JobStatus.COMPLETED
    result = provider.get_result(job)
    assert Path(result.artifact_path).read_bytes() == b"EDITED-PNG"
```

`tests/providers/test_fal_providers.py` already imports `Path`, `MagicMock`, `patch`, `JobStatus`, `ImageGenerationRequest`, `FalImageProvider`, `Capability` at module scope — only `ImageEditRequest` needs adding to the import line.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/providers/test_fal_providers.py -v`
Expected: FAIL — `AttributeError: 'FalImageProvider' object has no attribute 'get_results'`

- [ ] **Step 3: Write minimal implementation**

Replace `src/ai_film/providers/fal/image.py` with:

```python
from __future__ import annotations

from pathlib import Path

from ai_film.models import (
    Capability, GenerationJob, ImageEditRequest, ImageGenerationRequest,
    ImageGenerationResult, JobStatus,
)
from ai_film.providers.fal import client

MODEL_TO_APP_ID = {
    "nano-banana": "fal-ai/nano-banana-2",
    "nano-banana-pro": "fal-ai/nano-banana-pro",
}

EDIT_APP_ID = "fal-ai/nano-banana-2"


class FalImageProvider:
    def __init__(self):
        self._jobs: dict[str, tuple[str, str, ImageGenerationRequest]] = {}
        self._edits: dict[str, tuple[str, str, ImageEditRequest]] = {}

    def submit(self, request: ImageGenerationRequest) -> GenerationJob:
        app_id = MODEL_TO_APP_ID[request.model]
        input_data = {"prompt": request.prompt, "num_images": request.num_candidates}
        if request.reference_paths:
            input_data["image_urls"] = request.reference_paths
        job, status_url, response_url = client.submit(app_id, input_data, Capability.IMAGE)
        self._jobs[job.id] = (status_url, response_url, request)
        return job

    def poll(self, job: GenerationJob) -> JobStatus:
        status_url = self._status_url(job)
        return client.poll(status_url)

    def get_result(self, job: GenerationJob) -> ImageGenerationResult:
        if job.id in self._edits:
            _, response_url, request = self._edits[job.id]
            body = client.result(response_url)
            image_url = body["images"][0]["url"]
            output_path = str(Path(request.base_image_path).with_name(
                Path(request.base_image_path).stem + "_edited.png"
            ))
            size_bytes = client.download(image_url, output_path)
            return ImageGenerationResult(artifact_path=output_path, size_bytes=size_bytes)
        _, response_url, request = self._jobs[job.id]
        body = client.result(response_url)
        image_url = body["images"][0]["url"]
        size_bytes = client.download(image_url, request.output_path)
        return ImageGenerationResult(artifact_path=request.output_path, size_bytes=size_bytes)

    def get_results(self, job: GenerationJob, output_dir: str) -> list[ImageGenerationResult]:
        _, response_url, _ = self._jobs[job.id]
        body = client.result(response_url)
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for i, image in enumerate(body["images"], start=1):
            path = out_dir / f"result_{i}.png"
            size_bytes = client.download(image["url"], str(path))
            results.append(ImageGenerationResult(artifact_path=str(path), size_bytes=size_bytes))
        return results

    def supports_edit(self) -> bool:
        return True

    def submit_edit(self, request: ImageEditRequest) -> GenerationJob:
        input_data = {
            "prompt": request.instruction,
            "image_urls": [request.base_image_path],
        }
        job, status_url, response_url = client.submit(EDIT_APP_ID, input_data, Capability.IMAGE)
        self._edits[job.id] = (status_url, response_url, request)
        return job

    def _status_url(self, job: GenerationJob) -> str:
        if job.id in self._edits:
            return self._edits[job.id][0]
        return self._jobs[job.id][0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/providers/test_fal_providers.py -v`
Expected: PASS (all tests in the file, including the 3 new ones)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `pytest -q`
Expected: PASS. `submit`/`poll`/`get_result`'s pre-existing call paths are unchanged for any job not present in the new `self._edits` dict (which stays empty unless `submit_edit` was called).

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/providers/fal/image.py tests/providers/test_fal_providers.py
git commit -m "feat: implement get_results/supports_edit/submit_edit in FalImageProvider"
```

---

### Task 4: `candidate_store.py` — target resolution, atomic storage, candidate lookup

**Files:**
- Create: `src/ai_film/candidate_store.py`
- Test: `tests/test_candidate_store.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure stdlib: `json`, `os`, `tempfile`, `pathlib.Path`).
- Produces:
  - `ai_film.candidate_store.target_dir(project_dir: Path, target: str) -> Path` — resolves `"character:<name>"` → `project_dir/assets/characters/<name>`, `"env:<name>"` → `project_dir/assets/environments/<name>`, `"shot:<id>:image"` → `project_dir/04_storyboard/candidates/<id>`; raises `ValueError` for an unknown target kind or an unsupported shot stage (only `"image"` is supported in this plan).
  - `ai_film.candidate_store.scope_for_target(target: str) -> str` — `"bibles"` for `character:*`/`env:*`, `"storyboard"` for `shot:*:*`.
  - `ai_film.candidate_store.load_candidate_set(project_dir: Path, target: str) -> dict` — returns `{"target": target, "candidates": [], "selected": None}` if no `candidates.json` exists yet.
  - `ai_film.candidate_store.save_candidate_set(project_dir: Path, target: str, candidate_set: dict) -> None` — atomic write (temp file + `os.replace`).
  - `ai_film.candidate_store.next_candidate_id(candidate_set: dict) -> str` — zero-padded 3-digit string, one past the current max numeric id (`"001"` if empty).
  - `ai_film.candidate_store.add_candidates(project_dir: Path, target: str, entries: list[dict]) -> dict` — loads, appends, atomically saves, returns the updated candidate set.
  - `ai_film.candidate_store.get_candidate(candidate_set: dict, candidate_id: str) -> dict` — raises `ValueError` if not found.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_candidate_store.py
import json
from pathlib import Path

import pytest

from ai_film.candidate_store import (
    add_candidates,
    get_candidate,
    load_candidate_set,
    next_candidate_id,
    save_candidate_set,
    scope_for_target,
    target_dir,
)


def test_target_dir_character(tmp_path: Path):
    assert target_dir(tmp_path, "character:girl") == tmp_path / "assets" / "characters" / "girl"


def test_target_dir_env(tmp_path: Path):
    assert target_dir(tmp_path, "env:corridor") == tmp_path / "assets" / "environments" / "corridor"


def test_target_dir_shot_image(tmp_path: Path):
    assert target_dir(tmp_path, "shot:S01_SH01:image") == (
        tmp_path / "04_storyboard" / "candidates" / "S01_SH01"
    )


def test_target_dir_rejects_unknown_kind(tmp_path: Path):
    with pytest.raises(ValueError):
        target_dir(tmp_path, "voice:girl")


def test_target_dir_rejects_unsupported_shot_stage(tmp_path: Path):
    with pytest.raises(ValueError):
        target_dir(tmp_path, "shot:S01_SH01:video")


def test_scope_for_target():
    assert scope_for_target("character:girl") == "bibles"
    assert scope_for_target("env:corridor") == "bibles"
    assert scope_for_target("shot:S01_SH01:image") == "storyboard"


def test_load_candidate_set_returns_empty_shape_when_missing(tmp_path: Path):
    candidate_set = load_candidate_set(tmp_path, "character:girl")
    assert candidate_set == {"target": "character:girl", "candidates": [], "selected": None}


def test_save_and_load_round_trip(tmp_path: Path):
    candidate_set = {"target": "character:girl", "candidates": [{"id": "001"}], "selected": None}
    save_candidate_set(tmp_path, "character:girl", candidate_set)
    loaded = load_candidate_set(tmp_path, "character:girl")
    assert loaded == candidate_set


def test_save_candidate_set_writes_valid_json_even_after_interrupted_prior_write(tmp_path: Path, monkeypatch):
    target = "character:girl"
    save_candidate_set(tmp_path, target, {"target": target, "candidates": [], "selected": None})

    original_replace = __import__("os").replace
    call_count = {"n": 0}

    def flaky_replace(src, dst):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OSError("simulated interruption")
        return original_replace(src, dst)

    monkeypatch.setattr("os.replace", flaky_replace)
    with pytest.raises(OSError):
        save_candidate_set(tmp_path, target, {"target": target, "candidates": [{"id": "001"}], "selected": None})

    # The original file must still be intact and fully parseable — an interrupted
    # write must never leave a corrupted or partial candidates.json.
    directory = target_dir(tmp_path, target)
    on_disk = json.loads((directory / "candidates.json").read_text())
    assert on_disk == {"target": target, "candidates": [], "selected": None}


def test_next_candidate_id_starts_at_001():
    assert next_candidate_id({"candidates": []}) == "001"


def test_next_candidate_id_increments_past_max():
    candidate_set = {"candidates": [{"id": "001"}, {"id": "003"}]}
    assert next_candidate_id(candidate_set) == "004"


def test_add_candidates_appends_and_persists(tmp_path: Path):
    target = "character:girl"
    entry = {"id": "001", "path": "candidates/001.png"}
    updated = add_candidates(tmp_path, target, [entry])
    assert updated["candidates"] == [entry]
    reloaded = load_candidate_set(tmp_path, target)
    assert reloaded["candidates"] == [entry]


def test_get_candidate_found_and_missing():
    candidate_set = {"candidates": [{"id": "001", "path": "x.png"}]}
    assert get_candidate(candidate_set, "001")["path"] == "x.png"
    with pytest.raises(ValueError):
        get_candidate(candidate_set, "999")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_candidate_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_film.candidate_store'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ai_film/candidate_store.py
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

_TARGET_KINDS = ("character", "env", "shot")


def target_dir(project_dir: Path, target: str) -> Path:
    parts = target.split(":")
    kind = parts[0]
    if kind == "character":
        if len(parts) != 2:
            raise ValueError(f"malformed character target {target!r}")
        return project_dir / "assets" / "characters" / parts[1]
    if kind == "env":
        if len(parts) != 2:
            raise ValueError(f"malformed env target {target!r}")
        return project_dir / "assets" / "environments" / parts[1]
    if kind == "shot":
        if len(parts) != 3:
            raise ValueError(f"malformed shot target {target!r}, expected shot:<id>:<stage>")
        shot_id, stage = parts[1], parts[2]
        if stage != "image":
            raise ValueError(
                f"unsupported shot target stage {stage!r} in {target!r}; "
                f"only 'image' is supported in this release"
            )
        return project_dir / "04_storyboard" / "candidates" / shot_id
    raise ValueError(f"unknown target kind {kind!r} in {target!r}; expected one of {_TARGET_KINDS}")


def scope_for_target(target: str) -> str:
    kind = target.split(":", 1)[0]
    return "bibles" if kind in ("character", "env") else "storyboard"


def _candidates_json_path(project_dir: Path, target: str) -> Path:
    return target_dir(project_dir, target) / "candidates.json"


def load_candidate_set(project_dir: Path, target: str) -> dict:
    path = _candidates_json_path(project_dir, target)
    if not path.exists():
        return {"target": target, "candidates": [], "selected": None}
    return json.loads(path.read_text())


def save_candidate_set(project_dir: Path, target: str, candidate_set: dict) -> None:
    directory = target_dir(project_dir, target)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "candidates.json"
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".candidates-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(candidate_set, handle, indent=2, ensure_ascii=False)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def next_candidate_id(candidate_set: dict) -> str:
    existing = [int(c["id"]) for c in candidate_set["candidates"]]
    return f"{(max(existing) + 1) if existing else 1:03d}"


def add_candidates(project_dir: Path, target: str, entries: list[dict]) -> dict:
    candidate_set = load_candidate_set(project_dir, target)
    candidate_set["candidates"].extend(entries)
    save_candidate_set(project_dir, target, candidate_set)
    return candidate_set


def get_candidate(candidate_set: dict, candidate_id: str) -> dict:
    for candidate in candidate_set["candidates"]:
        if candidate["id"] == candidate_id:
            return candidate
    raise ValueError(f"no candidate with id {candidate_id!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_candidate_store.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `pytest -q`
Expected: PASS (all prior tests + 4 (Task 1) + 4 (Task 2) + 3 (Task 3) + 14 (this task) = 118 passed)

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/candidate_store.py tests/test_candidate_store.py
git commit -m "feat: add candidate_store with target resolution and atomic storage"
```

---

### Task 5: `candidate_service.generate_candidates()`

**Files:**
- Create: `src/ai_film/services/candidate_service.py`
- Test: `tests/services/test_candidate_service.py`

**Interfaces:**
- Consumes: `ai_film.candidate_store.{target_dir, scope_for_target, load_candidate_set, next_candidate_id, add_candidates}` (Task 4); `ai_film.approval.is_approved` (existing); `ai_film.jobs.run_job` (existing); `ai_film.logging_store.write_attempt_log` (existing); `ai_film.errors.{CostGateError, ProviderError}` (existing); `ai_film.models.ImageGenerationRequest` (Task 1); an `ImageProvider`-shaped object (Task 2/3) with `submit`, `poll`, `get_results`.
- Produces: `ai_film.services.candidate_service.generate_candidates(project_dir: Path, target: str, provider, prompt: str, model: str, count: int, provider_name: str, max_attempts: int = 3, poll_interval_seconds: float = 0.0) -> dict` — raises `CostGateError` if the target's scope isn't approved; on success returns the added entries as `{"target": target, "added": [id, id, ...]}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_candidate_service.py
from pathlib import Path

import pytest

from ai_film.approval import approve_generation
from ai_film.candidate_store import load_candidate_set
from ai_film.errors import CostGateError, ProviderError
from ai_film.providers.mock.image import MockImageProvider
from ai_film.services.candidate_service import generate_candidates


def _init_config(project_dir: Path) -> None:
    (project_dir / "config.json").write_text('{"providers": {}, "generation": {}}')


def test_generate_candidates_blocked_without_approval(tmp_path: Path):
    _init_config(tmp_path)
    provider = MockImageProvider()
    with pytest.raises(CostGateError):
        generate_candidates(
            project_dir=tmp_path, target="character:girl", provider=provider,
            prompt="a girl, sci-fi style", model="nano-banana", count=4,
            provider_name="mock",
        )
    assert provider._submit_calls == 0


def test_generate_candidates_writes_n_candidates_once_approved(tmp_path: Path):
    _init_config(tmp_path)
    approve_generation(tmp_path, "bibles", ["character:girl"], estimated_cost=0.32)
    provider = MockImageProvider()

    result = generate_candidates(
        project_dir=tmp_path, target="character:girl", provider=provider,
        prompt="a girl, sci-fi style", model="nano-banana", count=4,
        provider_name="mock",
    )

    assert result["target"] == "character:girl"
    assert result["added"] == ["001", "002", "003", "004"]
    candidate_set = load_candidate_set(tmp_path, "character:girl")
    assert len(candidate_set["candidates"]) == 4
    for candidate in candidate_set["candidates"]:
        assert candidate["operation"] == "generate"
        assert candidate["parent"] is None
        assert candidate["prompt"] == "a girl, sci-fi style"
        assert candidate["provider"] == "mock"
        assert candidate["model"] == "nano-banana"
        artifact_path = tmp_path / "assets" / "characters" / "girl" / candidate["path"]
        assert artifact_path.exists()


def test_generate_candidates_from_one_call_share_job_id_and_cost(tmp_path: Path):
    _init_config(tmp_path)
    approve_generation(tmp_path, "bibles", ["character:girl"], estimated_cost=0.32)
    provider = MockImageProvider()

    generate_candidates(
        project_dir=tmp_path, target="character:girl", provider=provider,
        prompt="a girl", model="nano-banana", count=3, provider_name="mock",
    )

    candidate_set = load_candidate_set(tmp_path, "character:girl")
    job_ids = {c["job"]["id"] for c in candidate_set["candidates"]}
    assert len(job_ids) == 1, "all candidates from one call must share the same job id"
    costs = {c["estimated_cost"] for c in candidate_set["candidates"]}
    assert len(costs) == 1, "all candidates from one call must share the same estimated_cost"


def test_generate_candidates_second_call_continues_id_sequence(tmp_path: Path):
    _init_config(tmp_path)
    approve_generation(tmp_path, "bibles", ["character:girl"], estimated_cost=0.32)
    provider = MockImageProvider()

    generate_candidates(
        project_dir=tmp_path, target="character:girl", provider=provider,
        prompt="a girl", model="nano-banana", count=2, provider_name="mock",
    )
    result = generate_candidates(
        project_dir=tmp_path, target="character:girl", provider=provider,
        prompt="a girl, take two", model="nano-banana", count=2, provider_name="mock",
    )

    assert result["added"] == ["003", "004"]


def test_generate_candidates_raises_provider_error_after_exhausted_retries(tmp_path: Path):
    _init_config(tmp_path)
    approve_generation(tmp_path, "bibles", ["character:girl"], estimated_cost=0.32)
    provider = MockImageProvider(fail_first_n_submits=10)

    with pytest.raises(ProviderError):
        generate_candidates(
            project_dir=tmp_path, target="character:girl", provider=provider,
            prompt="a girl", model="nano-banana", count=2, provider_name="mock",
            max_attempts=2,
        )


def test_generate_candidates_writes_attempt_log(tmp_path: Path):
    _init_config(tmp_path)
    approve_generation(tmp_path, "bibles", ["character:girl"], estimated_cost=0.32)
    provider = MockImageProvider()

    generate_candidates(
        project_dir=tmp_path, target="character:girl", provider=provider,
        prompt="a girl", model="nano-banana", count=2, provider_name="mock",
    )

    logs = list((tmp_path / "99_logs" / "character_girl").glob("*_candidates_attempt01.json"))
    assert len(logs) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/services/test_candidate_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_film.services.candidate_service'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ai_film/services/candidate_service.py
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ai_film.approval import is_approved
from ai_film.candidate_store import (
    add_candidates,
    load_candidate_set,
    next_candidate_id,
    scope_for_target,
    target_dir,
)
from ai_film.errors import CostGateError
from ai_film.jobs import run_job
from ai_film.logging_store import write_attempt_log
from ai_film.models import GenerationJob, ImageGenerationRequest


def _log_group(target: str) -> str:
    return target.replace(":", "_")


def generate_candidates(
    project_dir: Path,
    target: str,
    provider,
    prompt: str,
    model: str,
    count: int,
    provider_name: str,
    max_attempts: int = 3,
    poll_interval_seconds: float = 0.0,
) -> dict:
    scope = scope_for_target(target)
    if not is_approved(project_dir, scope, target):
        raise CostGateError(
            f"target {target} is not approved for generation (scope={scope}); "
            f"run `ai-film approve-generation --scope {scope} --targets {target},...` first"
        )

    output_dir = target_dir(project_dir, target) / "candidates"
    request = ImageGenerationRequest(prompt=prompt, model=model, num_candidates=count)

    def on_attempt(attempt: int, job: GenerationJob | None, outcome: str) -> None:
        write_attempt_log(
            project_dir,
            _log_group(target),
            "candidates",
            attempt,
            job={"provider": job.provider, "id": job.id} if job else None,
            request={"provider": provider_name, "model": model, "count": count},
            response={"outcome": outcome},
            outcome=outcome,
        )

    job_result = run_job(
        submit_fn=lambda: provider.submit(request),
        poll_fn=provider.poll,
        get_result_fn=lambda job: provider.get_results(job, str(output_dir)),
        max_attempts=max_attempts,
        poll_interval_seconds=poll_interval_seconds,
        on_attempt=on_attempt,
    )

    candidate_set = load_candidate_set(project_dir, target)
    created_at = datetime.now(timezone.utc).isoformat()
    entries = []
    for raw_result in job_result.result:
        candidate_id = next_candidate_id({"candidates": candidate_set["candidates"] + entries})
        final_path = output_dir / f"{candidate_id}.png"
        Path(raw_result.artifact_path).rename(final_path)
        entries.append({
            "id": candidate_id,
            "path": f"candidates/{candidate_id}.png",
            "provider": provider_name,
            "model": model,
            "prompt": prompt,
            "parent": None,
            "operation": "generate",
            "job": {"provider": job_result.job.provider, "id": job_result.job.id},
            "estimated_cost": None,
            "created_at": created_at,
        })

    add_candidates(project_dir, target, entries)
    return {"target": target, "added": [e["id"] for e in entries]}
```

Note on `estimated_cost: None`: no cost-estimation logic exists anywhere in the core engine yet (only `generation_approval.<scope>.estimated_cost`, a single value for the whole approved batch, exists) — this mirrors the existing precedent of `_image_artifact`'s `"sha256": None` in `generation_service.py`, a documented-but-not-yet-computed field, not a gap introduced by this task.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/services/test_candidate_service.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `pytest -q`
Expected: PASS (all prior + 6 new)

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/services/candidate_service.py tests/services/test_candidate_service.py
git commit -m "feat: add candidate_service.generate_candidates with cost gate and retries"
```

---

### Task 6: `candidate_service.select_candidate()`

**Files:**
- Modify: `src/ai_film/services/generation_service.py` (rename `_project_relative_path` to `project_relative_path`, update its one internal call site — no behavior change)
- Modify: `src/ai_film/services/candidate_service.py`
- Test: `tests/services/test_candidate_service.py`

**Interfaces:**
- Consumes: `ai_film.services.generation_service.project_relative_path(path_str: str, project_dir: Path) -> str` (renamed, previously `_project_relative_path`); `ai_film.candidate_store.{target_dir, load_candidate_set, save_candidate_set, get_candidate}` (Task 4); `ai_film.shot_store.{load_shot, save_shot}` (existing).
- Produces: `ai_film.services.candidate_service.select_candidate(project_dir: Path, target: str, candidate_id: str) -> dict` — sets `candidate_set["selected"]`, copies the chosen file to the canonical location (`reference.png` for `character:*`/`env:*`; `shot.json`'s `generation.image.artifact` for `shot:*:image`), returns `{"target": target, "selected": candidate_id, "canonical_path": <project-relative str>}`.

- [ ] **Step 1: Write the failing test**

In `src/ai_film/services/generation_service.py`, rename `_project_relative_path` to `project_relative_path` (drop the leading underscore) and update its single call site inside `run_generation_stage` (`artifact["path"], project_dir` line) accordingly — this is a pure rename, no logic change. Confirm the existing test suite still passes after the rename before writing new tests:

Run: `pytest tests/services/test_generation_service.py -v`
Expected: PASS (unchanged — the function's behavior and all its existing internal call sites are identical, only its name changed)

Now add the failing tests for `select_candidate`:

```python
# Append to tests/services/test_candidate_service.py
import json

from ai_film.services.candidate_service import add_candidates_for_test_setup  # see note below
from ai_film.services.candidate_service import select_candidate
from ai_film.shot_store import load_shot, save_shot


def _seed_character_candidate(tmp_path: Path, target: str = "character:girl") -> None:
    directory = tmp_path / "assets" / "characters" / "girl" / "candidates"
    directory.mkdir(parents=True)
    (directory / "001.png").write_bytes(b"CANDIDATE-ONE")
    add_candidates(tmp_path, target, [{
        "id": "001", "path": "candidates/001.png", "provider": "mock", "model": "nano-banana",
        "prompt": "a girl", "parent": None, "operation": "generate", "job": None,
        "estimated_cost": None, "created_at": "2026-08-22T00:00:00Z",
    }])


def test_select_candidate_copies_to_reference_png_for_character_target(tmp_path: Path):
    _seed_character_candidate(tmp_path)

    result = select_candidate(tmp_path, "character:girl", "001")

    assert result == {
        "target": "character:girl", "selected": "001",
        "canonical_path": "assets/characters/girl/reference.png",
    }
    reference_path = tmp_path / "assets" / "characters" / "girl" / "reference.png"
    assert reference_path.read_bytes() == b"CANDIDATE-ONE"
    candidate_set = load_candidate_set(tmp_path, "character:girl")
    assert candidate_set["selected"] == "001"


def test_select_candidate_can_be_re_run_to_change_pick(tmp_path: Path):
    _seed_character_candidate(tmp_path)
    directory = tmp_path / "assets" / "characters" / "girl" / "candidates"
    (directory / "002.png").write_bytes(b"CANDIDATE-TWO")
    add_candidates(tmp_path, "character:girl", [{
        "id": "002", "path": "candidates/002.png", "provider": "mock", "model": "nano-banana",
        "prompt": "a girl v2", "parent": "001", "operation": "edit", "job": None,
        "estimated_cost": None, "created_at": "2026-08-22T00:05:00Z",
    }])

    select_candidate(tmp_path, "character:girl", "001")
    select_candidate(tmp_path, "character:girl", "002")

    reference_path = tmp_path / "assets" / "characters" / "girl" / "reference.png"
    assert reference_path.read_bytes() == b"CANDIDATE-TWO"
    assert load_candidate_set(tmp_path, "character:girl")["selected"] == "002"


def _seed_shot(tmp_path: Path) -> None:
    shot = {
        "schema_version": "1.0", "id": "S01_SH01", "status": "draft", "duration_seconds": 3,
        "continuity": {"status": "pending", "checked_at": None, "issues": []},
        "generation": {
            "image": {"status": "pending", "attempts": 0},
            "video": {"status": "pending", "attempts": 0},
            "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", shot)


def _seed_shot_candidate(tmp_path: Path) -> None:
    directory = tmp_path / "04_storyboard" / "candidates" / "S01_SH01" / "candidates"
    directory.mkdir(parents=True)
    (directory / "001.png").write_bytes(b"SHOT-CANDIDATE")
    add_candidates(tmp_path, "shot:S01_SH01:image", [{
        "id": "001", "path": "candidates/001.png", "provider": "mock", "model": "nano-banana",
        "prompt": "wide shot", "parent": None, "operation": "generate", "job": None,
        "estimated_cost": None, "created_at": "2026-08-22T00:00:00Z",
    }])


def test_select_candidate_writes_into_shot_json_for_shot_target(tmp_path: Path):
    _seed_shot(tmp_path)
    _seed_shot_candidate(tmp_path)

    result = select_candidate(tmp_path, "shot:S01_SH01:image", "001")

    assert result["canonical_path"] == "04_storyboard/S01_SH01.png"
    artifact_path = tmp_path / "04_storyboard" / "S01_SH01.png"
    assert artifact_path.read_bytes() == b"SHOT-CANDIDATE"
    shot = load_shot(tmp_path / "03_shots" / "S01_SH01.json")
    assert shot["generation"]["image"]["status"] == "completed"
    assert shot["generation"]["image"]["artifact"]["path"] == "04_storyboard/S01_SH01.png"
```

Remove the placeholder `add_candidates_for_test_setup` import line above — it was a note-to-self, not real code; the test file already imports `add_candidates` from `ai_film.candidate_store` at the top from Task 4/5's tests. Only add:

```python
from ai_film.candidate_store import add_candidates, load_candidate_set
```
to the top of `tests/services/test_candidate_service.py` if not already present (Task 5 already imported `load_candidate_set`; add `add_candidates` alongside it).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/services/test_candidate_service.py -v`
Expected: FAIL — `ImportError: cannot import name 'select_candidate' from 'ai_film.services.candidate_service'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/ai_film/services/candidate_service.py`:

```python
import shutil

from ai_film.services.generation_service import project_relative_path
from ai_film.shot_store import load_shot, save_shot


def select_candidate(project_dir: Path, target: str, candidate_id: str) -> dict:
    candidate_set = load_candidate_set(project_dir, target)
    candidate = get_candidate(candidate_set, candidate_id)
    candidate_set["selected"] = candidate_id
    save_candidate_set(project_dir, target, candidate_set)

    directory = target_dir(project_dir, target)
    source_path = directory / candidate["path"]
    kind = target.split(":", 1)[0]

    if kind in ("character", "env"):
        dest_path = directory / "reference.png"
        shutil.copy(source_path, dest_path)
    else:
        shot_id = target.split(":")[1]
        shot_path = project_dir / "03_shots" / f"{shot_id}.json"
        shot = load_shot(shot_path)
        dest_path = project_dir / "04_storyboard" / f"{shot_id}.png"
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source_path, dest_path)
        shot["generation"]["image"] = {
            **shot["generation"].get("image", {}),
            "status": "completed",
            "artifact": {
                "path": project_relative_path(str(dest_path), project_dir),
                "size_bytes": dest_path.stat().st_size,
                "sha256": None,
            },
        }
        save_shot(shot_path, shot)

    return {
        "target": target,
        "selected": candidate_id,
        "canonical_path": project_relative_path(str(dest_path), project_dir),
    }
```

Add `get_candidate` and `save_candidate_set` to this file's existing `from ai_film.candidate_store import (...)` block (alongside `add_candidates`, `load_candidate_set`, `next_candidate_id`, `scope_for_target`, `target_dir` already imported by Task 5).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/services/test_candidate_service.py -v`
Expected: PASS (all tests in the file, including the 3 new ones)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `pytest -q`
Expected: PASS. Confirm specifically that `tests/services/test_generation_service.py` still passes after the rename in Step 1 (already checked, but re-verify as part of the full run).

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/services/generation_service.py src/ai_film/services/candidate_service.py tests/services/test_candidate_service.py
git commit -m "feat: add candidate_service.select_candidate, export project_relative_path"
```

---

### Task 7: `candidate_service.edit_candidate()`

**Files:**
- Modify: `src/ai_film/services/candidate_service.py`
- Test: `tests/services/test_candidate_service.py`

**Interfaces:**
- Consumes: `ai_film.models.ImageEditRequest` (Task 1); `provider.supports_edit()`/`provider.submit_edit()` (Task 2/3); everything already imported into `candidate_service.py` from Tasks 5-6.
- Produces: `ai_film.services.candidate_service.edit_candidate(project_dir: Path, target: str, candidate_id: str, instruction: str, provider, provider_name: str, model: str, max_attempts: int = 3, poll_interval_seconds: float = 0.0) -> dict` — raises `CostGateError` if unapproved; adds exactly one new candidate with `parent=candidate_id`, `operation="edit"`; uses `submit_edit` if `provider.supports_edit()` else falls back to `submit` with the instruction appended to the source candidate's prompt.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/services/test_candidate_service.py
from ai_film.services.candidate_service import edit_candidate


def test_edit_candidate_blocked_without_approval(tmp_path: Path):
    _init_config(tmp_path)
    _seed_character_candidate(tmp_path)
    provider = MockImageProvider(supports_edit=True)

    with pytest.raises(CostGateError):
        edit_candidate(
            project_dir=tmp_path, target="character:girl", candidate_id="001",
            instruction="black jacket", provider=provider, provider_name="mock",
            model="nano-banana",
        )


def test_edit_candidate_uses_submit_edit_when_supported(tmp_path: Path):
    _init_config(tmp_path)
    approve_generation(tmp_path, "bibles", ["character:girl"], estimated_cost=0.1)
    _seed_character_candidate(tmp_path)
    provider = MockImageProvider(supports_edit=True)

    entry = edit_candidate(
        project_dir=tmp_path, target="character:girl", candidate_id="001",
        instruction="black jacket instead of white", provider=provider,
        provider_name="mock", model="nano-banana",
    )

    assert entry["parent"] == "001"
    assert entry["operation"] == "edit"
    assert entry["id"] == "002"
    candidate_set = load_candidate_set(tmp_path, "character:girl")
    assert len(candidate_set["candidates"]) == 2
    new_path = tmp_path / "assets" / "characters" / "girl" / entry["path"]
    assert new_path.exists()


def test_edit_candidate_falls_back_to_regeneration_when_unsupported(tmp_path: Path):
    _init_config(tmp_path)
    approve_generation(tmp_path, "bibles", ["character:girl"], estimated_cost=0.1)
    _seed_character_candidate(tmp_path)
    provider = MockImageProvider(supports_edit=False)

    entry = edit_candidate(
        project_dir=tmp_path, target="character:girl", candidate_id="001",
        instruction="black jacket instead of white", provider=provider,
        provider_name="mock", model="nano-banana",
    )

    assert entry["parent"] == "001"
    assert entry["operation"] == "edit"
    assert "black jacket instead of white" in entry["prompt"]
    assert entry["prompt"].startswith("a girl")  # original candidate's prompt is preserved as a prefix
    new_path = tmp_path / "assets" / "characters" / "girl" / entry["path"]
    assert new_path.exists()


def test_edit_candidate_on_missing_id_raises_clear_error(tmp_path: Path):
    _init_config(tmp_path)
    approve_generation(tmp_path, "bibles", ["character:girl"], estimated_cost=0.1)
    _seed_character_candidate(tmp_path)
    provider = MockImageProvider(supports_edit=True)

    with pytest.raises(ValueError):
        edit_candidate(
            project_dir=tmp_path, target="character:girl", candidate_id="999",
            instruction="black jacket", provider=provider, provider_name="mock",
            model="nano-banana",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/services/test_candidate_service.py -v`
Expected: FAIL — `ImportError: cannot import name 'edit_candidate' from 'ai_film.services.candidate_service'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/ai_film/services/candidate_service.py`:

```python
from ai_film.models import ImageEditRequest


def edit_candidate(
    project_dir: Path,
    target: str,
    candidate_id: str,
    instruction: str,
    provider,
    provider_name: str,
    model: str,
    max_attempts: int = 3,
    poll_interval_seconds: float = 0.0,
) -> dict:
    scope = scope_for_target(target)
    if not is_approved(project_dir, scope, target):
        raise CostGateError(
            f"target {target} is not approved for generation (scope={scope}); "
            f"run `ai-film approve-generation --scope {scope} --targets {target},...` first"
        )

    directory = target_dir(project_dir, target)
    candidate_set = load_candidate_set(project_dir, target)
    source = get_candidate(candidate_set, candidate_id)
    output_dir = directory / "candidates"
    base_image_path = str(directory / source["path"])

    def on_attempt(attempt: int, job: GenerationJob | None, outcome: str) -> None:
        write_attempt_log(
            project_dir, _log_group(target), "edit-candidate", attempt,
            job={"provider": job.provider, "id": job.id} if job else None,
            request={"provider": provider_name, "model": model, "instruction": instruction},
            response={"outcome": outcome}, outcome=outcome,
        )

    if provider.supports_edit():
        edit_request = ImageEditRequest(base_image_path=base_image_path, instruction=instruction)
        job_result = run_job(
            submit_fn=lambda: provider.submit_edit(edit_request),
            poll_fn=provider.poll,
            get_result_fn=provider.get_result,
            max_attempts=max_attempts, poll_interval_seconds=poll_interval_seconds,
            on_attempt=on_attempt,
        )
        prompt_used = instruction
    else:
        merged_prompt = f"{source['prompt']}, {instruction}"
        request = ImageGenerationRequest(
            prompt=merged_prompt, model=model,
            output_path=str(output_dir / "edit_regeneration.png"),
        )
        job_result = run_job(
            submit_fn=lambda: provider.submit(request),
            poll_fn=provider.poll,
            get_result_fn=provider.get_result,
            max_attempts=max_attempts, poll_interval_seconds=poll_interval_seconds,
            on_attempt=on_attempt,
        )
        prompt_used = merged_prompt

    new_id = next_candidate_id(candidate_set)
    final_path = output_dir / f"{new_id}.png"
    Path(job_result.result.artifact_path).rename(final_path)
    entry = {
        "id": new_id,
        "path": f"candidates/{new_id}.png",
        "provider": provider_name,
        "model": model,
        "prompt": prompt_used,
        "parent": candidate_id,
        "operation": "edit",
        "job": {"provider": job_result.job.provider, "id": job_result.job.id},
        "estimated_cost": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    add_candidates(project_dir, target, [entry])
    return entry
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/services/test_candidate_service.py -v`
Expected: PASS (all tests in the file, including the 4 new ones)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `pytest -q`
Expected: PASS (all prior + 4 new)

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/services/candidate_service.py tests/services/test_candidate_service.py
git commit -m "feat: add candidate_service.edit_candidate with submit_edit-or-regenerate fallback"
```

---

### Task 8: Review gallery builder (`review_gallery.py`)

**Files:**
- Create: `src/ai_film/review_gallery.py`
- Test: `tests/test_review_gallery.py`

**Interfaces:**
- Consumes: `ai_film.candidate_store.{load_candidate_set, target_dir}` (Task 4).
- Produces: `ai_film.review_gallery.build_gallery(project_dir: Path, target: str) -> Path` — raises `ValueError` if the candidate pool is empty; writes `<target_dir>/candidates/review.html` and returns its path. `ai_film.review_gallery.open_in_browser(html_path: Path) -> None` — thin wrapper over `webbrowser.open`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_review_gallery.py
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_film.candidate_store import add_candidates
from ai_film.review_gallery import build_gallery, open_in_browser


def _seed_candidates(tmp_path: Path) -> None:
    directory = tmp_path / "assets" / "characters" / "girl" / "candidates"
    directory.mkdir(parents=True)
    (directory / "001.png").write_bytes(b"ONE")
    (directory / "002.png").write_bytes(b"TWO")
    add_candidates(tmp_path, "character:girl", [
        {
            "id": "001", "path": "candidates/001.png", "provider": "mock", "model": "nano-banana",
            "prompt": "a girl", "parent": None, "operation": "generate", "job": None,
            "estimated_cost": None, "created_at": "2026-08-22T00:00:00Z",
        },
        {
            "id": "002", "path": "candidates/002.png", "provider": "mock", "model": "nano-banana",
            "prompt": "a girl, black jacket", "parent": "001", "operation": "edit", "job": None,
            "estimated_cost": None, "created_at": "2026-08-22T00:05:00Z",
        },
    ])


def test_build_gallery_raises_on_empty_pool(tmp_path: Path):
    with pytest.raises(ValueError):
        build_gallery(tmp_path, "character:girl")


def test_build_gallery_writes_html_listing_every_candidate_with_id_and_parent(tmp_path: Path):
    _seed_candidates(tmp_path)

    html_path = build_gallery(tmp_path, "character:girl")

    assert html_path == tmp_path / "assets" / "characters" / "girl" / "candidates" / "review.html"
    content = html_path.read_text()
    assert "001" in content
    assert "002" in content
    assert "001" in content.split("002")[1] or "parent" in content.lower() or "edit of 001" in content
    assert 'src="001.png"' in content
    assert 'src="002.png"' in content


def test_build_gallery_marks_the_selected_candidate(tmp_path: Path):
    _seed_candidates(tmp_path)
    from ai_film.candidate_store import load_candidate_set, save_candidate_set
    candidate_set = load_candidate_set(tmp_path, "character:girl")
    candidate_set["selected"] = "002"
    save_candidate_set(tmp_path, "character:girl", candidate_set)

    html_path = build_gallery(tmp_path, "character:girl")

    content = html_path.read_text()
    assert "SELECTED" in content.upper()


@patch("ai_film.review_gallery.webbrowser")
def test_open_in_browser_calls_webbrowser_open_with_file_uri(mock_webbrowser, tmp_path: Path):
    html_path = tmp_path / "review.html"
    html_path.write_text("<html></html>")

    open_in_browser(html_path)

    mock_webbrowser.open.assert_called_once()
    call_arg = mock_webbrowser.open.call_args[0][0]
    assert call_arg.startswith("file://")
    assert str(html_path.resolve()) in call_arg
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_review_gallery.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_film.review_gallery'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ai_film/review_gallery.py
from __future__ import annotations

import webbrowser
from pathlib import Path

from ai_film.candidate_store import load_candidate_set, target_dir


def build_gallery(project_dir: Path, target: str) -> Path:
    candidate_set = load_candidate_set(project_dir, target)
    if not candidate_set["candidates"]:
        raise ValueError(
            f"no candidates for target {target!r}; run `ai-film generate-candidates` first"
        )

    directory = target_dir(project_dir, target)
    out_path = directory / "candidates" / "review.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_render_html(target, candidate_set))
    return out_path


def _render_html(target: str, candidate_set: dict) -> str:
    selected = candidate_set.get("selected")
    figures = []
    for candidate in candidate_set["candidates"]:
        filename = Path(candidate["path"]).name
        caption = candidate["id"]
        if candidate.get("parent"):
            caption += f" (edit of {candidate['parent']})"
        if selected == candidate["id"]:
            caption += " ★ SELECTED"
        figures.append(
            f'<figure style="margin:8px">'
            f'<img src="{filename}" style="max-width:300px;display:block">'
            f'<figcaption>{caption}</figcaption>'
            f'</figure>'
        )
    body = "".join(figures)
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>Candidates: {target}</title></head>"
        f"<body><h1>{target}</h1>"
        f'<div style="display:flex;flex-wrap:wrap;gap:16px">{body}</div>'
        "</body></html>"
    )


def open_in_browser(html_path: Path) -> None:
    webbrowser.open(f"file://{html_path.resolve()}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_review_gallery.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `pytest -q`
Expected: PASS (all prior + 5 new)

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/review_gallery.py tests/test_review_gallery.py
git commit -m "feat: add review_gallery HTML builder and browser opener"
```

---

### Task 9: CLI commands — `generate-candidates`, `review`, `select-candidate`, `edit-candidate`

**Files:**
- Modify: `src/ai_film/cli.py`
- Test: `tests/test_cli_candidate_commands.py` (new file — kept separate from the existing 242-line `tests/test_cli_generation_commands.py` per the file-size/one-clear-responsibility guideline; candidates are a distinct feature area)

**Interfaces:**
- Consumes: `ai_film.services.candidate_service.{generate_candidates, select_candidate, edit_candidate}` (Tasks 5-7); `ai_film.review_gallery.{build_gallery, open_in_browser}` (Task 8); `ai_film.candidate_store.scope_for_target` (Task 4); `ai_film.providers.registry.resolve_provider`, `ai_film.errors.{CostGateError, ProviderError}`, `ai_film.models.Capability`, `ai_film.prompts.build_image_prompt`, `ai_film.shot_store.load_shot` (all existing, unchanged imports already present in `cli.py`).
- Produces: four new `ai-film` CLI commands (below). No changes to any existing command.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_candidate_commands.py
import json
from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app

runner = CliRunner()


def _init_mock_project(tmp_path: Path) -> Path:
    project_dir = tmp_path / "project"
    runner.invoke(app, ["init", "Test Film", "--path", str(project_dir)])
    config = json.loads((project_dir / "config.json").read_text())
    for stage in config["providers"]:
        config["providers"][stage]["provider"] = "mock"
    (project_dir / "config.json").write_text(json.dumps(config))
    return project_dir


def _approve_bibles(project_dir: Path, target: str = "character:girl") -> None:
    runner.invoke(
        app,
        ["approve-generation", "--scope", "bibles", "--targets", target, "--path", str(project_dir)],
    )


def test_generate_candidates_blocked_without_approval(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)

    result = runner.invoke(
        app,
        ["generate-candidates", "--target", "character:girl", "--count", "4",
         "--prompt", "a girl, sci-fi style", "--path", str(project_dir)],
    )

    assert result.exit_code == 1


def test_generate_candidates_succeeds_after_approval(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    _approve_bibles(project_dir)

    result = runner.invoke(
        app,
        ["generate-candidates", "--target", "character:girl", "--count", "4",
         "--prompt", "a girl, sci-fi style", "--path", str(project_dir)],
    )

    assert result.exit_code == 0, result.output
    for i in range(1, 5):
        assert (project_dir / "assets" / "characters" / "girl" / "candidates" / f"{i:03d}.png").exists()


def test_generate_candidates_requires_prompt_for_character_target(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    _approve_bibles(project_dir)

    result = runner.invoke(
        app,
        ["generate-candidates", "--target", "character:girl", "--count", "4",
         "--path", str(project_dir)],
    )

    assert result.exit_code == 1
    assert "--prompt" in result.output


def test_review_errors_clearly_on_empty_pool(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)

    result = runner.invoke(app, ["review", "--target", "character:girl", "--path", str(project_dir)])

    assert result.exit_code == 1


def test_review_builds_gallery_without_opening_browser_in_tests(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    _approve_bibles(project_dir)
    runner.invoke(
        app,
        ["generate-candidates", "--target", "character:girl", "--count", "2",
         "--prompt", "a girl", "--path", str(project_dir)],
    )
    opened = {}
    monkeypatch.setattr(
        "ai_film.cli.open_in_browser", lambda path: opened.setdefault("path", path)
    )

    result = runner.invoke(app, ["review", "--target", "character:girl", "--path", str(project_dir)])

    assert result.exit_code == 0, result.output
    assert opened["path"] == (
        project_dir / "assets" / "characters" / "girl" / "candidates" / "review.html"
    )


def test_select_candidate_locks_in_the_pick(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    _approve_bibles(project_dir)
    runner.invoke(
        app,
        ["generate-candidates", "--target", "character:girl", "--count", "2",
         "--prompt", "a girl", "--path", str(project_dir)],
    )

    result = runner.invoke(
        app,
        ["select-candidate", "--target", "character:girl", "--id", "002", "--path", str(project_dir)],
    )

    assert result.exit_code == 0, result.output
    assert (project_dir / "assets" / "characters" / "girl" / "reference.png").exists()


def test_edit_candidate_blocked_without_approval(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    _approve_bibles(project_dir)
    runner.invoke(
        app,
        ["generate-candidates", "--target", "character:girl", "--count", "1",
         "--prompt", "a girl", "--path", str(project_dir)],
    )
    # Revoke approval by re-approving a different, unrelated target only.
    config_path = project_dir / "config.json"
    config = json.loads(config_path.read_text())
    config["generation_approval"] = {}
    config_path.write_text(json.dumps(config))

    result = runner.invoke(
        app,
        ["edit-candidate", "--target", "character:girl", "--id", "001",
         "--instruction", "black jacket", "--path", str(project_dir)],
    )

    assert result.exit_code == 1


def test_edit_candidate_adds_a_new_candidate(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    _approve_bibles(project_dir)
    runner.invoke(
        app,
        ["generate-candidates", "--target", "character:girl", "--count", "1",
         "--prompt", "a girl", "--path", str(project_dir)],
    )

    result = runner.invoke(
        app,
        ["edit-candidate", "--target", "character:girl", "--id", "001",
         "--instruction", "black jacket instead of white", "--path", str(project_dir)],
    )

    assert result.exit_code == 0, result.output
    assert (project_dir / "assets" / "characters" / "girl" / "candidates" / "002.png").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_candidate_commands.py -v`
Expected: FAIL — `Error: No such command 'generate-candidates'` (and similarly for the other three)

- [ ] **Step 3: Write minimal implementation**

Add these imports to the top of `src/ai_film/cli.py`, alongside the existing `from ai_film.services.generation_service import (...)` block:

```python
from ai_film.review_gallery import build_gallery, open_in_browser
from ai_film.services.candidate_service import (
    edit_candidate as edit_candidate_service,
    generate_candidates as generate_candidates_service,
    select_candidate as select_candidate_service,
)
```

Add these four commands to `src/ai_film/cli.py`, after the existing `generate-all` command and before the `if __name__ == "__main__":` guard:

```python
def _shot_default_prompt(path: Path, shot_id: str) -> str:
    shot_data = load_shot(path / "03_shots" / f"{shot_id}.json")
    return build_image_prompt(shot_data)


@app.command(name="generate-candidates")
def generate_candidates_cmd(
    target: str = typer.Option(..., "--target"),
    count: int = typer.Option(..., "--count"),
    prompt: str = typer.Option(None, "--prompt"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Generate N image candidates for a character:/env:/shot: target (cost-gated)."""
    stage_config, gen_config = _stage_config(path, "image")

    if prompt is None:
        if target.startswith("shot:"):
            shot_id = target.split(":")[1]
            prompt = _shot_default_prompt(path, shot_id)
        else:
            typer.echo("--prompt is required for character:/env: targets", err=True)
            raise typer.Exit(code=1)

    def _run():
        provider = resolve_provider(Capability.IMAGE, stage_config["provider"])
        return generate_candidates_service(
            project_dir=path, target=target, provider=provider,
            prompt=prompt, model=stage_config["model"], count=count,
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"],
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
```

`cli.py` never imports `scope_for_target` — scope resolution is owned entirely by
`candidate_service.py` (Tasks 5/7), consistent with how `_SCOPE_BY_STAGE` in
`generation_service.py` is never referenced from `cli.py` either.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_candidate_commands.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `pytest -q`
Expected: PASS (all prior tests, including every pre-existing CLI test in `tests/test_cli_generation_commands.py`, `tests/test_cli_project_commands.py`, `tests/test_cli_batch.py`, `tests/test_cli_skeleton.py`, plus the 9 new candidate CLI tests)

- [ ] **Step 6: Commit**

```bash
git add src/ai_film/cli.py tests/test_cli_candidate_commands.py
git commit -m "feat: add generate-candidates, review, select-candidate, edit-candidate CLI commands"
```

---

### Task 10: End-to-end integration test — the full propose → review → refine → lock loop

**Files:**
- Test: `tests/test_candidate_golden_path.py` (new file — mirrors the core engine's `tests/test_golden_path.py` in spirit: the automated proxy for this plan's acceptance checklist)

**Interfaces:**
- Consumes: the full candidate CLI surface from Task 9 (`generate-candidates`, `review`, `edit-candidate`, `select-candidate`) plus existing commands (`init`, `approve-generation`, `status`).
- Produces: nothing new — this task adds no source code, only the integration proof that the whole loop works together via `CliRunner`, using `MockImageProvider` (no network, no cost) exactly as `test_golden_path.py` does for the core engine.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_candidate_golden_path.py
import json
from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.shot_store import load_shot

runner = CliRunner()


def test_full_candidate_loop_generate_review_edit_select(tmp_path: Path, monkeypatch):
    project_dir = tmp_path / "project"

    # 1. init and switch to mock providers
    result = runner.invoke(app, ["init", "The Last Ship", "--path", str(project_dir)])
    assert result.exit_code == 0
    config_path = project_dir / "config.json"
    config = json.loads(config_path.read_text())
    for stage in config["providers"]:
        config["providers"][stage]["provider"] = "mock"
    config_path.write_text(json.dumps(config))

    # 2. cost gate blocks candidate generation until approved
    result = runner.invoke(
        app,
        ["generate-candidates", "--target", "character:girl", "--count", "4",
         "--prompt", "a girl, cinematic sci-fi style", "--path", str(project_dir)],
    )
    assert result.exit_code == 1

    # 3. approve, then generate 4 candidates in one call
    result = runner.invoke(
        app,
        ["approve-generation", "--scope", "bibles", "--targets", "character:girl",
         "--path", str(project_dir)],
    )
    assert result.exit_code == 0

    result = runner.invoke(
        app,
        ["generate-candidates", "--target", "character:girl", "--count", "4",
         "--prompt", "a girl, cinematic sci-fi style", "--path", str(project_dir)],
    )
    assert result.exit_code == 0, result.output
    for i in range(1, 5):
        assert (
            project_dir / "assets" / "characters" / "girl" / "candidates" / f"{i:03d}.png"
        ).exists()

    # 4. review builds a gallery without opening a real browser in tests
    monkeypatch.setattr("ai_film.cli.open_in_browser", lambda path: None)
    result = runner.invoke(app, ["review", "--target", "character:girl", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    gallery_path = project_dir / "assets" / "characters" / "girl" / "candidates" / "review.html"
    assert gallery_path.exists()
    for i in range(1, 5):
        assert f"{i:03d}" in gallery_path.read_text()

    # 5. discuss + edit candidate 002 (regeneration fallback, since MockImageProvider
    #    defaults to supports_edit=False)
    result = runner.invoke(
        app,
        ["edit-candidate", "--target", "character:girl", "--id", "002",
         "--instruction", "black jacket instead of white", "--path", str(project_dir)],
    )
    assert result.exit_code == 0, result.output
    edited_path = project_dir / "assets" / "characters" / "girl" / "candidates" / "005.png"
    assert edited_path.exists()

    # 6. review again reflects the new candidate and its lineage
    result = runner.invoke(app, ["review", "--target", "character:girl", "--path", str(project_dir)])
    assert result.exit_code == 0
    gallery_content = gallery_path.read_text()
    assert "005" in gallery_content
    assert "edit of 002" in gallery_content

    # 7. select the edited candidate, locking it in
    result = runner.invoke(
        app,
        ["select-candidate", "--target", "character:girl", "--id", "005", "--path", str(project_dir)],
    )
    assert result.exit_code == 0, result.output
    reference_path = project_dir / "assets" / "characters" / "girl" / "reference.png"
    assert reference_path.exists()
    assert reference_path.read_bytes() == edited_path.read_bytes()

    # 8. changing the mind: re-select an earlier candidate overwrites the lock, no error
    result = runner.invoke(
        app,
        ["select-candidate", "--target", "character:girl", "--id", "001", "--path", str(project_dir)],
    )
    assert result.exit_code == 0
    candidate_001 = project_dir / "assets" / "characters" / "girl" / "candidates" / "001.png"
    assert reference_path.read_bytes() == candidate_001.read_bytes()

    # 9. none of the core engine's existing commands are affected: run the ordinary
    #    shot pipeline end to end alongside the candidate flow, to confirm the two
    #    coexist without interference (this is the "downstream stays unaware of
    #    candidates" guarantee from the spec, exercised for real).
    shot = {
        "schema_version": "1.0", "id": "S01_SH01", "status": "draft", "duration_seconds": 2,
        "continuity": {"status": "pending", "checked_at": None, "issues": []},
        "action": "girl steps out of darkness", "visual": {"style": "cinematic sci-fi"},
        "camera": {"shot": "close_up"}, "characters": [],
        "generation": {
            "image": {"status": "pending", "attempts": 0},
            "video": {"status": "not_required"},
            "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }
    (project_dir / "03_shots" / "S01_SH01.json").write_text(json.dumps(shot))
    result = runner.invoke(
        app,
        ["check-continuity", "--shot", "S01_SH01", "--status", "passed", "--path", str(project_dir)],
    )
    assert result.exit_code == 0
    result = runner.invoke(
        app,
        ["approve-generation", "--scope", "storyboard", "--targets", "S01_SH01",
         "--path", str(project_dir)],
    )
    assert result.exit_code == 0
    result = runner.invoke(
        app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir)]
    )
    assert result.exit_code == 0, result.output
    final_shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    assert final_shot["generation"]["image"]["status"] == "completed"
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `pytest tests/test_candidate_golden_path.py -v`
Expected: if Tasks 1-9 are all correctly implemented, this should already PASS — this task adds no new source code, only the integration proof. If it fails, that means an earlier task's wiring has a bug (most likely a mismatch between what `generate_candidates`/`edit_candidate`/`select_candidate` return and what `cli.py` expects, or an off-by-one in candidate ID numbering across the edit step). Fix the earlier task's code directly, don't patch around it here.

- [ ] **Step 3: No implementation step — this task is verification only**

If Step 2 failed, identify which earlier task's function is responsible (trace the failing assertion back through `cli.py` → `candidate_service.py` → `candidate_store.py`/`review_gallery.py`) and fix that task's code, then re-run this test.

- [ ] **Step 4: Run the full test suite one final time**

Run: `pytest -q`
Expected: PASS — every test from Tasks 1-10, plus all 93 pre-existing core-engine tests, all passing together.

- [ ] **Step 5: Commit**

```bash
git add tests/test_candidate_golden_path.py
git commit -m "test: add end-to-end candidate loop integration test"
```
