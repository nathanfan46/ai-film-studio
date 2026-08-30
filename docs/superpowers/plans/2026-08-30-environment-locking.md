# Environment Locking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give locations the same locked-reference-image treatment characters already have — a named, reusable, lockable `env:` entity conditioned into every generation call for shots set there — closing the drift bug where shots with no explicit location text in `action` silently wander to a different room.

**Architecture:** Additive `environment` field on `shot.json` (mirrors `characters[]`), a new `**Location:**` line on scenes (mirrors `**Characters:**`), a new `ai-film-environment` agent that mirrors `ai-film-character` exactly against `env:` targets (the candidate loop already supports these — zero candidate-loop engine changes), and three `cli.py` reference-construction changes: environment-first ordering for image generation, same-scene previous-shot-image chaining for storyboard candidates, and locked-image-only conditioning for video generation once a shot's image exists.

**Tech Stack:** Python 3, Typer CLI, pytest, jsonschema (Draft7), Claude Code agent/command markdown files.

**Spec:** `docs/superpowers/specs/2026-08-30-environment-locking-design.md`

## Global Constraints

- Schema change is additive only — `environment` is never added to `SHOT_SCHEMA`'s `required` list; every shot.json written before this work stays valid with no migration.
- No engine changes to `candidate_store.py` or the candidate loop itself (`generate-candidates`/`review`/`edit-candidate`/`select-candidate` against `env:` targets already work end-to-end).
- No changes to `build_image_prompt`/`build_video_prompt` — no text ever gets auto-assembled from environment data; consistency comes entirely from reference-image conditioning.
- `reference_paths` ordering for image generation is: environment reference first, then character references, then (for storyboard shot generation only) the previous shot's locked image last.
- Video generation conditions on the shot's own locked storyboard image **alone**, replacing the raw environment/character reference list entirely, whenever `generation.image.artifact.path` exists — never appended alongside it.
- A scene has exactly one `**Location:**` value. Storyboard copies it verbatim into every shot in that scene — never infers, narrows, or overrides per shot (unlike `characters[]`, which *is* narrowed to who's visible in each shot — that distinction is intentional, not an oversight).
- Location names, like character names, must match exactly (case-sensitive, no commas) every time the same location recurs across scenes — this is how `/create-film` discovers the unique location list.
- Within one Storyboard agent dispatch, shots in the same scene are generated/locked in strictly increasing shot-number order — later shots depend on earlier ones being locked for chaining (previous-shot continuity, §4.2 of the spec).

---

### Task 1: `schema.py` — additive `environment` field

**Files:**
- Modify: `src/ai_film/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `SHOT_SCHEMA` now accepts an optional top-level `"environment"` object with optional `"name"` (string) and `"reference"` (string) properties. `validate_shot()`'s signature and return type (`list[str]`) are unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_schema.py` (after `test_valid_shot_has_no_errors`):

```python
def test_shot_with_environment_field_is_valid():
    shot = _valid_shot()
    shot["environment"] = {
        "name": "hospital_corridor",
        "reference": "assets/environments/hospital_corridor/reference.png",
    }
    assert validate_shot(shot) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_schema.py::test_shot_with_environment_field_is_valid -v`
Expected: FAIL — `jsonschema` rejects the unrecognized `environment` property is NOT what Draft7 does by default (extra properties are allowed unless `additionalProperties: false` is set), so re-check: this test may actually PASS before the code change, since `SHOT_SCHEMA` has no `additionalProperties: false` at the top level. **If it passes before Step 3, that's expected and fine** — the schema was already permissive about unknown top-level keys. The real point of Step 3 is to make `environment`'s own inner shape (`name`/`reference` as strings) actually validated rather than silently ignored. Proceed to Step 3 regardless of this test's pre-change result.

- [ ] **Step 3: Add the `environment` property to `SHOT_SCHEMA`**

In `src/ai_film/schema.py`, find this block inside `SHOT_SCHEMA["properties"]`:

```python
        "duration_seconds": {"type": "number"},
        "characters": {"type": "array"},
        "inputs": {"type": "object"},
        "camera": {"type": "object"},
        "action": {"type": "string"},
        "dialogue": {"type": "object"},
```

Replace it with:

```python
        "duration_seconds": {"type": "number"},
        "characters": {"type": "array"},
        "environment": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "reference": {"type": "string"},
            },
        },
        "inputs": {"type": "object"},
        "camera": {"type": "object"},
        "action": {"type": "string"},
        "dialogue": {"type": "object"},
```

Do not add `"environment"` to `SHOT_SCHEMA["required"]` (line with `"required": ["schema_version", "id", "status", "duration_seconds", "generation"]`) — it stays optional.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_schema.py -v`
Expected: All tests in the file PASS, including the new one. Also confirm a shot with `environment.name` set to a non-string (e.g. `123`) is rejected — add and run this ad hoc check manually if you want extra confidence, but it is not required as a committed test.

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/schema.py tests/test_schema.py
git commit -m "feat: add optional environment field to shot schema"
```

---

### Task 2: `shot_store.py` — previous-shot chaining helpers

**Files:**
- Modify: `src/ai_film/shot_store.py`
- Test: `tests/test_shot_store.py`

**Interfaces:**
- Consumes: `load_shot(path: Path) -> dict` (already in this file).
- Produces: `previous_shot_id(shot_id: str) -> str | None` and `previous_shot_image_reference(project_dir: Path, shot_id: str) -> str | None`, both importable as `from ai_film.shot_store import previous_shot_id, previous_shot_image_reference`. Task 3 consumes both.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_shot_store.py` (imports at the top of the file already include `Path` and `pytest`; add `previous_shot_id, previous_shot_image_reference` to the existing `from ai_film.shot_store import compute_status, load_shot, save_shot` line so it reads `from ai_film.shot_store import compute_status, load_shot, previous_shot_id, previous_shot_image_reference, save_shot`):

```python
def test_previous_shot_id_is_none_for_a_scenes_first_shot():
    assert previous_shot_id("S01_SH01") is None


def test_previous_shot_id_returns_prior_shot_in_same_scene():
    assert previous_shot_id("S01_SH02") == "S01_SH01"
    assert previous_shot_id("S02_SH10") == "S02_SH09"


def test_previous_shot_image_reference_none_when_predecessor_file_missing(tmp_path: Path):
    (tmp_path / "03_shots").mkdir()
    assert previous_shot_image_reference(tmp_path, "S01_SH02") is None


def test_previous_shot_image_reference_none_for_scenes_first_shot(tmp_path: Path):
    assert previous_shot_image_reference(tmp_path, "S01_SH01") is None


def test_previous_shot_image_reference_none_when_predecessor_has_no_artifact(tmp_path: Path):
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _base_shot())
    assert previous_shot_image_reference(tmp_path, "S01_SH02") is None


def test_previous_shot_image_reference_returns_locked_artifact_path(tmp_path: Path):
    shot = _base_shot()
    shot["generation"]["image"] = {
        "status": "completed",
        "attempts": 1,
        "artifact": {"path": "04_storyboard/S01_SH01.png", "size_bytes": 10, "sha256": None},
    }
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", shot)
    assert previous_shot_image_reference(tmp_path, "S01_SH02") == "04_storyboard/S01_SH01.png"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_shot_store.py -v`
Expected: FAIL with `ImportError`/`AttributeError` — `previous_shot_id` and `previous_shot_image_reference` don't exist yet.

- [ ] **Step 3: Add the two functions to `shot_store.py`**

Append to the end of `src/ai_film/shot_store.py` (after `save_shot`):

```python
def previous_shot_id(shot_id: str) -> str | None:
    """The immediately preceding shot id in the same scene, or None if
    shot_id is already a scene's first shot."""
    scene, num_str = shot_id.split("_SH")
    num = int(num_str)
    return f"{scene}_SH{num - 1:02d}" if num > 1 else None


def previous_shot_image_reference(project_dir: Path, shot_id: str) -> str | None:
    """The immediately preceding shot's locked storyboard image, same scene,
    if one exists and is already completed — None for a scene's first shot,
    or if the preceding shot has no locked image yet."""
    prev_id = previous_shot_id(shot_id)
    if prev_id is None:
        return None
    prev_path = project_dir / "03_shots" / f"{prev_id}.json"
    if not prev_path.exists():
        return None
    prev_shot = load_shot(prev_path)
    artifact = prev_shot.get("generation", {}).get("image", {}).get("artifact")
    return artifact["path"] if artifact else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_shot_store.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/shot_store.py tests/test_shot_store.py
