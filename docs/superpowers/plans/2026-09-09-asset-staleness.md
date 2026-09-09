# Asset Staleness Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Know which already-generated shots were built against a character/environment reference image that has since changed, via a read-only `ai-film check-stale` report.

**Architecture:** `generate_image` (in `generation_service.py`) records a sha256 content hash of each reference file it actually used, in the shot's `generation.image.source_assets` list. A new module, `asset_staleness.py`, recomputes those hashes on demand and reports mismatches. No auto-regeneration, no diff of *what* changed — a report only.

**Tech Stack:** Python 3.11 stdlib `hashlib` (no new dependencies), typer, pytest.

**Spec:** `docs/superpowers/specs/2026-09-04-template-library-design.md` (see its "Asset staleness tracking" section)

## Global Constraints

- No new pip dependencies.
- Content identity via sha256 hash, not a version-number scheme — no separate "current version" pointer to maintain.
- `shot.json`'s existing `artifact.sha256` field is currently always `None` in practice — this is the first thing to actually populate a sha256 field in this data model, not a reuse of an already-working mechanism (do not assume any other code already computes real hashes).
- Scoped to `generation.image` only in v1 — the stage that directly consumes `characters[]`/`environment` references. Do not extend to video/voice/sfx/music stages in this plan.
- `check-stale` is read-only: it reports, it never triggers regeneration or any other side effect.
- No diff of *what* changed about an asset — a changed/unchanged boolean signal per asset path is the entire output.
- A `source_assets` path that no longer exists on disk (the file was deleted, not just changed) is skipped, not reported as stale — there's nothing to compare against, and inventing a "missing" state this plan doesn't ask for would be scope creep.

---

### Task 1: Record `source_assets` at image-generation time

**Files:**
- Modify: `src/ai_film/services/generation_service.py`
- Test: `tests/services/test_generation_service.py` (append — check its existing imports first)

