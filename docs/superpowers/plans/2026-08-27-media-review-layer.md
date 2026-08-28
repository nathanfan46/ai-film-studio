# Media Review Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every shot's `image`/`video`/`voice`/`sfx`/`music` generation stage a sequential version history, a timeline-aware feedback log, a static no-server review page to watch/listen from, and a cheap non-generative audio-offset fix — closing the video/audio review gap the human-interaction-model and agent-layer specs deliberately deferred.

**Architecture:** Four small, focused additions to `src/ai_film/`: an additive extension to the existing `run_generation_stage` shared path (version/history bookkeeping, via a new `archive_stage_artifact` helper reused by the audio-offset fix), two new store/service modules (`feedback_store.py`, `audio_fix.py`) following `candidate_store.py`'s plain-functions-over-JSON style, a new static-HTML builder (`media_review.py`) following `review_gallery.py`'s zero-dependency f-string approach, and four new `cli.py` commands wiring it all together. No existing command signature changes.

**Tech Stack:** Python, `jsonschema`, `typer`, `ffmpeg` (already a hard dependency via `render.py`) — no new dependency.

**Spec:** `docs/superpowers/specs/2026-08-27-media-review-design.md`

## Global Constraints

- No server, API, or new runtime dependency; `ffmpeg` is the one external tool relied on, already required by `render.py`.
- No existing CLI command's flags or behavior change.
- All `shot.json` schema changes are additive/non-required — every shot.json written before this plan remains valid with no migration.
- No divergent (N-way) candidate storage for video/audio — one current artifact per stage per shot, plus sequential version history.
- The review page is playback and reference only — no drag/trim/scrub-and-commit editing affordance.
- Feedback `target` is exactly one of `"video"`, `"voice"`, `"sfx"`, `"music"`, `"sync"` — enforced by `feedback_store.add_feedback_entry`, not a broader taxonomy.
- Old artifact files are moved into a sibling `history/` directory on replacement, never deleted.
- New JSON files use the same atomic-write pattern as `candidate_store.py` (`tempfile.mkstemp` + `os.replace`).

---

## File Structure

```
src/ai_film/
  schema.py                    (modify — version/history properties)
  services/generation_service.py  (modify — archive_stage_artifact + run_generation_stage bookkeeping)
  feedback_store.py            (new — feedback log CRUD)
  audio_fix.py                 (new — cheap ffmpeg audio-offset fix)
  media_review.py              (new — static review page builder)
  project.py                   (modify — add "07_review" to PROJECT_DIRS)
  cli.py                       (modify — 4 new commands)

tests/
  test_schema.py                          (extend)
  services/test_generation_service.py     (extend)
  test_feedback_store.py                  (new)
  test_audio_fix.py                       (new)
  test_media_review.py                    (new)
  test_project.py                         (extend)
  test_cli_media_review_commands.py       (new)
  test_media_review_golden_path.py        (new)
```

---

### Task 1: Schema — accept `version`/`history` on generation stages

**Files:**
- Modify: `src/ai_film/schema.py:5-24` (`GENERATION_STAGE_SCHEMA`)
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `GENERATION_STAGE_SCHEMA["properties"]` gains `"version"` (type `integer`) and `"history"` (type `array`) — no function signature changes.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_schema.py`:

```python
def test_generation_stage_rejects_non_integer_version():
    shot = _valid_shot()
    shot["generation"]["video"]["version"] = "two"
    errors = validate_shot(shot)
    assert len(errors) == 1


def test_generation_stage_accepts_version_and_history():
    shot = _valid_shot()
    shot["generation"]["video"] = {
        "status": "completed",
        "attempts": 1,
        "version": 2,
        "history": [
            {
                "version": 1,
                "provider": "fal",
                "model": "veo-3",
                "artifact": {
                    "path": "05_video/history/S01_SH01_v1.mp4",
                    "size_bytes": 100,
                    "sha256": None,
                    "duration_seconds": 5.0,
                },
                "superseded_at": "2026-08-27T09:00:00Z",
                "superseded_reason": "regenerate",
            }
        ],
    }
    assert validate_shot(shot) == []
```

- [ ] **Step 2: Run tests to verify the first one fails**

Run: `pytest tests/test_schema.py -v -k "version_and_history or non_integer_version"`

Expected: `test_generation_stage_rejects_non_integer_version` FAILS (`assert len(errors) == 1` sees `errors == []`, since no property named `"version"` is constrained yet — an unrecognized extra key currently passes validation silently). `test_generation_stage_accepts_version_and_history` already PASSES at this point too — that's expected, not a bug: it's a permissive-schema regression check, not the test driving this change. The rejects-test is the one that must go from failing to passing.

- [ ] **Step 3: Add the two properties**

In `src/ai_film/schema.py`, inside `GENERATION_STAGE_SCHEMA["properties"]`, add two entries (after `"model"`, before `"job"`):

```python
        "version": {"type": "integer"},
        "history": {"type": "array"},
```

- [ ] **Step 4: Run tests to verify both pass**

Run: `pytest tests/test_schema.py -v`

Expected: PASS (all tests in the file, including the two pre-existing suites and the two new ones).

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/schema.py tests/test_schema.py
git commit -m "feat: accept version/history fields on generation stages"
```

---

### Task 2: Version/history bookkeeping in `run_generation_stage`

**Files:**
- Modify: `src/ai_film/services/generation_service.py` (full file — imports, add `ArchiveResult`/`archive_stage_artifact`, change `run_generation_stage`)
- Test: `tests/services/test_generation_service.py`

**Interfaces:**
- Consumes: `project_relative_path` (already defined in this file, unchanged).
- Produces: `ArchiveResult` dataclass (`version: int`, `history: list[dict]`, `archived_path: Path | None`, `restore: Callable[[], None] | None`) and `archive_stage_artifact(project_dir: Path, stage_data: dict, superseded_reason: str) -> ArchiveResult`, both importable from `ai_film.services.generation_service` — Task 4's `audio_fix.py` consumes both directly.