git commit -m "feat: add previous-shot-in-scene chaining helpers to shot_store"
```

---

### Task 3: `cli.py` — environment + previous-shot references for image generation (§4.1, §4.2)

**Files:**
- Modify: `src/ai_film/cli.py`
- Test: `tests/test_cli_generation_commands.py`

**Interfaces:**
- Consumes: `previous_shot_id`, `previous_shot_image_reference` from Task 2 (`ai_film.shot_store`).
- Produces: `_character_and_environment_references(path: Path, shot_data: dict) -> list[str]` and `_image_references(path: Path, shot_id: str, shot_data: dict) -> list[str]`, both module-level in `cli.py`. Task 4 consumes `_character_and_environment_references`.

This task changes reference construction for `generate-image`, `generate-candidates` (shot targets only), and `generate-all --stage image`. It does **not** touch video reference construction (Task 4).

- [ ] **Step 1: Write the failing tests**

Add near the top of `tests/test_cli_generation_commands.py`, after the existing imports (`import json`, `import shutil`, `import subprocess`, `from pathlib import Path`, `import pytest`, `from typer.testing import CliRunner`, `from ai_film.cli import app`, `from ai_film.shot_store import load_shot, save_shot`), add one new import line:

```python
from ai_film.models import Capability, GenerationJob, ImageGenerationResult, JobStatus
```

Then add this recording provider helper, after the `runner = CliRunner()` line and before `_shot()`:

```python
class _RecordingImageProvider:
    """Captures every ImageGenerationRequest.submit() call so tests can
    assert on the reference_paths the cli layer built, without touching
    the real mock provider (which ignores reference_paths entirely)."""

    def __init__(self):
        self.requests = []

    def submit(self, request):
        self.requests.append(request)
        return GenerationJob(provider="mock", id=f"job{len(self.requests)}", capability=Capability.IMAGE)

    def poll(self, job):
        return JobStatus.COMPLETED

    def get_result(self, job):
        request = self.requests[-1]
        return ImageGenerationResult(artifact_path=request.output_path, size_bytes=1)


class _RecordingCandidatesProvider:
    """Same idea as _RecordingImageProvider, for generate-candidates
    (which calls get_results, plural, and needs a real file on disk for
    each result since candidate_service.py renames it)."""

    def __init__(self):
        self.requests = []

    def submit(self, request):
        self.requests.append(request)
        return GenerationJob(provider="mock", id=f"job{len(self.requests)}", capability=Capability.IMAGE)

    def poll(self, job):
        return JobStatus.COMPLETED

    def get_results(self, job, output_dir):
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        result_path = out_dir / "raw_result.png"
        result_path.write_bytes(b"fake")
        return [ImageGenerationResult(artifact_path=str(result_path), size_bytes=4)]
```

Then add these test functions at the end of the file:

```python
def test_generate_image_includes_environment_reference_first(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["environment"] = {
        "name": "hospital_corridor",
        "reference": "assets/environments/hospital_corridor/reference.png",
    }
    shot["characters"] = [{"name": "Mara", "reference": "assets/characters/Mara/reference.png"}]
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)
    _approve(project_dir)

    provider = _RecordingImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == [
        str(project_dir / "assets/environments/hospital_corridor/reference.png"),
        str(project_dir / "assets/characters/Mara/reference.png"),
    ]