**Interfaces:**
- Produces: `sha256_of_file(path: Path) -> str` (module-level, no leading underscore — shared with Task 2's `asset_staleness.py`); `run_generation_stage(..., source_assets: list[dict] | None = None)` gains this new keyword-only-by-convention parameter, storing it into the completed stage record under `source_assets`. `generate_image` computes and passes it automatically from its existing `reference_paths` parameter — no new parameter on `generate_image`'s own signature.

- [ ] **Step 1: Write the failing tests**

Append to `tests/services/test_generation_service.py`, which already has `_project(tmp_path)` and `_shot_path(project_dir)` helpers and imports `approve_generation` and `MockImageProvider` — the exact pattern `test_generate_image_succeeds_once_approved` (already in this file) uses:

```python
from ai_film.services.generation_service import sha256_of_file


def test_sha256_of_file_matches_known_content(tmp_path: Path):
    file_path = tmp_path / "ref.png"
    file_path.write_bytes(b"REF-PNG-CONTENT")
    import hashlib
    expected = hashlib.sha256(b"REF-PNG-CONTENT").hexdigest()

    assert sha256_of_file(file_path) == expected


def test_generate_image_records_source_assets_with_current_hashes(tmp_path: Path):
    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)
    reference = project_dir / "assets" / "characters" / "mara" / "reference.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"MARA-REFERENCE")

    result = generate_image(
        project_dir=project_dir, shot_path=shot_path, provider=MockImageProvider(),
        prompt="a shot", model="nano-banana", reference_paths=[str(reference)],
        output_path=project_dir / "04_storyboard" / "S01_SH01.png",
        provider_name="mock",
    )

    assert result["source_assets"] == [
        {"path": "assets/characters/mara/reference.png", "sha256": sha256_of_file(reference)}
    ]


def test_generate_image_skips_reference_paths_that_do_not_exist(tmp_path: Path):
    """A reference_paths entry pointing at a file that doesn't exist (e.g.
    a continuity master-shot reference that was never generated) must not
    crash source_assets recording — it's silently omitted."""
    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)

    result = generate_image(
        project_dir=project_dir, shot_path=shot_path, provider=MockImageProvider(),
        prompt="a shot", model="nano-banana",
        reference_paths=[str(project_dir / "does_not_exist.png")],
        output_path=project_dir / "04_storyboard" / "S01_SH01.png",
        provider_name="mock",
    )

    assert result["source_assets"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/services/test_generation_service.py -v -k source_assets or sha256_of_file`
Expected: FAIL with `ImportError: cannot import name 'sha256_of_file'`

- [ ] **Step 3: Write the implementation**

Add `import hashlib` to `generation_service.py`'s imports. Add near the top of the file (module-level, alongside other small helpers):

```python
def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
```

Modify `run_generation_stage`'s signature to add the new parameter (append it after `superseded_reason: str = "regenerate",`):

```python
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
    source_assets: list[dict] | None = None,
) -> dict:
```

In the success-path dict construction (the block that sets `shot["generation"][stage] = {"provider": provider_name, ...}` right before `save_shot(shot_path, shot)` and `return shot["generation"][stage]`), add one key:

```python
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
        "source_assets": source_assets or [],
    }
```

Modify `generate_image` to compute `source_assets` from its existing `reference_paths` parameter and pass it through:

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
    source_assets = [
        {
            "path": project_relative_path(p, project_dir),
            "sha256": sha256_of_file(Path(p)),
        }
        for p in reference_paths
        if Path(p).exists()
    ]

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
        force=force, source_assets=source_assets,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/services/test_generation_service.py -v`
Expected: PASS, full file — including every pre-existing `generate_image` test, which must still pass unchanged (they don't assert on `source_assets`, so adding the key is additive and safe)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/services/generation_service.py tests/services/test_generation_service.py
git commit -m "feat: record source-asset hashes at image-generation time"
```

---

### Task 2: `check_stale`

**Files:**
- Create: `src/ai_film/asset_staleness.py`
- Test: `tests/test_asset_staleness.py`

**Interfaces:**
- Consumes: `sha256_of_file(path: Path) -> str` from `ai_film.services.generation_service` (Task 1); `list_shot_paths(shots_dir: Path) -> list[Path]`, `load_shot(path: Path) -> dict` from `ai_film.shot_store`.
- Produces: `check_stale(project_dir: Path) -> list[dict]` (each `{"shot_id": str, "changed_assets": list[str]}`) — consumed by Task 3's CLI command.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_asset_staleness.py
from pathlib import Path

from ai_film.asset_staleness import check_stale
from ai_film.shot_store import save_shot


def _shot_with_source_assets(shot_id: str, source_assets: list[dict]) -> dict:
    return {
        "schema_version": "1.0", "id": shot_id, "status": "draft", "duration_seconds": 4,
        "continuity": {"status": "passed", "checked_at": None, "issues": []},
        "generation": {
            "image": {"status": "completed", "source_assets": source_assets},
            "video": {"status": "not_required"},
            "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }


def test_check_stale_reports_nothing_when_no_shots_exist(tmp_path: Path):
    (tmp_path / "03_shots").mkdir(parents=True)
    assert check_stale(tmp_path) == []


def test_check_stale_reports_clean_when_hash_still_matches(tmp_path: Path):
    reference = tmp_path / "assets" / "characters" / "mara" / "reference.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"MARA-V1")
    from ai_film.services.generation_service import sha256_of_file

    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_source_assets("S01_SH01", [
        {"path": "assets/characters/mara/reference.png", "sha256": sha256_of_file(reference)}
    ]))

    assert check_stale(tmp_path) == []


def test_check_stale_reports_shot_whose_asset_hash_changed(tmp_path: Path):
    reference = tmp_path / "assets" / "characters" / "mara" / "reference.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"MARA-V1")
    from ai_film.services.generation_service import sha256_of_file

    old_hash = sha256_of_file(reference)
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_source_assets("S01_SH01", [
        {"path": "assets/characters/mara/reference.png", "sha256": old_hash}
    ]))

    reference.write_bytes(b"MARA-V2-CHANGED")

    result = check_stale(tmp_path)

    assert result == [
        {"shot_id": "S01_SH01", "changed_assets": ["assets/characters/mara/reference.png"]}
    ]


def test_check_stale_skips_shots_without_completed_image_generation(tmp_path: Path):
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    shot = _shot_with_source_assets("S01_SH01", [{"path": "does/not/matter.png", "sha256": "abc"}])
    shot["generation"]["image"]["status"] = "pending"
    save_shot(shot_path, shot)

    assert check_stale(tmp_path) == []


def test_check_stale_skips_asset_paths_that_no_longer_exist(tmp_path: Path):
    """A deleted reference file is skipped, not reported — there's nothing
    to compare its hash against, and this plan doesn't ask for a distinct
    "missing" state."""
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_source_assets("S01_SH01", [
        {"path": "assets/characters/deleted/reference.png", "sha256": "abc123"}
    ]))

    assert check_stale(tmp_path) == []