- [ ] **Step 1: Write the failing tests**

Append to `tests/services/test_generation_service.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/services/test_generation_service.py -v -k "version_one or force_regenerate or restores_prior"`

Expected: all three FAIL — `KeyError: 'version'` (the stage dict has no `version` key yet).

- [ ] **Step 3: Implement**

Replace the full contents of `src/ai_film/services/generation_service.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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


@dataclass
class ArchiveResult:
    version: int
    history: list[dict]
    archived_path: Path | None
    restore: Callable[[], None] | None


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
    """
    history = list(stage_data.get("history", []))
    if stage_data.get("status") != "completed" or not stage_data.get("artifact"):
        return ArchiveResult(version=1, history=history, archived_path=None, restore=None)

    old_version = stage_data.get("version", 1)
    old_artifact = stage_data["artifact"]
    old_path = project_dir / old_artifact["path"]

    if not old_path.exists():
        history.append({
            "version": old_version,
            "provider": stage_data.get("provider"),
            "model": stage_data.get("model"),
            "artifact": old_artifact,
            "superseded_at": datetime.now(timezone.utc).isoformat(),
            "superseded_reason": superseded_reason,
        })
        return ArchiveResult(version=old_version + 1, history=history, archived_path=None, restore=None)

    history_dir = old_path.parent / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    archived_path = history_dir / f"{old_path.stem}_v{old_version}{old_path.suffix}"
    old_path.replace(archived_path)
    archived_artifact = {
        **old_artifact, "path": project_relative_path(str(archived_path), project_dir),
    }
    history.append({
        "version": old_version,
        "provider": stage_data.get("provider"),
        "model": stage_data.get("model"),
        "artifact": archived_artifact,
        "superseded_at": datetime.now(timezone.utc).isoformat(),
        "superseded_reason": superseded_reason,
    })

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

    archive = archive_stage_artifact(project_dir, stage_data, "regenerate")

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

    artifact = result_to_artifact(job_result.result)
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

Note: `project_relative_path` moved below `run_generation_stage`/`archive_stage_artifact` in this listing purely because `archive_stage_artifact` calls it — Python resolves the name at call time, not definition time, so its position in the file doesn't actually matter functionally; keep it wherever reads cleanest, this ordering is just what's shown above.

- [ ] **Step 4: Run the full test file to verify everything passes**

Run: `pytest tests/services/test_generation_service.py -v`

Expected: PASS — all pre-existing tests (`test_generate_image_blocked_without_approval`, `test_generate_image_succeeds_once_approved`, `test_generate_image_is_idempotent_by_default`, `test_generate_image_force_regenerates`, `test_generate_image_writes_attempt_log`, `test_generate_image_marks_stage_failed_after_exhausted_retries`) plus the three new ones.

Also run the candidate service tests, which import `project_relative_path` from this same module: `pytest tests/services/test_candidate_service.py -v` — Expected: PASS, unaffected.

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/services/generation_service.py tests/services/test_generation_service.py
git commit -m "feat: version and archive generation stage artifacts on regeneration"
```

---

### Task 3: `feedback_store.py` — the per-shot feedback log

**Files:**
- Create: `src/ai_film/feedback_store.py`
- Test: `tests/test_feedback_store.py`

**Interfaces:**
- Produces: `VALID_TARGETS = ("video", "voice", "sfx", "music", "sync")`, `feedback_path(project_dir: Path, shot_id: str) -> Path`, `load_feedback(project_dir: Path, shot_id: str) -> dict`, `save_feedback(project_dir: Path, shot_id: str, data: dict) -> None`, `add_feedback_entry(project_dir: Path, shot_id: str, target: str, note: str, at: float | None = None, range_start: float | None = None, range_end: float | None = None) -> dict`, `resolve_feedback_entry(project_dir: Path, shot_id: str, feedback_id: str, resolution: str | None = None) -> dict` — Task 5's CLI commands and Task 6's `media_review.py` both import from this module.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_feedback_store.py`:

```python
# tests/test_feedback_store.py
import json
from pathlib import Path

import pytest

from ai_film.feedback_store import (
    add_feedback_entry,
    feedback_path,
    load_feedback,
    resolve_feedback_entry,
    save_feedback,
)


def test_load_feedback_returns_empty_shape_when_missing(tmp_path: Path):
    data = load_feedback(tmp_path, "S01_SH01")
    assert data == {"shot_id": "S01_SH01", "entries": []}


def test_save_and_load_round_trip(tmp_path: Path):
    data = {"shot_id": "S01_SH01", "entries": [{"id": "FB-001"}]}
    save_feedback(tmp_path, "S01_SH01", data)
    assert load_feedback(tmp_path, "S01_SH01") == data


def test_save_feedback_writes_valid_json_even_after_interrupted_prior_write(tmp_path: Path, monkeypatch):
    save_feedback(tmp_path, "S01_SH01", {"shot_id": "S01_SH01", "entries": []})

    original_replace = __import__("os").replace
    call_count = {"n": 0}

    def flaky_replace(src, dst):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OSError("simulated interruption")
        return original_replace(src, dst)

    monkeypatch.setattr("os.replace", flaky_replace)
    with pytest.raises(OSError):
        save_feedback(tmp_path, "S01_SH01", {"shot_id": "S01_SH01", "entries": [{"id": "FB-001"}]})

    on_disk = json.loads(feedback_path(tmp_path, "S01_SH01").read_text())
    assert on_disk == {"shot_id": "S01_SH01", "entries": []}


def test_add_feedback_entry_assigns_incrementing_ids(tmp_path: Path):
    first = add_feedback_entry(tmp_path, "S01_SH01", target="video", note="a", at=1.0)
    second = add_feedback_entry(tmp_path, "S01_SH01", target="voice", note="b", at=2.0)
    assert first["id"] == "FB-001"
    assert second["id"] == "FB-002"