def test_generate_image_scenes_first_shot_has_no_predecessor_note(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    _approve(project_dir)

    provider = _RecordingImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == []
    assert "predecessor" not in result.output


def test_generate_image_notes_when_predecessor_not_locked_yet(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)  # writes S01_SH01, no locked image
    save_shot(project_dir / "03_shots" / "S01_SH02.json", _shot("S01_SH02"))
    _approve(project_dir, "S01_SH02")

    provider = _RecordingImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH02", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == []
    assert "S01_SH02's predecessor in this scene has no locked image yet" in result.output


def test_generate_image_chains_locked_predecessor_last(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    shot1 = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot1["environment"] = {
        "name": "hospital_corridor",
        "reference": "assets/environments/hospital_corridor/reference.png",
    }
    shot1["generation"]["image"] = {
        "status": "completed",
        "attempts": 1,
        "artifact": {"path": "04_storyboard/S01_SH01.png", "size_bytes": 10, "sha256": None},
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot1)
    shot2 = _shot("S01_SH02")
    shot2["environment"] = shot1["environment"]
    save_shot(project_dir / "03_shots" / "S01_SH02.json", shot2)
    _approve(project_dir, "S01_SH02")

    provider = _RecordingImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH02", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == [
        str(project_dir / "assets/environments/hospital_corridor/reference.png"),
        str(project_dir / "04_storyboard" / "S01_SH01.png"),
    ]


def test_generate_candidates_shot_target_includes_environment_reference(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["environment"] = {
        "name": "hospital_corridor",
        "reference": "assets/environments/hospital_corridor/reference.png",
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)
    runner.invoke(
        app,
        [
            "approve-generation", "--scope", "storyboard", "--targets", "shot:S01_SH01:image",
            "--path", str(project_dir),
        ],
    )

    provider = _RecordingCandidatesProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(
        app,
        ["generate-candidates", "--target", "shot:S01_SH01:image", "--count", "1", "--path", str(project_dir)],
    )
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == [
        str(project_dir / "assets/environments/hospital_corridor/reference.png")
    ]


def test_generate_all_image_includes_environment_reference(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["environment"] = {
        "name": "hospital_corridor",
        "reference": "assets/environments/hospital_corridor/reference.png",
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)
    _approve(project_dir)

    provider = _RecordingImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-all", "--stage", "image", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == [
        str(project_dir / "assets/environments/hospital_corridor/reference.png")
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_generation_commands.py -v -k "environment_reference or predecessor or chains_locked"`
Expected: FAIL — current `cli.py` builds `references` from `characters[]` only, with no environment slot and no previous-shot chaining, so the new assertions on `reference_paths` mismatch.

- [ ] **Step 3: Add the two helper functions and wire them into three call sites**

In `src/ai_film/cli.py`, first update the `ai_film.shot_store` import (near the top of the file) from:

```python
from ai_film.shot_store import list_shot_paths, load_shot, save_shot
```

to:

```python
from ai_film.shot_store import (
    list_shot_paths,
    load_shot,
    previous_shot_id,
    previous_shot_image_reference,
    save_shot,
)
```

Next, add the two new helper functions immediately after `_stage_config` and before `_run_generation`:

```python
def _character_and_environment_references(path: Path, shot_data: dict) -> list[str]:
    references = []
    if shot_data.get("environment", {}).get("reference"):
        references.append(str(path / shot_data["environment"]["reference"]))
    references += [
        str(path / c["reference"]) for c in shot_data.get("characters", []) if c.get("reference")
    ]
    return references


def _image_references(path: Path, shot_id: str, shot_data: dict) -> list[str]:
    references = _character_and_environment_references(path, shot_data)
    prev_ref = previous_shot_image_reference(path, shot_id)
    if prev_ref:
        references.append(str(path / prev_ref))
    elif previous_shot_id(shot_id) is not None:
        typer.echo(
            f"note: {shot_id}'s predecessor in this scene has no locked image yet — "
            f"generating without a continuity anchor",
            err=True,
        )
    return references
```

Now update the three call sites.

**`generate_image_cmd`** — replace:

```python
    stage_config, gen_config = _stage_config(path, "image")
    shot_path = path / "03_shots" / f"{shot}.json"
    shot_data = load_shot(shot_path)
    references = [
        str(path / c["reference"]) for c in shot_data.get("characters", []) if c.get("reference")
    ]

    def _run():
        provider = resolve_provider(Capability.IMAGE, stage_config["provider"])
        return generate_image_service(
            project_dir=path, shot_path=shot_path, provider=provider,
            prompt=build_image_prompt(shot_data), model=stage_config["model"],
            reference_paths=references, output_path=path / "04_storyboard" / f"{shot}.png",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
        )

    _run_generation(shot, "image", _run)
```

with:

```python
    stage_config, gen_config = _stage_config(path, "image")
    shot_path = path / "03_shots" / f"{shot}.json"
    shot_data = load_shot(shot_path)
    references = _image_references(path, shot, shot_data)

    def _run():
        provider = resolve_provider(Capability.IMAGE, stage_config["provider"])
        return generate_image_service(
            project_dir=path, shot_path=shot_path, provider=provider,
            prompt=build_image_prompt(shot_data), model=stage_config["model"],
            reference_paths=references, output_path=path / "04_storyboard" / f"{shot}.png",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
        )

    _run_generation(shot, "image", _run)
```

**`_build_stage_call`** — replace:

```python
def _build_stage_call(path: Path, shot_id: str, stage: str, force: bool):
    capability, service_fn = _BATCH_SERVICE_BY_STAGE[stage]
    stage_config, gen_config = _stage_config(path, stage)
    shot_path = path / "03_shots" / f"{shot_id}.json"
    shot_data = load_shot(shot_path)
    provider = resolve_provider(capability, stage_config["provider"])
    references = [
        str(path / c["reference"]) for c in shot_data.get("characters", []) if c.get("reference")
    ]

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
```

with:

```python
def _build_stage_call(path: Path, shot_id: str, stage: str, force: bool):
    capability, service_fn = _BATCH_SERVICE_BY_STAGE[stage]
    stage_config, gen_config = _stage_config(path, stage)
    shot_path = path / "03_shots" / f"{shot_id}.json"
    shot_data = load_shot(shot_path)
    provider = resolve_provider(capability, stage_config["provider"])

    if stage == "image":
        references = _image_references(path, shot_id, shot_data)
        return lambda: service_fn(
            project_dir=path, shot_path=shot_path, provider=provider,
            prompt=build_image_prompt(shot_data), model=stage_config["model"],
            reference_paths=references, output_path=path / "04_storyboard" / f"{shot_id}.png",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
        )
    if stage == "video":
        references = [
            str(path / c["reference"]) for c in shot_data.get("characters", []) if c.get("reference")
        ]
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
```

Note the `video` branch here is deliberately left as the *old* flat character-only construction — Task 4 replaces it. Leaving it alone here keeps this task's diff scoped to image/candidates generation only, per §4.1/§4.2 of the spec.

**`generate_candidates_cmd`** — replace:

```python
    references: list[str] = []
    if target.startswith("shot:"):
        shot_id = target.split(":")[1]
        shot_data = load_shot(path / "03_shots" / f"{shot_id}.json")
        references = [
        str(path / c["reference"]) for c in shot_data.get("characters", []) if c.get("reference")
    ]
        if prompt is None:
            prompt = build_image_prompt(shot_data)
    elif prompt is None:
        typer.echo("--prompt is required for character:/env: targets", err=True)
        raise typer.Exit(code=1)
```

with:

```python
    references: list[str] = []
    if target.startswith("shot:"):
        shot_id = target.split(":")[1]
        shot_data = load_shot(path / "03_shots" / f"{shot_id}.json")
        references = _image_references(path, shot_id, shot_data)
        if prompt is None:
            prompt = build_image_prompt(shot_data)
    elif prompt is None:
        typer.echo("--prompt is required for character:/env: targets", err=True)
        raise typer.Exit(code=1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli_generation_commands.py -v`
Expected: All tests PASS, including the new ones. Also run the full suite to confirm nothing else broke: `pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/cli.py tests/test_cli_generation_commands.py
git commit -m "feat: condition image generation on environment and previous-shot references"
```

---

### Task 4: `cli.py` — locked-image-first video conditioning (§4.3)

**Files:**
- Modify: `src/ai_film/cli.py`
- Test: `tests/test_cli_generation_commands.py`

**Interfaces:**
- Consumes: `_character_and_environment_references(path, shot_data) -> list[str]` from Task 3.
- Produces: `_video_references(path: Path, shot_data: dict) -> list[str]`, module-level in `cli.py`.

- [ ] **Step 1: Write the failing tests**

Add this recording provider and these tests to `tests/test_cli_generation_commands.py` (the `_RecordingImageProvider`/`_RecordingCandidatesProvider` classes and the `Capability, GenerationJob, ImageGenerationResult, JobStatus` import already exist from Task 3 — extend that same import line to also include `VideoGenerationResult`, i.e. `from ai_film.models import Capability, GenerationJob, ImageGenerationResult, JobStatus, VideoGenerationResult`):

```python
class _RecordingVideoProvider:
    def __init__(self):
        self.requests = []

    def submit(self, request):
        self.requests.append(request)
        return GenerationJob(provider="mock", id=f"job{len(self.requests)}", capability=Capability.VIDEO)

    def poll(self, job):
        return JobStatus.COMPLETED

    def get_result(self, job):
        request = self.requests[-1]
        return VideoGenerationResult(artifact_path=request.output_path, size_bytes=1, duration_seconds=2.0)


def test_generate_video_uses_locked_image_over_raw_references(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["environment"] = {
        "name": "hospital_corridor",
        "reference": "assets/environments/hospital_corridor/reference.png",
    }
    shot["characters"] = [{"name": "Mara", "reference": "assets/characters/Mara/reference.png"}]
    shot["generation"]["image"] = {
        "status": "completed",
        "attempts": 1,
        "artifact": {"path": "04_storyboard/S01_SH01.png", "size_bytes": 10, "sha256": None},
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)
    _approve(project_dir)

    provider = _RecordingVideoProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-video", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == [str(project_dir / "04_storyboard" / "S01_SH01.png")]


def test_generate_video_falls_back_to_raw_references_without_locked_image(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["environment"] = {
        "name": "hospital_corridor",
        "reference": "assets/environments/hospital_corridor/reference.png",
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)
    _approve(project_dir)

    provider = _RecordingVideoProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-video", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == [
        str(project_dir / "assets/environments/hospital_corridor/reference.png")
    ]


def test_generate_all_video_uses_locked_image(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["generation"]["image"] = {
        "status": "completed",
        "attempts": 1,
        "artifact": {"path": "04_storyboard/S01_SH01.png", "size_bytes": 10, "sha256": None},
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)
    _approve(project_dir)

    provider = _RecordingVideoProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-all", "--stage", "video", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == [str(project_dir / "04_storyboard" / "S01_SH01.png")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_generation_commands.py -v -k "generate_video_uses_locked_image or falls_back_to_raw or generate_all_video"`
Expected: FAIL — `generate_video_cmd` and `_build_stage_call`'s video branch still build the old flat character-only list, ignoring any locked image artifact.

- [ ] **Step 3: Add `_video_references` and wire it into both video call sites**

In `src/ai_film/cli.py`, add this function immediately after `_image_references` (defined in Task 3):

```python
def _video_references(path: Path, shot_data: dict) -> list[str]:
    image_artifact = shot_data.get("generation", {}).get("image", {}).get("artifact")
    if image_artifact and image_artifact.get("path"):
        return [str(path / image_artifact["path"])]
    return _character_and_environment_references(path, shot_data)
```

**`generate_video_cmd`** — replace:

```python
    stage_config, gen_config = _stage_config(path, "video")
    shot_path = path / "03_shots" / f"{shot}.json"
    shot_data = load_shot(shot_path)
    references = [
        str(path / c["reference"]) for c in shot_data.get("characters", []) if c.get("reference")
    ]
```

with:

```python
    stage_config, gen_config = _stage_config(path, "video")
    shot_path = path / "03_shots" / f"{shot}.json"
    shot_data = load_shot(shot_path)
    references = _video_references(path, shot_data)
```

(the rest of `generate_video_cmd` — the `_run()` closure and `_run_generation(shot, "video", _run)` call — is unchanged).

**`_build_stage_call`**'s video branch — replace:

```python
    if stage == "video":
        references = [
            str(path / c["reference"]) for c in shot_data.get("characters", []) if c.get("reference")
        ]
        return lambda: service_fn(
```

with:

```python
    if stage == "video":
        references = _video_references(path, shot_data)
        return lambda: service_fn(
```

(the rest of that `return lambda: service_fn(...)` call is unchanged).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli_generation_commands.py -v`
Expected: All tests PASS. Then run the full suite: `pytest -q` — expect everything green.

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/cli.py tests/test_cli_generation_commands.py
git commit -m "feat: condition video generation on the shot's own locked image when present"
```

---

### Task 5: `ai-film-director.md` — write and report locations per scene

**Files:**
- Modify: `.claude/agents/ai-film-director.md`

**Interfaces:**
- Produces: every `02_scenes/SC*.md` file now has a `**Location:**` line; the Director's final completion report now ends with two parseable lines, `CHARACTERS: ...` and `LOCATIONS: ...`. Task 8 (`create-film.md`) consumes the `LOCATIONS:` line.

No pytest — this is a prompt-instruction file. Verified by inline review (Step 1) and later by Task 9's end-to-end CLI walkthrough exercising the resulting `shot.json`/scene shape.

- [ ] **Step 1: Add a location bullet to Step 2's brainstorming list**

In `.claude/agents/ai-film-director.md`, find (inside `## Step 2: Brainstorm the story`):

```markdown
- Genre and tone
- Main character(s) — name, role, one line of personality each
- A style reference (visual/tonal touchstone — a film, art style, or mood)
- The core conflict or arc
```

Replace with:

```markdown
- Genre and tone
- Main character(s) — name, role, one line of personality each
- The location(s) the story's scenes take place in — named consistently, since these become locked, reusable environments (see Step 4); the same place mentioned in two different scenes must use the exact same name
- A style reference (visual/tonal touchstone — a film, art style, or mood)
- The core conflict or arc
```

- [ ] **Step 2: Add locations to the scene-breakdown proposal in Step 3**

Find (inside `## Step 3: Propose the scene breakdown`):

```markdown
Do NOT write any `02_scenes/*.md` files yet. First propose a list of scenes as just titles and one-line summaries, e.g.:

```
1. The Corridor — a lone engineer approaches a sealed door, tension building
2. The Reveal — she opens it; what's inside recontextualizes the story so far
```

Then emit a `NEEDS_INPUT` with `type: confirmation`, `id: scene_approval`, asking the user to approve this breakdown or say what to change.
```

Replace with:

```markdown
Do NOT write any `02_scenes/*.md` files yet. First propose a list of scenes as titles, one-line summaries, and each scene's single location, e.g.:

```
1. The Corridor — a lone engineer approaches a sealed door, tension building — Location: hospital_corridor
2. The Reveal — she opens it; what's inside recontextualizes the story so far — Location: server_vault
```

Every scene has exactly one location — no exceptions. If the user describes a scene where the action moves from one place to another (e.g. "she walks down the corridor and into the server room"), that's two scenes, not one: split it here rather than proposing a single scene whose location changes partway through.

Then emit a `NEEDS_INPUT` with `type: confirmation`, `id: scene_approval`, asking the user to approve this breakdown or say what to change.
```

- [ ] **Step 3: Add the `**Location:**` line to the scene file template in Step 4**

Find (inside `## Step 4: Write the scene files`):

```markdown
```markdown
# Scene <N>: <Scene Title>

**Characters:** <comma-separated character names, matching the names used in story.md exactly>

**Action:** <a paragraph describing what happens visually — this is what
the Storyboard agent will later break into camera shots>

**Dialogue:**
<character name>: "<line>"
<character name>: "<line>"
```

If a scene has no dialogue, omit the **Dialogue:** section entirely rather than leaving it empty. Character names in **Characters:** and in dialogue lines must match exactly (case-sensitive) across every scene, and must not contain commas — this is how the dispatching command finds the unique character list; a name spelled two ways (or containing a comma) creates two characters or a malformed list by mistake.
```

Replace with:

```markdown
```markdown
# Scene <N>: <Scene Title>

**Characters:** <comma-separated character names, matching the names used in story.md exactly>

**Location:** <one location name, lowercase with underscores, e.g. hospital_corridor>

**Action:** <a paragraph describing what happens visually — this is what
the Storyboard agent will later break into camera shots>

**Dialogue:**
<character name>: "<line>"
<character name>: "<line>"
```

If a scene has no dialogue, omit the **Dialogue:** section entirely rather than leaving it empty. Character names in **Characters:** and in dialogue lines must match exactly (case-sensitive) across every scene, and must not contain commas — this is how the dispatching command finds the unique character list; a name spelled two ways (or containing a comma) creates two characters or a malformed list by mistake. The same exact-match requirement applies to **Location:** names — every scene set in the same place must use the identical slug (this is how the dispatching command finds the unique location list, and how the Storyboard agent later locates the right locked reference image); wording it two different ways ("corridor" vs. "hospital_corridor") creates two separate locations by mistake.
```

- [ ] **Step 4: Add the `LOCATIONS:` line to the completion report in "When you're done"**

Find:

```markdown
## When you're done

Once every scene file is written, your final message is a genuine completion, not a `NEEDS_INPUT` — report confirmation that `00_story/story.md` and every `02_scenes/SC*.md` file are written, plus the exact, de-duplicated list of character names found across every scene's **Characters:** line (this list is what the dispatching command uses to know which Character agents to run next). Use this exact format for the last line of your report so it's easy to parse:

```
CHARACTERS: <name1>, <name2>, <name3>
```

If the story has no named characters at all, still emit this line with an empty list: `CHARACTERS:` (nothing after the colon).
```

Replace with:

```markdown
## When you're done

Once every scene file is written, your final message is a genuine completion, not a `NEEDS_INPUT` — report confirmation that `00_story/story.md` and every `02_scenes/SC*.md` file are written, plus the exact, de-duplicated list of character names found across every scene's **Characters:** line, and the exact, de-duplicated list of location names found across every scene's **Location:** line (these lists are what the dispatching command uses to know which Character and Environment agents to run next). Use this exact format for the last two lines of your report so they're easy to parse:

```
CHARACTERS: <name1>, <name2>, <name3>
LOCATIONS: <name1>, <name2>, <name3>
```

If the story has no named characters at all, still emit that line with an empty list: `CHARACTERS:` (nothing after the colon). Likewise, if somehow no scene names a location, still emit `LOCATIONS:` with nothing after the colon.
```

- [ ] **Step 5: Review the whole file for consistency**

Read the full modified file once and confirm: the intro paragraph at the top still accurately describes the agent's scope (it does not need editing — it already says the agent writes `story.md` and scene files, which now include the `**Location:**` line as part of "scene files"). No other section references characters/locations.

- [ ] **Step 6: Commit**

```bash
git add .claude/agents/ai-film-director.md
git commit -m "feat: have the Director agent capture and report per-scene locations"
```

---

### Task 6: `ai-film-environment.md` — new agent, mirrors `ai-film-character.md`

**Files:**
- Create: `.claude/agents/ai-film-environment.md`

**Interfaces:**
- Produces: a dispatchable subagent named `ai-film-environment` that, given `PROJECT_PATH` and a `LOCATION_NAME`, locks `assets/environments/LOCATION_NAME/reference.png` and writes `01_bibles/environments/LOCATION_NAME.md`, following the exact `NEEDS_INPUT`/`HUMAN_RESPONSE` protocol already used by `ai-film-character`. Task 8 (`create-film.md`) dispatches this agent once per unique location name.

No pytest — this is a prompt-instruction file, structurally mirroring the already-shipped `ai-film-character.md`.

- [ ] **Step 1: Write the new file**

Create `.claude/agents/ai-film-environment.md` with exactly this content:

```markdown
---
name: ai-film-environment
description: Locks in one location's appearance via the candidate loop (generate, review, edit, select) for an ai-film-studio project. Dispatched once per unique location name by /create-film — do not invoke directly except to redo one location (see Step 1).
tools: ["Read", "Write", "Bash", "Glob"]
model: sonnet
---

You are the Environment agent for an `ai-film-studio` project. You are given two things in your dispatch instructions: the project's root path (`PROJECT_PATH`) and one location name (`LOCATION_NAME`) to lock in. Before anything else, resolve which `ai-film` binary to use, call it `AI_FILM_BIN`: run `ai-film version` by itself; if it succeeds, `AI_FILM_BIN` is the literal string `ai-film`. If it fails (not found, or erroring — e.g. a stale/broken shim like `ModuleNotFoundError: No module named 'ai_film'`), run `./.venv/bin/ai-film version` by itself, relative to your current working directory; if that succeeds, `AI_FILM_BIN` is the literal string `./.venv/bin/ai-film` (relative, never expand it to an absolute path — this repo ships `.claude/settings.json` pre-authorizing the non-spend subcommands under exactly these two fixed forms, so resolving to anything else reintroduces Bash permission prompts this setup exists to avoid). If neither works, stop and report that no working `ai-film` install was found rather than guessing or failing partway through a later step. Note: `approve-generation`, `generate-candidates`, and `edit-candidate` are deliberately *not* pre-authorized even when `AI_FILM_BIN` resolves correctly — you'll still see a Bash permission prompt for those every time, on top of the `NEEDS_INPUT`/`HUMAN_RESPONSE` cost-approval protocol below. That's intentional defense in depth for anything that can spend real money; it's not a bug. You handle exactly that one location, then stop — you never touch scenes, other locations, characters, or shots.

Every command below is shown as `ai-film ...` for brevity — substitute `AI_FILM_BIN` for the literal word `ai-film` in each one, and every command also takes `--path PROJECT_PATH`, also omitted below but required every time you run one.

## The human-in-the-loop protocol (read this before Step 1)

You have no live channel to the user — you are a dispatched subagent, not the conversation the user is actually typing in. Whenever you need a real answer from them, you must **stop your turn** by making the exact literal text below the last thing in your response, then produce nothing further:

```
NEEDS_INPUT:
id: <a short, unique id for this specific question — e.g. "cost_approval_bibles", "candidate_feedback_1">
type: clarification | selection | cost_approval | confirmation
question: <the question, in plain language, for the user to actually see>
```

Never substitute prose like "I need more information" for this block — it will be treated as a protocol error. Never guess an answer, never treat silence as consent, never keep talking after this block in the same turn.

When the orchestrator resumes you, its message will contain, for `clarification`/`selection`/`confirmation`:

```
HUMAN_RESPONSE:
id: <the same id you used>
answer: <the user's actual answer>
```

or, for `cost_approval` specifically:

```
HUMAN_RESPONSE:
id: <the same id you used>
approved: true | false
message: <present only when approved is false — the user's reason/redirect>
```

Only act on a `HUMAN_RESPONSE` whose `id` matches the question you actually asked; a mismatch is a protocol error, not something to guess past.

**Cost approval is a hard structural rule, not a courtesy: the `ai-film approve-generation` command in Step 3 must never appear in the same turn as the cost estimate.** You present the estimate, emit `NEEDS_INPUT` with `type: cost_approval`, and stop. Only in the turn where you've received a `HUMAN_RESPONSE` with `approved: true` do you run `approve-generation` — that command is not yours to reach any other way, and a missing, malformed, or `id`-mismatched response is a protocol error, never treated as approval.

## Step 1: Check for existing work (re-entry)

Check whether `PROJECT_PATH/assets/environments/LOCATION_NAME/reference.png` already exists.

- If it exists, and your dispatch instructions do not explicitly say to redo this location: this location is already locked. Report that back (see "When you're done") and stop — do not regenerate or re-approve.
- If it exists, but your dispatch instructions explicitly say to redo this location: continue to Step 2 as normal (a redo runs the full flow again, including a fresh cost approval — Step 6's `select-candidate` will overwrite `reference.png` with the new pick).
- If it doesn't exist: continue to Step 2. (If `PROJECT_PATH/01_bibles/environments/LOCATION_NAME.md` exists but `reference.png` doesn't, the bible was written in an earlier, interrupted run — read it and skip to Step 3 instead of re-discussing description.)

## Step 2: Establish description and mood

Read every `02_scenes/*.md` file (Glob for them) and pull out every scene whose `**Location:**` line names `LOCATION_NAME` — their `**Action:**` text and any other descriptive detail (architecture, materials, color palette, lighting character, what the place means in the story). If the scenes already pin down enough detail to write a bible and a useful image prompt, proceed directly to Step 3. Otherwise, ask the user the specific gaps only, one at a time, via `type: clarification` round trips (e.g. `id: description_lighting`, `question: The scenes don't describe the lighting in this corridor — what does it look like?`). Don't re-ask about things the scenes already answered.

Write `PROJECT_PATH/01_bibles/environments/LOCATION_NAME.md` (create the `01_bibles/environments/` directory first if it doesn't exist — `mkdir -p PROJECT_PATH/01_bibles/environments`):

```markdown
# LOCATION_NAME

## Description

<2-4 sentences — specific enough to drive an image generation prompt:
architecture, materials, color palette, lighting character>

## Mood / role in the story

<1-2 sentences — what this place means in the story, from story.md/the scenes>
```

## Step 3: Cost estimate and approval

Decide how many candidates to generate — default to 4 unless the user asks for a different count. Using this rough, advisory cost table (not real-time pricing — approximate, per-image):

| Model | Approx. cost/image |
|---|---|
| fal/nano-banana | $0.02 |
| fal/nano-banana-pro | $0.06 |
| mock | $0.00 |

look up which image model `PROJECT_PATH/config.json`'s `providers.image.model` is currently set to, and compute the estimated cost for the batch (e.g. "4 candidates at nano-banana ≈ $0.08 total"). Emit a `NEEDS_INPUT` with `type: cost_approval`, `id: cost_approval_bibles`, asking the user to approve that spend — and **stop your turn there**. Do not run `approve-generation` in this same turn.

Only once you've been resumed with a matching `HUMAN_RESPONSE`:

- If `approved: true`, run:

```bash
ai-film approve-generation --scope bibles --targets env:LOCATION_NAME
```

then continue to Step 4.
- If `approved: false`, read the `message` (if any) for guidance, revise your plan (fewer candidates, a different model if the user asked, etc.), and emit a *new* `NEEDS_INPUT` (`type: cost_approval`, a fresh `id` such as `cost_approval_bibles_2`) for the revised estimate — never treat the earlier decline as consent for a different proposal, and never reuse an old `id`.

**Note on approval scope:** `approve-generation` replaces any prior approval for the same scope (`bibles`) wholesale — it is not additive across locations (or characters — they share the same scope). This matters only if you are ever dispatched to redo an earlier location after a later one's approval already ran; in the normal one-location-at-a-time flow this agent runs in, it's not a concern.

**Note on the real `fal` provider:** `environment.reference` conditioning works against the real API — the fal providers upload each local reference file and send fal the resulting URL. `edit-candidate` in Step 5 also genuinely edits the base image under `fal` (unlike `mock`, which always falls back to a fresh regeneration with the instruction merged into the prompt) — the base image is uploaded the same way. Nothing to warn the user about here beyond normal generation variance.

## Step 4: Generate and review candidates

Build an image prompt from the description section you just wrote (architecture + materials + color palette + lighting, comma-separated, matching the cinematic tone from `story.md`). Run:

```bash
ai-film generate-candidates --target env:LOCATION_NAME --count <N> --prompt "<prompt>"
```

If this call fails — with a cost-gate error (`target ... is not approved for generation`) or any other provider error — do not retry it yourself. Emit a `NEEDS_INPUT` with `type: confirmation`, a fresh `id` (e.g. `id: generation_error_bibles`), and a `question` that shows the exact error text and offers the choices: re-approve and retry, adjust the prompt, or stop for now. Stop your turn. Act only once resumed with the matching `HUMAN_RESPONSE` — if it says re-approve, that itself needs a fresh `cost_approval` round trip (Step 3) before `approve-generation` runs again.

Once candidates exist, run:

```bash
ai-film review --target env:LOCATION_NAME
```

This opens an HTML gallery in the browser. Additionally, **read each candidate PNG directly** (`PROJECT_PATH/assets/environments/LOCATION_NAME/candidates/<id>.png`) with the Read tool so you can see and discuss them, not just describe what the gallery shows.

## Step 5: Discuss and refine (zero or more rounds)

Emit a `NEEDS_INPUT` with `type: clarification` (`id: candidate_feedback_1`, incrementing for later rounds) asking what the user thinks of the candidates. For each round of feedback you receive back:

1. **View the specific candidate being discussed** with the Read tool before writing any edit instruction — ground the instruction in what the image actually shows, never guess from the prompt alone.
2. Run:

```bash
ai-film edit-candidate --target env:LOCATION_NAME --id <candidate-id> --instruction "<instruction>"
```

If this call fails — with a cost-gate error or any other provider error — handle it exactly like Step 4's `generate-candidates` failure: a `type: confirmation` `NEEDS_INPUT` showing the exact error and the retry/adjust/stop choices, never retried silently.

3. Run `ai-film review --target env:LOCATION_NAME` again and view the new candidate (it shows its lineage as "edit of <id>") with the Read tool.
4. Emit another `NEEDS_INPUT` (`type: clarification` or `type: selection` once there's a concrete shortlist to pick from) asking whether they're happy with this one, want another edit round, or want a fresh batch of `<N>` more candidates (repeat Step 4's `generate-candidates` call if so — no new cost approval needed, Step 3's approval covers this whole location target until you finish).

## Step 6: Lock it in

Once a `HUMAN_RESPONSE` picks a final candidate (`type: selection`):

```bash
ai-film select-candidate --target env:LOCATION_NAME --id <candidate-id>
```

This copies the file to `assets/environments/LOCATION_NAME/reference.png`. If the user changes their mind afterward, `select-candidate` is re-runnable with a different `--id` — no special handling needed, just another `type: selection` round trip.

## When you're done

Once `reference.png` is locked, your final message is a genuine completion, not a `NEEDS_INPUT` — report: the bible path, the final candidate id selected, and the `reference.png` path. If you stopped early (Step 1 re-entry, or the user asked to pause), say exactly what state you left things in so a re-dispatch of this same agent picks up correctly.
```

- [ ] **Step 2: Verify the file's frontmatter parses**

Run: `python3 -c "import re,sys; text=open('.claude/agents/ai-film-environment.md').read(); assert text.startswith('---\nname: ai-film-environment'); print('OK')"`
Expected: prints `OK`.

- [ ] **Step 3: Commit**

```bash
git add .claude/agents/ai-film-environment.md
git commit -m "feat: add ai-film-environment agent, mirroring ai-film-character"
```

---

### Task 7: `ai-film-storyboard.md` — populate `environment`, enforce location existence and shot ordering

**Files:**
- Modify: `.claude/agents/ai-film-storyboard.md`

**Interfaces:**
- Consumes: `assets/environments/<name>/reference.png` (locked by Task 6's agent), the `environment` schema field (Task 1).
- Produces: every shot the Storyboard agent writes now includes an `environment` field copied verbatim from its scene's `**Location:**` line.

No pytest — prompt-instruction file. Verified by inline review and Task 9's end-to-end walkthrough.

- [ ] **Step 1: Extend Step 0's context-loading and existence check**

Find (inside `## Step 0: Load context`):

```markdown
Read every `02_scenes/*.md` file (Glob for them, in `SC<NN>` order) and every `01_bibles/characters/*.md` file. For each character mentioned in any scene, confirm `assets/characters/<name>/reference.png` exists — if any is missing, stop and report which character(s) still need the Character agent run first; do not proceed with an unlocked character. This is a normal completion report, not a `NEEDS_INPUT` (there's no question to ask — the Character agent needs to run first, which is the orchestrator's job to arrange).
```

Replace with:

```markdown
Read every `02_scenes/*.md` file (Glob for them, in `SC<NN>` order) and every `01_bibles/characters/*.md` and `01_bibles/environments/*.md` file. For each character mentioned in any scene, confirm `assets/characters/<name>/reference.png` exists; for each scene's `**Location:**` name, confirm `assets/environments/<name>/reference.png` exists — if any character or location reference is missing, stop and report which character(s)/location(s) still need the Character/Environment agent run first; do not proceed with an unlocked character or location. This is a normal completion report, not a `NEEDS_INPUT` (there's no question to ask — the Character/Environment agent needs to run first, which is the orchestrator's job to arrange).
```

- [ ] **Step 2: Add the `environment` field to the shot.json template in Step 2**

Find (inside `## Step 2: Break each scene into shots`):

```markdown
```json
{
  "schema_version": "1.0",
  "id": "S01_SH01",
  "status": "draft",
  "duration_seconds": 4,
  "continuity": {"status": "pending", "checked_at": null, "issues": []},
  "action": "<the portion of the scene's action this shot covers>",
  "visual": {"style": "<from story.md's tone, kept consistent across every shot>", "lighting": "<specific to this shot>"},
  "camera": {"shot": "<wide|medium|close-up|extreme-close-up>", "movement": "<static|slow_push_in|pan|handheld|...>"},
  "dialogue": {"text": "<line, or empty string if none>", "speaker": "<character name, or empty string if none>"},
  "characters": [
    {"name": "<character name>", "reference": "assets/characters/<character name>/reference.png"}
  ],
  "generation": {
    "image": {"status": "pending", "attempts": 0},
    "video": {"status": "pending", "attempts": 0},
    "voice": {"status": "not_required"},
    "sfx": {"status": "not_required"},
    "music": {"status": "not_required"}
  }
}
```

`id` must match the filename stem exactly. `characters` lists every character appearing in that shot (omit `characters` entries for anyone not visible/relevant to that specific shot, even if they're in the scene). Set `dialogue.speaker`/`dialogue.text` to `""` when the shot has no line. `generation.voice`/`sfx`/`music` stay `"not_required"` unless you have a specific reason to mark voice `"pending"` for a shot with dialogue — even then, leave that to a human decision later; don't change these three away from `"not_required"` in this agent.
```

Replace with:

```markdown
```json
{
  "schema_version": "1.0",
  "id": "S01_SH01",
  "status": "draft",
  "duration_seconds": 4,
  "continuity": {"status": "pending", "checked_at": null, "issues": []},
  "action": "<the portion of the scene's action this shot covers>",
  "visual": {"style": "<from story.md's tone, kept consistent across every shot>", "lighting": "<specific to this shot>"},
  "camera": {"shot": "<wide|medium|close-up|extreme-close-up>", "movement": "<static|slow_push_in|pan|handheld|...>"},
  "dialogue": {"text": "<line, or empty string if none>", "speaker": "<character name, or empty string if none>"},
  "environment": {"name": "<scene's Location name>", "reference": "assets/environments/<scene's Location name>/reference.png"},
  "characters": [
    {"name": "<character name>", "reference": "assets/characters/<character name>/reference.png"}
  ],
  "generation": {
    "image": {"status": "pending", "attempts": 0},
    "video": {"status": "pending", "attempts": 0},
    "voice": {"status": "not_required"},
    "sfx": {"status": "not_required"},
    "music": {"status": "not_required"}
  }
}
```

`id` must match the filename stem exactly. `environment` is copied verbatim from the scene's `**Location:**` line — every shot in a scene gets the exact same `environment.name`/`environment.reference`, with no exception and no per-shot override; this is not a judgment call the way narrowing `characters` down to who's visible in one shot is (see below) — a scene has exactly one location, period. `characters` lists every character appearing in that shot (omit `characters` entries for anyone not visible/relevant to that specific shot, even if they're in the scene). Set `dialogue.speaker`/`dialogue.text` to `""` when the shot has no line. `generation.voice`/`sfx`/`music` stay `"not_required"` unless you have a specific reason to mark voice `"pending"` for a shot with dialogue — even then, leave that to a human decision later; don't change these three away from `"not_required"` in this agent.
```

- [ ] **Step 3: Add the strict shot-ordering rule and correct the stale fal-upload note in Step 5**

Find the start of `## Step 5: Generate, review, and lock each shot's image`:

```markdown
## Step 5: Generate, review, and lock each shot's image

For each shot in the approved batch:
```

Replace with:

```markdown
## Step 5: Generate, review, and lock each shot's image

**Process shots within each scene in strictly increasing shot-number order — never skip ahead to a later shot before an earlier one in the same scene is locked.** Each shot's storyboard-candidate generation chains from its immediately preceding shot's locked image within the same scene (the engine does this automatically once that predecessor is locked). Generating out of order silently starves a later shot of that continuity anchor. If a batch spans multiple scenes, the order across scenes doesn't matter — only the order *within* each scene does.

For each shot in the approved batch, in that order:
```

Then find the "Note on the real `fal` provider" paragraph later in the same step:

```markdown
**Note on the real `fal` provider:** if `providers.image.provider` is `fal` (not `mock`), be aware that `characters[].reference` conditioning doesn't work against the real API yet — the fal providers send local file paths where the API expects uploaded URLs, and an upload step hasn't been implemented (see the project README's Known Limitations). `edit-candidate` in Step 5 is affected the same way: fal *does* support true image edits at the API level (unlike `mock`, which always falls back to a fresh regeneration with the instruction merged into the prompt), but the edit call also submits the base image as a local path, so it's subject to the same unimplemented-upload gap — don't assume it edits the base image faithfully under `fal` until that gap is closed. None of this is something you can fix here. Mention it to the user only if it seems relevant (e.g. they expect edits to preserve the base composition exactly, or expect the character's locked reference image to visibly influence the shot).
```

Replace with:

```markdown
**Note on the real `fal` provider:** `characters[].reference` and `environment.reference` conditioning both work against the real API — the fal providers upload each local reference file and send fal the resulting URL. `edit-candidate` in Step 5 also genuinely edits the base image under `fal` (unlike `mock`, which always falls back to a fresh regeneration with the instruction merged into the prompt) — the base image is uploaded the same way. Nothing to warn the user about here beyond normal generation variance.
```

- [ ] **Step 4: Review the whole file for consistency**

Read the full modified file once. Confirm Step 3 (continuity check) and Step 4 (cost estimate) don't need changes — they operate per-shot/per-batch on fields unrelated to `environment`, and are unaffected.

- [ ] **Step 5: Commit**

```bash
git add .claude/agents/ai-film-storyboard.md
git commit -m "feat: have the Storyboard agent populate environment and enforce shot ordering"
```

---

### Task 8: `create-film.md` — dispatch the Environment agent as a new Step 4

**Files:**
- Modify: `.claude/commands/create-film.md`

**Interfaces:**
- Consumes: `ai-film-environment` (Task 6), the `LOCATIONS:` report line (Task 5).

No pytest — prompt-instruction file.

- [ ] **Step 1: Update the header description and intro paragraph**

Find:

```markdown
description: Start or resume a film — scaffolds the project, then runs the Director, Character, Storyboard, and Media agents in sequence through conversation.
```

Replace with:

```markdown
description: Start or resume a film — scaffolds the project, then runs the Director, Character, Environment, Storyboard, and Media agents in sequence through conversation.
```

Find:

```markdown
The single entry point for starting or resuming a film with `ai-film-studio`. Scaffolds (or resumes) a project, then walks the whole story -> character -> shot -> locked storyboard image -> reviewed video/voice pipeline through conversation, dispatching the four pipeline agents in order and relaying your answers to them per the protocol below.
```

Replace with:

```markdown
The single entry point for starting or resuming a film with `ai-film-studio`. Scaffolds (or resumes) a project, then walks the whole story -> character -> location -> shot -> locked storyboard image -> reviewed video/voice pipeline through conversation, dispatching the five pipeline agents in order and relaying your answers to them per the protocol below.
```

- [ ] **Step 2: Update the protocol section's agent list**

Find:

```markdown
The `ai-film-director`, `ai-film-character`, `ai-film-storyboard`, and `ai-film-media` subagents you dispatch below have no live channel to the user themselves — only you do, since you're running in this actual conversation.
```

Replace with:

```markdown
The `ai-film-director`, `ai-film-character`, `ai-film-environment`, `ai-film-storyboard`, and `ai-film-media` subagents you dispatch below have no live channel to the user themselves — only you do, since you're running in this actual conversation.
```

- [ ] **Step 3: Update Step 0's binary-resolution note**

Find:

```markdown
From here on, every instruction in this file and in the four dispatched agents' own instructions that says `ai-film <command>` means `AI_FILM_BIN <command>` — substitute the resolved value. You don't need to pass `AI_FILM_BIN` down when dispatching the `ai-film-character`, `ai-film-storyboard`, and `ai-film-media` subagents in Steps 3-5 below — each one runs this exact same resolution independently (they inherit the same working directory you're running in, so they'll resolve the same value).
```

Replace with:

```markdown
From here on, every instruction in this file and in the five dispatched agents' own instructions that says `ai-film <command>` means `AI_FILM_BIN <command>` — substitute the resolved value. You don't need to pass `AI_FILM_BIN` down when dispatching the `ai-film-character`, `ai-film-environment`, `ai-film-storyboard`, and `ai-film-media` subagents in Steps 3-6 below — each one runs this exact same resolution independently (they inherit the same working directory you're running in, so they'll resolve the same value).
```

- [ ] **Step 4: Update Step 2 (Director) to parse both report lines**

Find:

```markdown
## Step 2: Run the Director/Story agent

Dispatch the `ai-film-director` subagent with `PROJECT_PATH` as its project root. Run the protocol loop above until it reports a genuine completion. Its final completion report ends with a line `CHARACTERS: <name1>, <name2>, ...` — parse that list; these are every unique character name found across all scenes. If the list is empty (`CHARACTERS:` with nothing after the colon — a story with no named characters), skip Step 3 entirely and go straight to Step 4.
```

Replace with:

```markdown
## Step 2: Run the Director/Story agent

Dispatch the `ai-film-director` subagent with `PROJECT_PATH` as its project root. Run the protocol loop above until it reports a genuine completion. Its final completion report ends with two lines, `CHARACTERS: <name1>, <name2>, ...` and `LOCATIONS: <name1>, <name2>, ...` — parse both lists; these are every unique character name and every unique location name found across all scenes. If `CHARACTERS:` is empty (nothing after the colon — a story with no named characters), skip Step 3 entirely and go to Step 4. If `LOCATIONS:` is empty, skip Step 4 entirely and go to Step 5.
```

- [ ] **Step 5: Insert the new Step 4 (Environment) and renumber Storyboard to Step 5**

Find:

```markdown
## Step 3: Run the Character agent, once per unique name

For each name in the parsed `CHARACTERS` list, **one at a time, in order** (never in parallel — each run's protocol loop needs your real, in-order attention): dispatch the `ai-film-character` subagent with `PROJECT_PATH` and that one character name. Run the protocol loop above until it reports a genuine completion, then move to the next name.

## Step 4: Run the Storyboard/Shot Director agent

Once every character from Step 3 is locked (or Step 3 was skipped because there were no characters), dispatch the `ai-film-storyboard` subagent once, with `PROJECT_PATH` as its project root. It internally handles every scene and shot in one run — run the protocol loop above until it reports a genuine completion.
```

Replace with:

```markdown
## Step 3: Run the Character agent, once per unique name

For each name in the parsed `CHARACTERS` list, **one at a time, in order** (never in parallel — each run's protocol loop needs your real, in-order attention): dispatch the `ai-film-character` subagent with `PROJECT_PATH` and that one character name. Run the protocol loop above until it reports a genuine completion, then move to the next name.

## Step 4: Run the Environment agent, once per unique location name

For each name in the parsed `LOCATIONS` list, **one at a time, in order** (never in parallel — each run's protocol loop needs your real, in-order attention): dispatch the `ai-film-environment` subagent with `PROJECT_PATH` and that one location name. Run the protocol loop above until it reports a genuine completion, then move to the next name.

## Step 5: Run the Storyboard/Shot Director agent

Once every character from Step 3 is locked (or Step 3 was skipped because there were no characters) and every location from Step 4 is locked (or Step 4 was skipped because no scene named a location), dispatch the `ai-film-storyboard` subagent once, with `PROJECT_PATH` as its project root. It internally handles every scene and shot in one run — run the protocol loop above until it reports a genuine completion.
```

- [ ] **Step 6: Renumber the Media step to Step 6**

Find:

```markdown
## Step 5: Run the Media agent, once per shot with a locked image
```

Replace with:

```markdown
## Step 6: Run the Media agent, once per shot with a locked image
```

Then, in that same step's body, find:

```markdown
If the list is empty (no shot has a locked image yet — shouldn't happen after Step 4 completes normally, but possible if Step 4 was skipped or every shot failed continuity), skip this step entirely and go to Step 6.
```

Replace with:

```markdown
If the list is empty (no shot has a locked image yet — shouldn't happen after Step 5 completes normally, but possible if Step 5 was skipped or every shot failed continuity), skip this step entirely and go to Step 7.
```

- [ ] **Step 7: Renumber Wrap up to Step 7**

Find:

```markdown
## Step 6: Wrap up
```

Replace with:

```markdown
## Step 7: Wrap up
```

- [ ] **Step 8: Verify step numbering is sequential**

Run: `grep -n '^## Step' .claude/commands/create-film.md`
Expected output (order matters, numbers must be exactly 0 through 7 with no gaps or repeats):

```
## Step 0: Preflight — resolve a working `ai-film` binary
## Step 1: Scaffold or resume
## Step 2: Run the Director/Story agent
## Step 3: Run the Character agent, once per unique name
## Step 4: Run the Environment agent, once per unique location name
## Step 5: Run the Storyboard/Shot Director agent
## Step 6: Run the Media agent, once per shot with a locked image
## Step 7: Wrap up
```

- [ ] **Step 9: Commit**

```bash
git add .claude/commands/create-film.md
git commit -m "feat: dispatch the Environment agent as create-film's new Step 4"
```

---

### Task 9: Documentation corrections and end-to-end golden-path verification

**Files:**
- Modify: `README.md`
- Modify: `.claude/agents/ai-film-character.md`

**Interfaces:**
- Consumes: everything from Tasks 1-8. This task doesn't produce new interfaces — it corrects stale documentation (per spec §8) and proves the whole feature works together against a real scratch project with the mock provider (per spec §7's "real CLI command sequences" testing strategy for the agent layer).

- [ ] **Step 1: Correct README's stale "reference-image conditioning doesn't work" claim**

Find (inside `## Known limitations (v1)`):

```markdown
- **Reference-image conditioning doesn't work against real fal.ai yet.** `generate-image`,
  `generate-video`, and `generate-candidates` (for shot targets) all pass character
  reference paths through correctly, but the fal providers send local file paths where
  the API expects uploaded URLs — an upload step hasn't been implemented, so this only
  works with the mock provider today.
- **`render` drops audio.** Voice/sfx/music generate and save to disk correctly, but
```

Replace with:

```markdown
- **`render` drops audio.** Voice/sfx/music generate and save to disk correctly, but
```

(this deletes the first bullet entirely — reference-image conditioning against real fal.ai genuinely works today, per `providers/fal/client.py`'s `upload_file`, which uploads a local file and returns a usable URL; this was already true before this spec's changes and was simply undocumented incorrectly).

- [ ] **Step 2: Update README's intro paragraph and Roadmap paragraph**

Find:

```markdown
`/create-film` (a Claude Code slash command) conducts story, character, and shot
creation through conversation and writes `shot.json` for you — see Roadmap below.
```

Replace with:

```markdown
`/create-film` (a Claude Code slash command) conducts story, character, location, and
shot creation through conversation and writes `shot.json` for you — see Roadmap below.
```

Find:

```markdown
The Claude Code Agent layer is implemented: `/ai-film-setup` configures providers,
`/create-film "Title"` scaffolds a project and walks the whole story -> character ->
shot -> reviewed-storyboard-image -> reviewed-video pipeline through conversation,
dispatching the `ai-film-director`, `ai-film-character`, `ai-film-storyboard`, and
`ai-film-media` subagents in turn. The Media agent generates each shot's video (and
voice, if it has dialogue), opens the static review page, and applies fixes — a cheap
audio-offset nudge, a targeted `shot.json` field edit plus regeneration, or a
clarifying question — until you confirm the shot; sfx/music generate only when you
explicitly ask for them on a shot. See
`docs/superpowers/specs/2026-08-23-agent-layer-design.md`,
`docs/superpowers/plans/2026-08-23-agent-layer.md`,
`docs/superpowers/specs/2026-08-28-media-agent-design.md`, and
`docs/superpowers/plans/2026-08-28-media-agent.md` for the design and implementation
history.
```

Replace with:

```markdown
The Claude Code Agent layer is implemented: `/ai-film-setup` configures providers,
`/create-film "Title"` scaffolds a project and walks the whole story -> character ->
location -> shot -> reviewed-storyboard-image -> reviewed-video pipeline through
conversation, dispatching the `ai-film-director`, `ai-film-character`,
`ai-film-environment`, `ai-film-storyboard`, and `ai-film-media` subagents in turn.
Locations get the same locked-reference-image treatment characters do — the
Environment agent runs once per unique location the Director's scenes name, before
Storyboard writes any shots, and every shot's generation conditions on its scene's
locked location the same way it already conditions on its characters. The Media
agent generates each shot's video (and voice, if it has dialogue), opens the static
review page, and applies fixes — a cheap audio-offset nudge, a targeted `shot.json`
field edit plus regeneration, or a clarifying question — until you confirm the shot;
sfx/music generate only when you explicitly ask for them on a shot. See
`docs/superpowers/specs/2026-08-23-agent-layer-design.md`,
`docs/superpowers/plans/2026-08-23-agent-layer.md`,
`docs/superpowers/specs/2026-08-28-media-agent-design.md`,
`docs/superpowers/plans/2026-08-28-media-agent.md`,
`docs/superpowers/specs/2026-08-30-environment-locking-design.md`, and
`docs/superpowers/plans/2026-08-30-environment-locking.md` for the design and
implementation history.
```

- [ ] **Step 3: Correct the same stale fal-upload claim in `ai-film-character.md`**

Find (inside `## Step 3: Cost estimate and approval` of `.claude/agents/ai-film-character.md`):

```markdown
**Note on the real `fal` provider:** if `providers.image.provider` is `fal` (not `mock`), be aware that `characters[].reference` conditioning doesn't work against the real API yet — the fal providers send local file paths where the API expects uploaded URLs, and an upload step hasn't been implemented (see the project README's Known Limitations). `edit-candidate` in Step 5 is affected the same way: fal *does* support true image edits at the API level (unlike `mock`, which always falls back to a fresh regeneration with the instruction merged into the prompt), but the edit call also submits the base image as a local path, so it's subject to the same unimplemented-upload gap — don't assume it edits the base image faithfully under `fal` until that gap is closed. None of this is something you can fix here. Mention it to the user only if it seems relevant to what they're asking for (e.g. they expect edits to preserve the base image exactly).
```

Replace with:

```markdown
**Note on the real `fal` provider:** `characters[].reference` conditioning works against the real API — the fal providers upload each local reference file and send fal the resulting URL. `edit-candidate` in Step 5 also genuinely edits the base image under `fal` (unlike `mock`, which always falls back to a fresh regeneration with the instruction merged into the prompt) — the base image is uploaded the same way. Nothing to warn the user about here beyond normal generation variance.
```

- [ ] **Step 4: Run the full pytest suite**

Run: `pytest -q`
Expected: every test passes, including all of Tasks 1-4's new tests.

- [ ] **Step 5: End-to-end golden-path CLI walkthrough**

This proves the whole feature — environment locking, environment-first image references, same-scene previous-shot chaining, and locked-image-only video conditioning — works together against a real scratch project, using only the `ai-film` CLI and the mock provider (no fakes/monkeypatches, no agent dispatch — this mirrors the exact bug from the live `one-more-life` project that motivated this spec: a scene's second shot silently drifting to a different location because it had no way to inherit the first shot's locked location).

Run this script (adjust `ai-film` to `.venv/bin/ai-film` if that's what Task 0-style resolution in this repo requires — check with `ai-film version` first):

```bash
set -e
WORK=$(mktemp -d)
ai-film init "Golden Path" --path "$WORK/project"
python3 -c "
import json
config_path = '$WORK/project/config.json'
config = json.load(open(config_path))
for stage in config['providers']:
    config['providers'][stage]['provider'] = 'mock'
json.dump(config, open(config_path, 'w'))
"

# 1. Lock an environment via the existing env: candidate loop (no engine changes needed here).
ai-film approve-generation --scope bibles --targets env:hospital_corridor --path "$WORK/project"
ai-film generate-candidates --target env:hospital_corridor --count 1 --prompt "hospital corridor, white walls" --path "$WORK/project"
ai-film select-candidate --target env:hospital_corridor --id 001 --path "$WORK/project"
test -f "$WORK/project/assets/environments/hospital_corridor/reference.png" && echo "PASS: environment locked"

# 2. Write two shots in the same scene, both referencing the locked environment, neither with an image yet.
python3 -c "
import json
shot_common = {
    'schema_version': '1.0', 'status': 'draft', 'duration_seconds': 3,
    'continuity': {'status': 'pending', 'checked_at': None, 'issues': []},
    'visual': {'style': 'grounded drama'}, 'camera': {'shot': 'wide', 'movement': 'static'},
    'dialogue': {'text': '', 'speaker': ''}, 'characters': [],
    'environment': {'name': 'hospital_corridor', 'reference': 'assets/environments/hospital_corridor/reference.png'},
    'generation': {
        'image': {'status': 'pending', 'attempts': 0}, 'video': {'status': 'pending', 'attempts': 0},
        'voice': {'status': 'not_required'}, 'sfx': {'status': 'not_required'}, 'music': {'status': 'not_required'},
    },
}
for shot_id, action in [('S01_SH01', 'a lone engineer walks into the corridor'), ('S01_SH02', 'a doctor closes the file')]:
    shot = dict(shot_common, id=shot_id, action=action)
    json.dump(shot, open(f'$WORK/project/03_shots/{shot_id}.json', 'w'))
"
ai-film approve-generation --scope storyboard --targets S01_SH01,S01_SH02 --path "$WORK/project"

# 3. Generate S01_SH02's image FIRST (out of order, on purpose) — its predecessor S01_SH01
#    has no locked image yet, so the CLI must note the missing continuity anchor on stderr
#    rather than silently proceeding.
ai-film generate-image --shot S01_SH02 --path "$WORK/project" 2>&1 | grep -q "S01_SH02's predecessor in this scene has no locked image yet" \
  && echo "PASS: missing-predecessor note printed"

# 4. Now lock S01_SH01 (the scene's genuine first shot — no note expected).
ai-film generate-image --shot S01_SH01 --path "$WORK/project" 2>&1 | tee /tmp/sh01_out.txt
grep -q "predecessor" /tmp/sh01_out.txt && echo "FAIL: unexpected predecessor note on scene's first shot" || echo "PASS: no predecessor note on first shot"

# 5. Regenerate S01_SH02 with --force — S01_SH01 is locked now, so this time the note must be absent.
ai-film generate-image --shot S01_SH02 --path "$WORK/project" --force 2>&1 | tee /tmp/sh02_out.txt
grep -q "predecessor" /tmp/sh02_out.txt && echo "FAIL: note still present after predecessor locked" || echo "PASS: chaining resolved, no note"

# 6. Generate video for both shots. S01_SH01's own locked image is now used as its sole
#    reference (there's no "previous shot" concept for video — §4.3 only cares about the
#    shot's own locked image). S01_SH02 must also condition on its own locked image now,
#    not raw environment/character references.
ai-film generate-video --shot S01_SH01 --path "$WORK/project"
ai-film generate-video --shot S01_SH02 --path "$WORK/project"
ai-film validate --path "$WORK/project" && echo "PASS: all shots valid"
ai-film status --path "$WORK/project"
rm -rf "$WORK"
```

Expected output includes every line: `PASS: environment locked`, `PASS: missing-predecessor note printed`, `PASS: no predecessor note on first shot`, `PASS: chaining resolved, no note`, `PASS: all shots valid`, and `ai-film status`'s final summary showing `S01_SH01  completed` and `S01_SH02  completed` (`compute_status` reports `completed` once image and video are both done, regardless of continuity — `voice`/`sfx`/`music` are already `not_required`; continuity was never explicitly checked in this script and that's fine, it only gates the "ready" status, never "completed"). If any `PASS` line is missing or a `FAIL` line appears, stop and fix the underlying task (identify which of Tasks 1-4's changes the failure traces back to) before proceeding — do not edit this script to make failures disappear.

- [ ] **Step 6: Commit**

```bash
git add README.md .claude/agents/ai-film-character.md
git commit -m "docs: correct stale fal reference-upload claims and document location locking"
```