def test_check_stale_reports_multiple_shots_independently(tmp_path: Path):
    reference = tmp_path / "assets" / "characters" / "mara" / "reference.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"MARA-V1")
    from ai_film.services.generation_service import sha256_of_file

    old_hash = sha256_of_file(reference)
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot_with_source_assets(
        "S01_SH01", [{"path": "assets/characters/mara/reference.png", "sha256": old_hash}]
    ))
    save_shot(tmp_path / "03_shots" / "S01_SH02.json", _shot_with_source_assets(
        "S01_SH02", [{"path": "assets/characters/mara/reference.png", "sha256": old_hash}]
    ))

    reference.write_bytes(b"MARA-V2-CHANGED")

    result = check_stale(tmp_path)

    assert {r["shot_id"] for r in result} == {"S01_SH01", "S01_SH02"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_asset_staleness.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_film.asset_staleness'`

- [ ] **Step 3: Write the implementation**

```python
# src/ai_film/asset_staleness.py
"""Read-only report of which shots were generated against a character or
environment reference image that has since changed. See
docs/superpowers/specs/2026-09-04-template-library-design.md's "Asset
staleness tracking" section. Never triggers regeneration itself."""

from __future__ import annotations

from pathlib import Path

from ai_film.services.generation_service import sha256_of_file
from ai_film.shot_store import list_shot_paths, load_shot


def check_stale(project_dir: Path) -> list[dict]:
    shots_dir = project_dir / "03_shots"
    if not shots_dir.exists():
        return []

    stale = []
    for shot_path in list_shot_paths(shots_dir):
        shot = load_shot(shot_path)
        image_stage = shot.get("generation", {}).get("image", {})
        if image_stage.get("status") != "completed":
            continue

        changed_assets = []
        for asset in image_stage.get("source_assets") or []:
            asset_path = project_dir / asset["path"]
            if not asset_path.exists():
                continue
            if sha256_of_file(asset_path) != asset["sha256"]:
                changed_assets.append(asset["path"])

        if changed_assets:
            stale.append({"shot_id": shot["id"], "changed_assets": changed_assets})

    return stale
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_asset_staleness.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/asset_staleness.py tests/test_asset_staleness.py
git commit -m "feat: add check_stale for asset-version staleness detection"
```

---

### Task 3: CLI command — `check-stale`

**Files:**
- Modify: `src/ai_film/cli.py` (add import; add command — insert alongside the other read-only, zero-cost project-scoped commands, e.g. near `status_cmd`/`validate_cmd`; run `grep -n '@app.command(name="validate")' src/ai_film/cli.py` to find the current location)
- Modify: `.claude/settings.json` (add to both permission lists — this is a non-spend, read-only command, same category as `status`/`validate`)
- Test: `tests/test_cli_asset_staleness_commands.py` (new file)

**Interfaces:**
- Consumes: `check_stale(project_dir: Path) -> list[dict]` from `ai_film.asset_staleness` (Task 2).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_asset_staleness_commands.py
from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.project import init_project
from ai_film.services.generation_service import sha256_of_file
from ai_film.shot_store import save_shot

runner = CliRunner()


def _shot_with_source_assets(shot_id: str, source_assets: list[dict]) -> dict:
    return {
        "schema_version": "1.0", "id": shot_id, "status": "draft", "duration_seconds": 4,
        "continuity": {"status": "passed", "checked_at": None, "issues": []},
        "generation": {
            "image": {"status": "completed", "source_assets": source_assets},
            "video": {"status": "not_required"},
            "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }


def test_check_stale_cmd_reports_no_stale_shots(tmp_path: Path):
    project_dir = init_project(tmp_path / "proj", "Test")

    result = runner.invoke(app, ["check-stale", "--path", str(project_dir)])

    assert result.exit_code == 0
    assert "no stale shots" in result.output.lower()


def test_check_stale_cmd_reports_a_stale_shot(tmp_path: Path):
    project_dir = init_project(tmp_path / "proj", "Test")
    reference = project_dir / "assets" / "characters" / "mara" / "reference.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"MARA-V1")
    old_hash = sha256_of_file(reference)
    save_shot(project_dir / "03_shots" / "S01_SH01.json", _shot_with_source_assets(
        "S01_SH01", [{"path": "assets/characters/mara/reference.png", "sha256": old_hash}]
    ))
    reference.write_bytes(b"MARA-V2-CHANGED")

    result = runner.invoke(app, ["check-stale", "--path", str(project_dir)])

    assert result.exit_code == 0
    assert "S01_SH01" in result.output
    assert "assets/characters/mara/reference.png" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_asset_staleness_commands.py -v`
Expected: FAIL — `check-stale` is not a registered command.

- [ ] **Step 3: Write the implementation**

Add to `src/ai_film/cli.py`'s import block:

```python
from ai_film.asset_staleness import check_stale as check_stale_service
```

Add the command near `status_cmd`/`validate_cmd`:

```python
@app.command(name="check-stale")
def check_stale_cmd(path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path")) -> None:
    """Report shots whose generated image was built against a character or
    environment reference that has since changed. Read-only — never
    triggers regeneration itself."""
    stale = check_stale_service(path)
    if not stale:
        typer.echo("no stale shots")
        return
    typer.echo(f"{len(stale)} shot(s) reference a changed asset:")
    for entry in stale:
        typer.echo(f"\n{entry['shot_id']}")
        for asset_path in entry["changed_assets"]:
            typer.echo(f"  {asset_path} changed since generation")
```

Edit `.claude/settings.json` — add one line after `"Bash(ai-film validate *)",` and one after `"Bash(./.venv/bin/ai-film validate *)",` (read-only, non-spend command, same category as `validate`/`status`):

```json
      "Bash(ai-film validate *)",
      "Bash(ai-film check-stale *)",
```

```json
      "Bash(./.venv/bin/ai-film validate *)",
      "Bash(./.venv/bin/ai-film check-stale *)",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli_asset_staleness_commands.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/cli.py .claude/settings.json tests/test_cli_asset_staleness_commands.py
git commit -m "feat: wire check-stale into the CLI"
```

---

### Task 4: README documentation (English + Traditional Chinese)

**Files:**
- Modify: `README.md`
- Modify: `README.zh-TW.md`

**Interfaces:** None — documentation only.

- [ ] **Step 1: Add English documentation**

Insert a new paragraph in `README.md` near the other project-status/reporting commands (search for where `validate`/`status` are documented, or place it immediately after the Template Library paragraph this session's earlier plan added — either location is fine, pick whichever existing paragraph it reads most naturally after):

```markdown
`check-stale [--path <project>]` reports every shot whose generated storyboard
image was built against a character or environment reference file that has
since changed — e.g. you re-locked Mara's reference image after already
generating shots with the old one. Read-only: it only reports, it never
queues or triggers regeneration. Re-run the normal `generate-image --force`
(and the candidate loop, if you want to review before committing) on whatever
it flags.
```

- [ ] **Step 2: Add Traditional Chinese documentation**

Insert the matching paragraph into `README.zh-TW.md` at the equivalent location, keeping command names and flags in English:

```markdown
`check-stale [--path <project>]` 會列出每一個「產生分鏡圖時所用的角色或場景參考檔案，
之後又被更換」的鏡頭——例如你在已經用舊的參考圖生成過幾個鏡頭之後，重新鎖定了 Mara
的參考圖。這個指令是唯讀的：只會回報，不會自動排入或觸發重新生成。看到它列出的鏡頭，
自己視需要重新跑一次 `generate-image --force`（如果想先審核再定案，也可以照常走
candidate loop）。
```

- [ ] **Step 3: Commit**

```bash
git add README.md README.zh-TW.md
git commit -m "docs: document check-stale"
```

---

## Self-Review Notes

- **Spec coverage:** Every element of the spec's "Asset staleness tracking" section — mechanism (Task 1), `check-stale` command and output format (Tasks 2-3), all three named non-goals (no auto-regeneration: `check-stale` never calls anything but `check_stale_service`; no diff, just changed/unchanged: `changed_assets` is a bare path list, not a diff; scoped to `generation.image` only: `check_stale` reads only `generation["image"]`, nothing else) — is covered.
- **Placeholder scan:** No TBD/TODO. Task 1's tests note where to borrow this test file's existing fixture-construction pattern rather than inventing one blind, since the actual helper names in `tests/services/test_generation_service.py` weren't re-derived here — that's a deliberate "match existing conventions" instruction, not a placeholder for unwritten logic; the actual new assertions and implementation code are complete.
- **Type consistency:** `sha256_of_file(path: Path) -> str` defined once in Task 1, imported identically in Task 2's `asset_staleness.py` and reused directly in Task 2/3's tests. `check_stale(project_dir: Path) -> list[dict]` defined once in Task 2, consumed with the identical signature in Task 3's CLI command. The `source_assets`/`changed_assets` field names and shapes are identical across Tasks 1-3.