def test_add_feedback_entry_defaults_and_persists(tmp_path: Path):
    entry = add_feedback_entry(tmp_path, "S01_SH01", target="video", note="too calm", at=3.29)
    assert entry["status"] == "open"
    assert entry["at"] == 3.29
    assert entry["range"] is None
    assert entry["resolved_at"] is None
    reloaded = load_feedback(tmp_path, "S01_SH01")
    assert reloaded["entries"] == [entry]


def test_add_feedback_entry_accepts_a_range(tmp_path: Path):
    entry = add_feedback_entry(
        tmp_path, "S01_SH01", target="video", note="fear should build",
        range_start=3.3, range_end=4.1,
    )
    assert entry["at"] is None
    assert entry["range"] == {"start": 3.3, "end": 4.1}


def test_add_feedback_entry_accepts_neither_at_nor_range(tmp_path: Path):
    entry = add_feedback_entry(tmp_path, "S01_SH01", target="sync", note="general pacing note")
    assert entry["at"] is None
    assert entry["range"] is None


def test_add_feedback_entry_rejects_unknown_target(tmp_path: Path):
    with pytest.raises(ValueError):
        add_feedback_entry(tmp_path, "S01_SH01", target="bogus", note="x")


def test_add_feedback_entry_rejects_both_at_and_range(tmp_path: Path):
    with pytest.raises(ValueError):
        add_feedback_entry(
            tmp_path, "S01_SH01", target="video", note="x",
            at=1.0, range_start=1.0, range_end=2.0,
        )


def test_add_feedback_entry_rejects_incomplete_range(tmp_path: Path):
    with pytest.raises(ValueError):
        add_feedback_entry(tmp_path, "S01_SH01", target="video", note="x", range_start=1.0)


def test_add_feedback_entry_rejects_negative_timestamp(tmp_path: Path):
    with pytest.raises(ValueError):
        add_feedback_entry(tmp_path, "S01_SH01", target="video", note="x", at=-1.0)


def test_resolve_feedback_entry_updates_status_and_leaves_others_untouched(tmp_path: Path):
    add_feedback_entry(tmp_path, "S01_SH01", target="video", note="a", at=1.0)
    add_feedback_entry(tmp_path, "S01_SH01", target="voice", note="b", at=2.0)

    resolved = resolve_feedback_entry(tmp_path, "S01_SH01", "FB-001", resolution="fixed in v2")

    assert resolved["status"] == "resolved"
    assert resolved["resolution"] == "fixed in v2"
    assert resolved["resolved_at"] is not None
    data = load_feedback(tmp_path, "S01_SH01")
    assert data["entries"][0]["status"] == "resolved"
    assert data["entries"][1]["status"] == "open"


def test_resolve_feedback_entry_rejects_unknown_id(tmp_path: Path):
    add_feedback_entry(tmp_path, "S01_SH01", target="video", note="a", at=1.0)
    with pytest.raises(ValueError):
        resolve_feedback_entry(tmp_path, "S01_SH01", "FB-999")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_feedback_store.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'ai_film.feedback_store'`.

- [ ] **Step 3: Implement**

Create `src/ai_film/feedback_store.py`:

```python
# src/ai_film/feedback_store.py
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

VALID_TARGETS = ("video", "voice", "sfx", "music", "sync")


def feedback_path(project_dir: Path, shot_id: str) -> Path:
    return project_dir / "03_shots" / f"{shot_id}.feedback.json"


def load_feedback(project_dir: Path, shot_id: str) -> dict:
    path = feedback_path(project_dir, shot_id)
    if not path.exists():
        return {"shot_id": shot_id, "entries": []}
    return json.loads(path.read_text())


def save_feedback(project_dir: Path, shot_id: str, data: dict) -> None:
    path = feedback_path(project_dir, shot_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".feedback-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _next_feedback_id(data: dict) -> str:
    return f"FB-{len(data['entries']) + 1:03d}"


def add_feedback_entry(
    project_dir: Path,
    shot_id: str,
    target: str,
    note: str,
    at: float | None = None,
    range_start: float | None = None,
    range_end: float | None = None,
) -> dict:
    if target not in VALID_TARGETS:
        raise ValueError(f"target must be one of {VALID_TARGETS}, got {target!r}")
    if at is not None and (range_start is not None or range_end is not None):
        raise ValueError("give either `at` or a range, not both")
    if (range_start is None) != (range_end is None):
        raise ValueError("range_start and range_end must be given together")
    for value in (at, range_start, range_end):
        if value is not None and value < 0:
            raise ValueError(f"timestamps must be non-negative, got {value}")

    data = load_feedback(project_dir, shot_id)
    entry = {
        "id": _next_feedback_id(data),
        "target": target,
        "at": at,
        "range": {"start": range_start, "end": range_end} if range_start is not None else None,
        "note": note,
        "status": "open",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "resolved_at": None,
        "resolution": None,
    }
    data["entries"].append(entry)
    save_feedback(project_dir, shot_id, data)
    return entry


def resolve_feedback_entry(
    project_dir: Path, shot_id: str, feedback_id: str, resolution: str | None = None,
) -> dict:
    data = load_feedback(project_dir, shot_id)
    for entry in data["entries"]:
        if entry["id"] == feedback_id:
            entry["status"] = "resolved"
            entry["resolved_at"] = datetime.now(timezone.utc).isoformat()
            entry["resolution"] = resolution
            save_feedback(project_dir, shot_id, data)
            return entry
    raise ValueError(f"no feedback entry with id {feedback_id!r} for shot {shot_id!r}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_feedback_store.py -v`

Expected: PASS (all 13 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/feedback_store.py tests/test_feedback_store.py
git commit -m "feat: add per-shot feedback log store"
```

---

### Task 4: `audio_fix.py` — cheap ffmpeg audio-offset fix

**Files:**
- Create: `src/ai_film/audio_fix.py`
- Test: `tests/test_audio_fix.py`

**Interfaces:**
- Consumes: `ArchiveResult`, `archive_stage_artifact`, `project_relative_path` from `ai_film.services.generation_service` (Task 2); `load_shot`, `save_shot` from `ai_film.shot_store`.
- Produces: `AUDIO_STAGES = ("voice", "sfx", "music")`, `apply_audio_offset(project_dir: Path, shot_path: Path, track: str, offset_ms: float) -> dict` — Task 5's `apply-audio-offset` CLI command consumes this.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_audio_fix.py`:

```python
# tests/test_audio_fix.py
import shutil
import subprocess
from pathlib import Path

import pytest

from ai_film.audio_fix import apply_audio_offset
from ai_film.shot_store import save_shot


def _make_tiny_wav(path: Path, duration: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono",
            "-t", str(duration), str(path),
        ],
        check=True, capture_output=True,
    )


def _shot_with_completed_voice(shot_id: str, voice_path: str) -> dict:
    return {
        "schema_version": "1.0", "id": shot_id, "status": "draft", "duration_seconds": 2,
        "continuity": {"status": "passed", "checked_at": None, "issues": []},
        "generation": {
            "image": {"status": "not_required"},
            "video": {"status": "not_required"},
            "voice": {
                "status": "completed", "provider": "fal", "model": "csm-1b", "attempts": 1,
                "artifact": {
                    "path": voice_path, "size_bytes": 10, "sha256": None, "duration_seconds": 1.0,
                },
            },
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }


def test_apply_audio_offset_rejects_unknown_track(tmp_path: Path):
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_completed_voice("S01_SH01", "06_audio/dialogue/S01_SH01.wav"))
    with pytest.raises(ValueError):
        apply_audio_offset(tmp_path, shot_path, track="video", offset_ms=100)


def test_apply_audio_offset_rejects_stage_without_completed_artifact(tmp_path: Path):
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    shot = _shot_with_completed_voice("S01_SH01", "06_audio/dialogue/S01_SH01.wav")
    shot["generation"]["music"] = {"status": "pending", "attempts": 0}
    save_shot(shot_path, shot)
    with pytest.raises(ValueError):
        apply_audio_offset(tmp_path, shot_path, track="music", offset_ms=100)


def test_apply_audio_offset_raises_when_ffmpeg_missing(tmp_path: Path, monkeypatch):
    voice_path = tmp_path / "06_audio" / "dialogue" / "S01_SH01.wav"
    voice_path.parent.mkdir(parents=True)
    voice_path.write_bytes(b"FAKE-WAV")
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_completed_voice("S01_SH01", "06_audio/dialogue/S01_SH01.wav"))

    monkeypatch.setattr("ai_film.audio_fix.shutil.which", lambda name: None)
    with pytest.raises(RuntimeError):
        apply_audio_offset(tmp_path, shot_path, track="voice", offset_ms=100)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_apply_audio_offset_delays_track_and_archives_prior_version(tmp_path: Path):
    voice_path = tmp_path / "06_audio" / "dialogue" / "S01_SH01.wav"
    _make_tiny_wav(voice_path)
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_completed_voice("S01_SH01", "06_audio/dialogue/S01_SH01.wav"))

    stage = apply_audio_offset(tmp_path, shot_path, track="voice", offset_ms=400)

    assert stage["version"] == 2
    assert len(stage["history"]) == 1
    assert stage["history"][0]["superseded_reason"] == "audio_offset"
    archived_path = tmp_path / stage["history"][0]["artifact"]["path"]
    assert archived_path.exists()
    new_path = tmp_path / stage["artifact"]["path"]
    assert new_path.exists()
    assert new_path == voice_path


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_apply_audio_offset_advances_track_with_negative_offset(tmp_path: Path):
    voice_path = tmp_path / "06_audio" / "dialogue" / "S01_SH01.wav"
    _make_tiny_wav(voice_path, duration=2.0)
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_completed_voice("S01_SH01", "06_audio/dialogue/S01_SH01.wav"))

    stage = apply_audio_offset(tmp_path, shot_path, track="voice", offset_ms=-300)

    assert stage["version"] == 2
    new_path = tmp_path / stage["artifact"]["path"]
    assert new_path.exists()
    assert new_path.stat().st_size > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_audio_fix.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'ai_film.audio_fix'`.

- [ ] **Step 3: Implement**

Create `src/ai_film/audio_fix.py`:

```python
# src/ai_film/audio_fix.py
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ai_film.services.generation_service import archive_stage_artifact, project_relative_path
from ai_film.shot_store import load_shot, save_shot

AUDIO_STAGES = ("voice", "sfx", "music")


def apply_audio_offset(project_dir: Path, shot_path: Path, track: str, offset_ms: float) -> dict:
    if track not in AUDIO_STAGES:
        raise ValueError(f"track must be one of {AUDIO_STAGES}, got {track!r}")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not installed or not on PATH")

    shot = load_shot(shot_path)
    shot_id = shot["id"]
    stage_data = shot["generation"].get(track, {"status": "pending"})
    if stage_data.get("status") != "completed" or not stage_data.get("artifact"):
        raise ValueError(f"shot {shot_id} has no completed {track!r} artifact to offset")

    archive = archive_stage_artifact(project_dir, stage_data, "audio_offset")
    if archive.archived_path is None:
        raise ValueError(
            f"artifact file for shot {shot_id}'s {track!r} stage is missing on disk"
        )
    new_path = project_dir / stage_data["artifact"]["path"]  # now vacated by the archive step

    if offset_ms >= 0:
        cmd = [
            "ffmpeg", "-y", "-i", str(archive.archived_path),
            "-af", f"adelay={offset_ms}:all=1", str(new_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-ss", str(abs(offset_ms) / 1000), "-i", str(archive.archived_path),
            str(new_path),
        ]

    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError:
        if archive.restore:
            archive.restore()
        raise

    artifact = {
        "path": project_relative_path(str(new_path), project_dir),
        "size_bytes": new_path.stat().st_size,
        "sha256": None,
        "duration_seconds": stage_data["artifact"].get("duration_seconds"),
    }
    shot["generation"][track] = {
        "provider": stage_data.get("provider"),
        "model": stage_data.get("model"),
        "status": "completed",
        "version": archive.version,
        "history": archive.history,
        "job": stage_data.get("job"),
        "inputs": stage_data.get("inputs", []),
        "artifact": artifact,
        "attempts": stage_data.get("attempts", 1),
    }
    save_shot(shot_path, shot)
    return shot["generation"][track]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_audio_fix.py -v`

Expected: PASS (3 tests always run; the 2 `skipif` tests PASS if `ffmpeg` is installed, SKIP otherwise).

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/audio_fix.py tests/test_audio_fix.py
git commit -m "feat: add cheap ffmpeg audio-offset fix for timing feedback"
```

---

### Task 5: CLI — `add-feedback`, `resolve-feedback`, `apply-audio-offset`

**Files:**
- Modify: `src/ai_film/cli.py` (imports near top; three new commands after `approve_generation_cmd`)
- Test: `tests/test_cli_media_review_commands.py` (new file)

**Interfaces:**
- Consumes: `add_feedback_entry`, `resolve_feedback_entry` from `ai_film.feedback_store` (Task 3); `apply_audio_offset` from `ai_film.audio_fix` (Task 4).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_media_review_commands.py`:

```python
# tests/test_cli_media_review_commands.py
import json
from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.feedback_store import load_feedback
from ai_film.shot_store import save_shot

runner = CliRunner()


def _init_mock_project(tmp_path: Path) -> Path:
    project_dir = tmp_path / "project"
    runner.invoke(app, ["init", "Test Film", "--path", str(project_dir)])
    config = json.loads((project_dir / "config.json").read_text())
    for stage in config["providers"]:
        config["providers"][stage]["provider"] = "mock"
    (project_dir / "config.json").write_text(json.dumps(config))
    return project_dir


def _shot(shot_id: str) -> dict:
    return {
        "schema_version": "1.0", "id": shot_id, "status": "draft", "duration_seconds": 5,
        "continuity": {"status": "passed", "checked_at": None, "issues": []},
        "generation": {
            "image": {"status": "not_required"},
            "video": {"status": "not_required"},
            "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }


def test_add_feedback_writes_entry(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    save_shot(project_dir / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    result = runner.invoke(
        app,
        [
            "add-feedback", "--shot", "S01_SH01", "--target", "video",
            "--at", "3.29", "--note", "too calm", "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "FB-001" in result.output
    data = load_feedback(project_dir, "S01_SH01")
    assert data["entries"][0]["note"] == "too calm"


def test_add_feedback_rejects_unknown_target(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    save_shot(project_dir / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    result = runner.invoke(
        app,
        [
            "add-feedback", "--shot", "S01_SH01", "--target", "bogus",
            "--note", "x", "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 1


def test_resolve_feedback_marks_entry_resolved(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    save_shot(project_dir / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    runner.invoke(
        app,
        [
            "add-feedback", "--shot", "S01_SH01", "--target", "sync",
            "--note", "early", "--path", str(project_dir),
        ],
    )
    result = runner.invoke(
        app,
        ["resolve-feedback", "--shot", "S01_SH01", "--id", "FB-001", "--path", str(project_dir)],
    )
    assert result.exit_code == 0, result.output
    data = load_feedback(project_dir, "S01_SH01")
    assert data["entries"][0]["status"] == "resolved"


def test_resolve_feedback_rejects_unknown_id(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    save_shot(project_dir / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    result = runner.invoke(
        app,
        ["resolve-feedback", "--shot", "S01_SH01", "--id", "FB-999", "--path", str(project_dir)],
    )
    assert result.exit_code == 1


def test_apply_audio_offset_rejects_stage_without_artifact(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    save_shot(project_dir / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    result = runner.invoke(
        app,
        [
            "apply-audio-offset", "--shot", "S01_SH01", "--track", "voice",
            "--offset-ms", "100", "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_media_review_commands.py -v`

Expected: FAIL — `typer` reports "No such command 'add-feedback'" (exit code 2, not 0 or 1) for every test.

- [ ] **Step 3: Implement**

In `src/ai_film/cli.py`, add two imports near the top (alongside the existing `ai_film.services.*` imports):

```python
from ai_film.audio_fix import apply_audio_offset as apply_audio_offset_service
from ai_film.feedback_store import (
    add_feedback_entry as add_feedback_entry_service,
    resolve_feedback_entry as resolve_feedback_entry_service,
)
```

Then add three new commands, placed directly after `approve_generation_cmd` (before `render_cmd`):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli_media_review_commands.py -v`

Expected: PASS (5 tests). Also run `pytest tests/test_cli_generation_commands.py tests/test_cli_candidate_commands.py -v` to confirm no existing CLI test regressed.

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/cli.py tests/test_cli_media_review_commands.py
git commit -m "feat: add add-feedback/resolve-feedback/apply-audio-offset commands"
```

---

### Task 6: `media_review.py` — the static review page builder

**Files:**
- Modify: `src/ai_film/project.py` (`PROJECT_DIRS`)
- Create: `src/ai_film/media_review.py`
- Test: `tests/test_media_review.py` (new file), extend `tests/test_project.py`

**Interfaces:**
- Consumes: `load_shot` from `ai_film.shot_store`; `load_feedback` from `ai_film.feedback_store` (Task 3).
- Produces: `build_media_review(project_dir: Path, shot_id: str) -> Path` — Task 7's `review-media` CLI command consumes this.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_project.py`:

```python
def test_project_dirs_includes_review_directory():
    assert "07_review" in PROJECT_DIRS
```

Create `tests/test_media_review.py`:

```python
# tests/test_media_review.py
from pathlib import Path

import pytest

from ai_film.feedback_store import add_feedback_entry
from ai_film.media_review import build_media_review
from ai_film.shot_store import save_shot


def _shot(shot_id: str, video_completed: bool = False) -> dict:
    video = (
        {
            "status": "completed", "provider": "fal", "model": "veo-3", "attempts": 1,
            "version": 1, "history": [],
            "artifact": {
                "path": f"05_video/{shot_id}.mp4", "size_bytes": 10, "sha256": None,
                "duration_seconds": 6.0,
            },
        }
        if video_completed
        else {"status": "pending", "attempts": 0}
    )
    return {
        "schema_version": "1.0", "id": shot_id, "status": "draft", "duration_seconds": 6,
        "continuity": {"status": "passed", "checked_at": None, "issues": []},
        "generation": {
            "image": {"status": "not_required"}, "video": video,
            "voice": {"status": "not_required"}, "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }


def test_build_media_review_raises_for_missing_shot(tmp_path: Path):
    with pytest.raises(ValueError):
        build_media_review(tmp_path, "S01_SH01")


def test_build_media_review_shows_placeholder_when_nothing_generated(tmp_path: Path):
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    html_path = build_media_review(tmp_path, "S01_SH01")
    content = html_path.read_text()
    assert "video not generated for this shot" in content
    assert "not generated for this shot" in content  # at least one audio row too


def test_build_media_review_embeds_video_source_path(tmp_path: Path):
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01", video_completed=True))
    html_path = build_media_review(tmp_path, "S01_SH01")
    content = html_path.read_text()
    assert "../05_video/S01_SH01.mp4" in content


def test_build_media_review_plots_a_timeline_flag_per_timed_entry(tmp_path: Path):
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01", video_completed=True))
    add_feedback_entry(tmp_path, "S01_SH01", target="video", note="too calm", at=3.29)
    add_feedback_entry(tmp_path, "S01_SH01", target="sync", note="general pacing note")
    html_path = build_media_review(tmp_path, "S01_SH01")
    content = html_path.read_text()
    assert 'class="flag' in content
    assert "3.29s" in content
    assert "general pacing note" in content  # listed even without a plotted flag


def test_build_media_review_writes_to_07_review_directory(tmp_path: Path):
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    html_path = build_media_review(tmp_path, "S01_SH01")
    assert html_path == tmp_path / "07_review" / "S01_SH01.html"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_project.py tests/test_media_review.py -v`

Expected: `test_project_dirs_includes_review_directory` FAILS (`AssertionError`). All of `test_media_review.py` FAILS with `ModuleNotFoundError: No module named 'ai_film.media_review'`.

- [ ] **Step 3: Implement**

In `src/ai_film/project.py`, add `"07_review"` to `PROJECT_DIRS`, between `"06_audio/music"` and `"final"`:

```python
PROJECT_DIRS = (
    "assets/characters", "assets/environments", "assets/props",
    "assets/reference-images", "assets/fonts",
    "00_story", "01_bibles", "02_scenes", "03_shots",
    "04_storyboard", "05_video",
    "06_audio/dialogue", "06_audio/sfx", "06_audio/music",
    "07_review",
    "final", "99_logs",
)
```

Create `src/ai_film/media_review.py`:

```python
# src/ai_film/media_review.py
from __future__ import annotations

import html
import shutil
import subprocess
from pathlib import Path

from ai_film.feedback_store import load_feedback
from ai_film.shot_store import load_shot

_AUDIO_STAGES = (("voice", "Voice"), ("music", "Music"), ("sfx", "SFX"))


def build_media_review(project_dir: Path, shot_id: str) -> Path:
    shot_path = project_dir / "03_shots" / f"{shot_id}.json"
    if not shot_path.exists():
        raise ValueError(f"no shot.json for {shot_id!r} at {shot_path}")
    shot = load_shot(shot_path)
    feedback = load_feedback(project_dir, shot_id)

    review_dir = project_dir / "07_review"
    review_dir.mkdir(parents=True, exist_ok=True)

    waveforms: dict[str, str] = {}
    if shutil.which("ffmpeg"):
        for stage, _label in _AUDIO_STAGES:
            stage_data = shot["generation"].get(stage, {})
            artifact = stage_data.get("artifact")
            if stage_data.get("status") == "completed" and artifact:
                out_png = review_dir / f"{shot_id}_{stage}_waveform.png"
                track_path = project_dir / artifact["path"]
                try:
                    subprocess.run(
                        [
                            "ffmpeg", "-y", "-i", str(track_path), "-filter_complex",
                            "showwavespic=s=640x60:colors=#6fbdb0", "-frames:v", "1",
                            str(out_png),
                        ],
                        check=True, capture_output=True,
                    )
                    waveforms[stage] = out_png.name
                except subprocess.CalledProcessError:
                    pass

    out_path = review_dir / f"{shot_id}.html"
    out_path.write_text(_render_html(shot, feedback, waveforms))
    return out_path


def _rel(project_relative_path: str) -> str:
    # 07_review/<id>.html sits one level below the project root, same as
    # every other top-level stage directory — so a stage artifact path
    # already stored project-relative (e.g. "05_video/S01_SH01.mp4") is
    # reached with a single "../" prefix.
    return f"../{project_relative_path}"


def _format_ts(seconds: float) -> str:
    return f"{seconds:.2f}s"


def _flag_html(entry: dict, duration: float) -> str:
    if entry.get("at") is None and entry.get("range") is None:
        return ""
    if entry.get("range"):
        position = entry["range"]["start"]
        ts_label = f'{entry["range"]["start"]:.1f}-{entry["range"]["end"]:.1f}s'
    else:
        position = entry["at"]
        ts_label = _format_ts(entry["at"])
    left_pct = 0.0 if duration <= 0 else max(0.0, min(100.0, position / duration * 100))
    status_class = "resolved" if entry["status"] == "resolved" else ""
    note = html.escape(entry["note"][:40])
    return (
        f'<div class="flag {status_class}" style="left:{left_pct:.2f}%">'
        f'<span class="tag">{ts_label} · {note}</span>'
        f'<span class="pin"></span><span class="stem"></span></div>'
    )


def _track_row_html(shot: dict, stage: str, label: str, waveforms: dict) -> str:
    stage_data = shot["generation"].get(stage, {})
    if stage_data.get("status") != "completed" or not stage_data.get("artifact"):
        return (
            f'<div class="track"><span class="name">{label}</span>'
            f'<span class="placeholder">not generated for this shot</span></div>'
        )
    artifact = stage_data["artifact"]
    version = stage_data.get("version", 1)
    history = stage_data.get("history", [])
    history_html = ""
    if history:
        rows = "".join(
            f'<div class="history-row">v{h["version"]} '
            f'<audio controls src="{_rel(h["artifact"]["path"])}"></audio></div>'
            for h in history
        )
        history_html = (
            f'<details><summary>{len(history)} earlier version(s)</summary>{rows}</details>'
        )
    waveform_html = ""
    if stage in waveforms:
        waveform_html = f'<img class="waveform" src="{waveforms[stage]}" alt="">'
    return (
        f'<div class="track"><span class="name">{label} · v{version}</span>'
        f'<audio controls src="{_rel(artifact["path"])}"></audio>'
        f'{waveform_html}{history_html}</div>'
    )


def _render_html(shot: dict, feedback: dict, waveforms: dict) -> str:
    shot_id = shot["id"]
    duration = float(shot.get("duration_seconds") or 0)
    video_stage = shot["generation"].get("video", {})
    if video_stage.get("status") == "completed" and video_stage.get("artifact"):
        video_html = (
            f'<video controls id="player" src="{_rel(video_stage["artifact"]["path"])}"></video>'
        )
    else:
        video_html = '<div class="placeholder">video not generated for this shot</div>'

    flags = "".join(_flag_html(e, duration) for e in feedback["entries"])
    open_list = "".join(
        f'<li>[{e["id"]}] {html.escape(e["target"])} — {html.escape(e["note"])}</li>'
        for e in feedback["entries"] if e["status"] == "open"
    )
    resolved_list = "".join(
        f'<li>[{e["id"]}] {html.escape(e["target"])} — {html.escape(e["note"])}</li>'
        for e in feedback["entries"] if e["status"] == "resolved"
    )
    tracks_html = "".join(
        _track_row_html(shot, stage, label, waveforms) for stage, label in _AUDIO_STAGES
    )

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Review: {shot_id}</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 780px; margin: 40px auto; }}
.mono {{ font-variant-numeric: tabular-nums; }}
.placeholder {{ color: #888; font-size: 13px; }}
video {{ width: 100%; }}
.ruler {{ position: relative; height: 40px; border-top: 1px solid #ccc; margin-top: 12px; }}
.flag {{ position: absolute; top: -14px; font-size: 10px; }}
.flag.resolved {{ color: green; }}
.track {{ padding: 8px 0; border-top: 1px solid #eee; }}
</style></head>
<body>
<h1>{shot_id}</h1>
<p>Review only — changes are made by the agent, not by editing here.</p>
{video_html}
<div class="ruler">{flags}</div>
<div class="tracks-header"><p>Media review tracks — playback only</p></div>
{tracks_html}
<h3>How to give feedback</h3>
<p>Review the shot, then describe any issue in chat. Include an approximate
timestamp when relevant: a point (3.3s), a range (3.3-4.1s), or a relative
offset (~400ms).</p>
<h3>Open feedback</h3><ul>{open_list or "<li>none</li>"}</ul>
<h3>Resolved feedback</h3><ul>{resolved_list or "<li>none</li>"}</ul>
</body></html>"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_project.py tests/test_media_review.py -v`

Expected: PASS (all tests). Also run `pytest tests/ -v -k "generation_service or candidate"` to confirm the `PROJECT_DIRS` change didn't affect existing behavior.

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/project.py src/ai_film/media_review.py tests/test_project.py tests/test_media_review.py
git commit -m "feat: add static video/audio review page builder"
```

---

### Task 7: CLI — `review-media`

**Files:**
- Modify: `src/ai_film/cli.py` (import; one new command)
- Test: `tests/test_cli_media_review_commands.py` (extend)

**Interfaces:**
- Consumes: `build_media_review` from `ai_film.media_review` (Task 6); `open_in_browser` from `ai_film.review_gallery` (already imported in `cli.py`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_media_review_commands.py`:

```python
def test_review_media_builds_and_opens_page(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    save_shot(project_dir / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    monkeypatch.setattr("ai_film.cli.open_in_browser", lambda path: None)
    result = runner.invoke(app, ["review-media", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert (project_dir / "07_review" / "S01_SH01.html").exists()


def test_review_media_rejects_unknown_shot(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    result = runner.invoke(app, ["review-media", "--shot", "NOPE", "--path", str(project_dir)])
    assert result.exit_code == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_media_review_commands.py -v -k review_media`

Expected: FAIL — "No such command 'review-media'".

- [ ] **Step 3: Implement**

In `src/ai_film/cli.py`, add one import near the top:

```python
from ai_film.media_review import build_media_review
```

Add one command, directly after the `apply_audio_offset_cmd` added in Task 5:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli_media_review_commands.py -v`

Expected: PASS (all 7 tests in the file).

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/cli.py tests/test_cli_media_review_commands.py
git commit -m "feat: add review-media command"
```

---

### Task 8: Golden-path integration test

**Files:**
- Create: `tests/test_media_review_golden_path.py`

**Interfaces:**
- Consumes: the full CLI surface added by Tasks 1–7, plus the existing `generate-video`/`generate-voice`/`approve-generation` commands. No production code changes in this task.

- [ ] **Step 1: Write the test**

Create `tests/test_media_review_golden_path.py`:

```python
# tests/test_media_review_golden_path.py
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.feedback_store import load_feedback
from ai_film.shot_store import load_shot

runner = CliRunner()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_full_media_review_loop(tmp_path: Path, monkeypatch):
    project_dir = tmp_path / "project"
    result = runner.invoke(app, ["init", "The Last Ship", "--path", str(project_dir)])
    assert result.exit_code == 0
    config_path = project_dir / "config.json"
    config = json.loads(config_path.read_text())
    for stage in config["providers"]:
        config["providers"][stage]["provider"] = "mock"
    config_path.write_text(json.dumps(config))

    shot = {
        "schema_version": "1.0", "id": "S01_SH01", "status": "draft", "duration_seconds": 6,
        "continuity": {"status": "passed", "checked_at": None, "issues": []},
        "action": "Mara answers the phone", "camera": {"shot": "close_up"}, "characters": [],
        "generation": {
            "image": {"status": "not_required"},
            "video": {"status": "pending", "attempts": 0},
            "voice": {"status": "pending", "attempts": 0},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }
    (project_dir / "03_shots" / "S01_SH01.json").write_text(json.dumps(shot))

    result = runner.invoke(
        app,
        ["approve-generation", "--scope", "storyboard", "--targets", "S01_SH01", "--path", str(project_dir)],
    )
    assert result.exit_code == 0

    result = runner.invoke(app, ["generate-video", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["generate-voice", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output

    # first review page: everything generated, nothing flagged yet
    monkeypatch.setattr("ai_film.cli.open_in_browser", lambda path: None)
    result = runner.invoke(app, ["review-media", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    review_html = project_dir / "07_review" / "S01_SH01.html"
    assert review_html.exists()
    assert "video not generated" not in review_html.read_text()

    # human notices a sync problem in the review page; agent records it
    result = runner.invoke(
        app,
        [
            "add-feedback", "--shot", "S01_SH01", "--target", "sync",
            "--note", "voice starts ~400ms early", "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 0
    feedback = load_feedback(project_dir, "S01_SH01")
    assert len(feedback["entries"]) == 1
    fb_id = feedback["entries"][0]["id"]

    # MockAudioProvider writes non-audio placeholder bytes; apply-audio-offset
    # shells out to real ffmpeg, so swap in a real tiny wav first — the same
    # "replace mock-provider output with real content before exercising real
    # tooling" pattern test_candidate_golden_path.py uses for PNGs.
    voice_path = project_dir / "06_audio" / "dialogue" / "S01_SH01.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "1", str(voice_path)],
        check=True, capture_output=True,
    )

    # cheap fix: nudge the voice track, no new provider spend
    result = runner.invoke(
        app,
        [
            "apply-audio-offset", "--shot", "S01_SH01", "--track", "voice",
            "--offset-ms", "400", "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    shot_after = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    assert shot_after["generation"]["voice"]["version"] == 2
    assert len(shot_after["generation"]["voice"]["history"]) == 1

    result = runner.invoke(
        app,
        [
            "resolve-feedback", "--shot", "S01_SH01", "--id", fb_id,
            "--resolution", "applied +400ms offset", "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 0

    # rebuilt review page reflects the new voice version and the (still
    # listed) resolved feedback note
    result = runner.invoke(app, ["review-media", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0
    final_html = review_html.read_text()
    assert "voice starts ~400ms early" in final_html
    assert "v2" in final_html
    assert "1 earlier version" in final_html

    # none of the core engine's existing behavior is affected: render still
    # works exactly as before, untouched by this whole layer
    result = runner.invoke(app, ["render", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert (project_dir / "final" / "reel_001.mp4").exists()
```

- [ ] **Step 2: Run it**

Run: `pytest tests/test_media_review_golden_path.py -v`

Expected: PASS if `ffmpeg` is installed (SKIP otherwise, per the `skipif` guard).

- [ ] **Step 3: Run the full suite**

Run: `pytest tests/ -v`

Expected: every test in the repository PASSES (or SKIPS only for the pre-existing, unrelated ffmpeg-gated tests when ffmpeg is absent) — confirming this whole plan is additive and nothing in the pre-existing 150-plus test suite regressed.

- [ ] **Step 4: Commit**

```bash
git add tests/test_media_review_golden_path.py
git commit -m "test: add media review layer golden-path integration test"
```

---

## Self-Review

**1. Spec coverage.**

| Spec section | Task |
|---|---|
| §3.1 `version`/`history` on generation stages | Task 1 (schema), Task 2 (bookkeeping) |
| §3.2 feedback log | Task 3 |
| §4.1 `run_generation_stage` extension | Task 2 |
| §4.2 `feedback_store.py` | Task 3 |
| §4.3 `audio_fix.py` | Task 4 |
| §5 static review page | Task 6 |
| §6 CLI commands | Tasks 5, 7 |
| §7 cost/approval (no change needed) | Task 2 (approval check untouched — verified by unchanged `test_generate_image_blocked_without_approval` still passing) |
| §8 error handling / edge cases | Covered across Tasks 3, 4, 6's `ValueError`/`RuntimeError` tests |
| §9 testing strategy | Tasks 1–8 collectively |

No spec section lacks a task.

**2. Placeholder scan.** No "TBD"/"TODO"/"add appropriate handling" phrases; every step has real code or a real command.

**3. Type consistency.** `ArchiveResult` (Task 2) is used identically in Task 4 (`archive.version`, `archive.history`, `archive.archived_path`, `archive.restore`) — same attribute names throughout. `add_feedback_entry`'s parameter names (`target`, `note`, `at`, `range_start`, `range_end`) match exactly between Task 3's definition, Task 5's CLI wiring, and Task 8's golden-path usage. `build_media_review(project_dir, shot_id)` signature matches between Task 6's definition and Task 7's CLI usage.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-27-media-review-layer.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
