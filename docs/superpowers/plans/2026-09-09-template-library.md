# Template Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an approved reference-video analysis be saved as a named, reusable template (`templates/<id>/template.json`) that any future project can point at to pre-fill shot drafts, and let templates be exported/imported as portable zip bundles.

**Architecture:** A new local-only module (`template_store.py`, no provider abstraction, matching `reference_analysis.py`'s established pattern) handles template file I/O: validate + save, list, show, export, import. New CLI commands wrap each function. Two existing agents (`ai-film-director`, `ai-film-storyboard`) gain small additive hooks to apply a named template when a project's `config.json` names one; `ai-film-reference-analyst` gains a step offering to save an approved brief as a template; `/ai-film-setup` gains an optional prompt to pick one.

**Tech Stack:** Python 3.11, `jsonschema` (already a dependency), stdlib `zipfile`/`shutil` (no new dependencies), typer, pytest.

**Spec:** `docs/superpowers/specs/2026-09-04-template-library-design.md`

## Global Constraints

- No new pip dependencies.
- Templates live at `templates/<id>/` relative to the current working directory (matching every other CLI command's `DEFAULT_PROJECT_PATH = Path("project")` CWD-relative convention) — never inside a project directory.
- `template.json` schema fields, exact names: top-level `schema_version`, `id`, `name`, `created_at`, `source_note`, `shot_patterns` (array). Each shot pattern: `order`, `pattern_name`, `camera`, `subject_motion`, `framing`, `suggested_duration_seconds`, `reference_keyframe` — all required except `suggested_duration_seconds`/`reference_keyframe`, which may be `null`.
- No numeric camera-geometry fields, no music/BGM fields (see spec Non-Goals).
- `save-template`/`import-template` refuse to overwrite an existing `templates/<id>/` without `--force` — same idempotency convention as every generation command in this project.
- `save-template` performs schema validation and file placement only — it never generates or rewrites template *content* itself; that's the calling agent's job.
- Templates are optional context a project points at via a plain string `config.json` key (`"template": "hero-orbit"`) — no object wrapper, no version pin.
- Precedence when applying a template: explicit user/story requirement > template > agent default (agents never force a template's suggestion over an explicit story requirement).
- `templates/` needs no `.gitignore` change — already excluded by the existing `/*` allowlist rule (confirmed empirically during spec design).

---

### Task 1: `TEMPLATE_SCHEMA` and `validate_template`

**Files:**
- Modify: `src/ai_film/schema.py` (append)
- Test: `tests/test_schema.py` (append)

**Interfaces:**
- Produces: `TEMPLATE_SCHEMA` (dict), `validate_template(data: dict) -> list[str]` — consumed by Task 2's `save_template` and Task 5's `import_template`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_schema.py` (check its existing imports first — it already imports from `ai_film.schema`; add `TEMPLATE_SCHEMA, validate_template` to that import):

```python
def _valid_template() -> dict:
    return {
        "schema_version": "1.0",
        "id": "hero-orbit",
        "name": "Hero Orbit Reveal",
        "created_at": "2026-09-04T12:00:00Z",
        "source_note": "Reference clip of a hero introduction",
        "shot_patterns": [
            {
                "order": 0,
                "pattern_name": "establish",
                "camera": "wide shot, static",
                "subject_motion": "N/A",
                "framing": "wide, subject not yet visible",
                "suggested_duration_seconds": 3,
                "reference_keyframe": None,
            }
        ],
    }


def test_validate_template_accepts_valid_template():
    assert validate_template(_valid_template()) == []


def test_validate_template_rejects_missing_shot_patterns():
    template = _valid_template()
    del template["shot_patterns"]
    errors = validate_template(template)
    assert any("shot_patterns" in e for e in errors)


def test_validate_template_rejects_shot_pattern_missing_camera():
    template = _valid_template()
    del template["shot_patterns"][0]["camera"]
    errors = validate_template(template)
    assert any("camera" in e for e in errors)


def test_validate_template_allows_null_reference_keyframe_and_duration():
    template = _valid_template()
    template["shot_patterns"][0]["reference_keyframe"] = None
    template["shot_patterns"][0]["suggested_duration_seconds"] = None
    assert validate_template(template) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_schema.py -v -k template`
Expected: FAIL with `ImportError: cannot import name 'TEMPLATE_SCHEMA'`

- [ ] **Step 3: Write the implementation**

Append to `src/ai_film/schema.py`:

```python
SHOT_PATTERN_SCHEMA = {
    "type": "object",
    "required": [
        "order", "pattern_name", "camera", "subject_motion", "framing",
        "suggested_duration_seconds", "reference_keyframe",
    ],
    "properties": {
        "order": {"type": "integer"},
        "pattern_name": {"type": "string"},
        "camera": {"type": "string"},
        "subject_motion": {"type": "string"},
        "framing": {"type": "string"},
        "suggested_duration_seconds": {"type": ["number", "null"]},
        "reference_keyframe": {"type": ["string", "null"]},
    },
}

TEMPLATE_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["schema_version", "id", "name", "shot_patterns"],
    "properties": {
        "schema_version": {"type": "string"},
        "id": {"type": "string"},
        "name": {"type": "string"},
        "created_at": {"type": ["string", "null"]},
        "source_note": {"type": ["string", "null"]},
        "shot_patterns": {"type": "array", "items": SHOT_PATTERN_SCHEMA},
    },
}


def validate_template(data: dict) -> list[str]:
    validator = jsonschema.Draft7Validator(TEMPLATE_SCHEMA)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    return [
        f"{'.'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_schema.py -v -k template`
Expected: PASS (4 new tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/schema.py tests/test_schema.py
git commit -m "feat: add TEMPLATE_SCHEMA and validate_template"
```

---

### Task 2: `save_template`

**Files:**
- Create: `src/ai_film/template_store.py`
- Test: `tests/test_template_store.py`

**Interfaces:**
- Consumes: `validate_template(data: dict) -> list[str]` from `ai_film.schema` (Task 1).
- Produces: `save_template(from_path: Path, template_id: str, templates_dir: Path, force: bool = False) -> dict` — consumed by Task 4's CLI command. Also produces the module itself, which Tasks 3 and 5 append to.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_template_store.py
import json
from pathlib import Path

import pytest

from ai_film.template_store import save_template


def _draft(**overrides) -> dict:
    draft = {
        "schema_version": "1.0",
        "id": "placeholder",
        "name": "Hero Orbit Reveal",
        "created_at": "2026-09-04T12:00:00Z",
        "source_note": "test",
        "shot_patterns": [
            {
                "order": 0,
                "pattern_name": "establish",
                "camera": "wide shot, static",
                "subject_motion": "N/A",
                "framing": "wide",
                "suggested_duration_seconds": 3,
                "reference_keyframe": None,
            }
        ],
    }
    draft.update(overrides)
    return draft


def _write_draft(path: Path, draft: dict) -> None:
    path.write_text(json.dumps(draft))


def test_save_template_rejects_missing_draft_file(tmp_path: Path):
    with pytest.raises(ValueError, match="not found"):
        save_template(tmp_path / "missing.json", "hero-orbit", tmp_path / "templates")


def test_save_template_rejects_invalid_draft(tmp_path: Path):
    draft_path = tmp_path / "draft.json"
    draft = _draft()
    del draft["shot_patterns"]
    _write_draft(draft_path, draft)

    with pytest.raises(ValueError, match="schema validation"):
        save_template(draft_path, "hero-orbit", tmp_path / "templates")


def test_save_template_writes_template_json_with_given_id(tmp_path: Path):
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, _draft())
    templates_dir = tmp_path / "templates"

    result = save_template(draft_path, "hero-orbit", templates_dir)

    assert result["id"] == "hero-orbit"
    written = json.loads((templates_dir / "hero-orbit" / "template.json").read_text())
    assert written["id"] == "hero-orbit"
    assert written["name"] == "Hero Orbit Reveal"


def test_save_template_copies_referenced_keyframes(tmp_path: Path):
    keyframe = tmp_path / "shot1_start.jpg"
    keyframe.write_bytes(b"FAKE-JPG")
    draft = _draft()
    draft["shot_patterns"][0]["reference_keyframe"] = "shot1_start.jpg"
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, draft)
    templates_dir = tmp_path / "templates"

    result = save_template(draft_path, "hero-orbit", templates_dir)

    copied = templates_dir / "hero-orbit" / "keyframes" / "shot1_start.jpg"
    assert copied.exists()
    assert result["shot_patterns"][0]["reference_keyframe"] == "keyframes/shot1_start.jpg"


def test_save_template_rejects_missing_referenced_keyframe(tmp_path: Path):
    draft = _draft()
    draft["shot_patterns"][0]["reference_keyframe"] = "does_not_exist.jpg"
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, draft)

    with pytest.raises(ValueError, match="reference_keyframe"):
        save_template(draft_path, "hero-orbit", tmp_path / "templates")


def test_save_template_refuses_existing_without_force(tmp_path: Path):
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, _draft())
    templates_dir = tmp_path / "templates"
    save_template(draft_path, "hero-orbit", templates_dir)

    with pytest.raises(RuntimeError, match="already exists"):
        save_template(draft_path, "hero-orbit", templates_dir)


def test_save_template_force_overwrites(tmp_path: Path):
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, _draft())
    templates_dir = tmp_path / "templates"
    save_template(draft_path, "hero-orbit", templates_dir)

    save_template(draft_path, "hero-orbit", templates_dir, force=True)  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_template_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_film.template_store'`

- [ ] **Step 3: Write the implementation**

```python
# src/ai_film/template_store.py
"""Local file operations for the template library — save/list/show/
export/import. No provider abstraction, no generation calls; this module
only validates and moves files. See
docs/superpowers/specs/2026-09-04-template-library-design.md."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ai_film.schema import validate_template


def save_template(
    from_path: Path, template_id: str, templates_dir: Path, force: bool = False
) -> dict:
    if not from_path.exists():
        raise ValueError(f"draft file not found: {from_path}")

    draft = json.loads(from_path.read_text())
    errors = validate_template(draft)
    if errors:
        raise ValueError(f"draft failed schema validation: {'; '.join(errors)}")

    target_dir = templates_dir / template_id
    if target_dir.exists():
        if not force:
            raise RuntimeError(f"{target_dir} already exists — pass --force to overwrite")
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True)

    keyframes_dir = target_dir / "keyframes"
    for pattern in draft.get("shot_patterns", []):
        keyframe = pattern.get("reference_keyframe")
        if not keyframe:
            continue
        source_keyframe = from_path.parent / keyframe
        if not source_keyframe.exists():
            raise ValueError(f"reference_keyframe not found: {source_keyframe}")
        keyframes_dir.mkdir(parents=True, exist_ok=True)
        dest = keyframes_dir / Path(keyframe).name
        shutil.copyfile(source_keyframe, dest)
        pattern["reference_keyframe"] = f"keyframes/{dest.name}"

    draft["id"] = template_id
    (target_dir / "template.json").write_text(json.dumps(draft, indent=2, ensure_ascii=False))
    return draft
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_template_store.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/template_store.py tests/test_template_store.py
git commit -m "feat: add save_template for the template library"
```

---

### Task 3: `list_templates` and `show_template`

**Files:**
- Modify: `src/ai_film/template_store.py` (append)
- Test: `tests/test_template_store.py` (append)

**Interfaces:**
- Consumes: nothing new (reads `template.json` files `save_template` already writes).
- Produces: `list_templates(templates_dir: Path) -> list[dict]` (each `{"id": str, "name": str, "shot_pattern_count": int}`), `show_template(template_id: str, templates_dir: Path) -> dict` — both consumed by Task 4's CLI commands.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_template_store.py`:

```python
from ai_film.template_store import list_templates, show_template


def test_list_templates_empty_directory_returns_empty_list(tmp_path: Path):
    assert list_templates(tmp_path / "templates") == []


def test_list_templates_returns_id_name_and_pattern_count(tmp_path: Path):
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, _draft())
    templates_dir = tmp_path / "templates"
    save_template(draft_path, "hero-orbit", templates_dir)

    results = list_templates(templates_dir)

    assert results == [{"id": "hero-orbit", "name": "Hero Orbit Reveal", "shot_pattern_count": 1}]


def test_list_templates_skips_directories_without_template_json(tmp_path: Path):
    templates_dir = tmp_path / "templates"
    (templates_dir / "not-a-template").mkdir(parents=True)

    assert list_templates(templates_dir) == []


def test_show_template_returns_full_content(tmp_path: Path):
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, _draft())
    templates_dir = tmp_path / "templates"
    save_template(draft_path, "hero-orbit", templates_dir)

    result = show_template("hero-orbit", templates_dir)

    assert result["id"] == "hero-orbit"
    assert result["name"] == "Hero Orbit Reveal"


def test_show_template_rejects_unknown_id(tmp_path: Path):
    with pytest.raises(ValueError, match="not found"):
        show_template("does-not-exist", tmp_path / "templates")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_template_store.py -v -k "list_templates or show_template"`
Expected: FAIL with `ImportError: cannot import name 'list_templates'`

- [ ] **Step 3: Write the implementation**

Append to `src/ai_film/template_store.py`:

```python
def list_templates(templates_dir: Path) -> list[dict]:
    if not templates_dir.exists():
        return []
    results = []
    for template_dir in sorted(p for p in templates_dir.iterdir() if p.is_dir()):
        template_path = template_dir / "template.json"
        if not template_path.exists():
            continue
        data = json.loads(template_path.read_text())
        results.append({
            "id": data.get("id", template_dir.name),
            "name": data.get("name", ""),
            "shot_pattern_count": len(data.get("shot_patterns", [])),
        })
    return results


def show_template(template_id: str, templates_dir: Path) -> dict:
    template_path = templates_dir / template_id / "template.json"
    if not template_path.exists():
        raise ValueError(f"template not found: {template_id}")
    return json.loads(template_path.read_text())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_template_store.py -v`
Expected: PASS (12 tests total so far)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/template_store.py tests/test_template_store.py
git commit -m "feat: add list_templates and show_template"
```

---

### Task 4: CLI commands — `save-template`, `list-templates`, `show-template`

**Files:**
- Modify: `src/ai_film/cli.py` (add imports near the top; add three new commands — insert after `analyze_reference_video_cmd` and before `@app.command(name="review-media")`, matching this codebase's existing cluster of local-only, zero-cost commands — run `grep -n '@app.command(name="review-media")' src/ai_film/cli.py` first to confirm the current line number, since it may have shifted)
- Modify: `.claude/settings.json` (add three new command names to both permission lists, next to `analyze-reference-video`)
- Test: `tests/test_cli_template_commands.py` (new file)

**Interfaces:**
- Consumes: `save_template`, `list_templates`, `show_template` from `ai_film.template_store` (Tasks 2-3).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_template_commands.py
import json
from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app

runner = CliRunner()


def _draft(**overrides) -> dict:
    draft = {
        "schema_version": "1.0",
        "id": "placeholder",
        "name": "Hero Orbit Reveal",
        "created_at": "2026-09-04T12:00:00Z",
        "source_note": "test",
        "shot_patterns": [
            {
                "order": 0,
                "pattern_name": "establish",
                "camera": "wide shot, static",
                "subject_motion": "N/A",
                "framing": "wide",
                "suggested_duration_seconds": 3,
                "reference_keyframe": None,
            }
        ],
    }
    draft.update(overrides)
    return draft


def test_save_template_cmd_rejects_missing_draft(tmp_path: Path):
    result = runner.invoke(
        app,
        [
            "save-template", "--from", str(tmp_path / "missing.json"), "--id", "hero-orbit",
            "--templates-dir", str(tmp_path / "templates"),
        ],
    )
    assert result.exit_code == 1
    assert "not found" in result.output


def test_save_template_cmd_then_list_and_show(tmp_path: Path):
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(_draft()))
    templates_dir = tmp_path / "templates"

    save_result = runner.invoke(
        app,
        [
            "save-template", "--from", str(draft_path), "--id", "hero-orbit",
            "--templates-dir", str(templates_dir),
        ],
    )
    assert save_result.exit_code == 0
    assert "hero-orbit" in save_result.output

    list_result = runner.invoke(app, ["list-templates", "--templates-dir", str(templates_dir)])
    assert list_result.exit_code == 0
    assert "hero-orbit" in list_result.output
    assert "Hero Orbit Reveal" in list_result.output

    show_result = runner.invoke(
        app, ["show-template", "--id", "hero-orbit", "--templates-dir", str(templates_dir)]
    )
    assert show_result.exit_code == 0
    assert '"id": "hero-orbit"' in show_result.output


def test_list_templates_cmd_empty_prints_no_templates_message(tmp_path: Path):
    result = runner.invoke(app, ["list-templates", "--templates-dir", str(tmp_path / "templates")])
    assert result.exit_code == 0
    assert "no templates" in result.output.lower()


def test_show_template_cmd_rejects_unknown_id(tmp_path: Path):
    result = runner.invoke(
        app, ["show-template", "--id", "does-not-exist", "--templates-dir", str(tmp_path / "templates")]
    )
    assert result.exit_code == 1
    assert "not found" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_template_commands.py -v`
Expected: FAIL — commands not registered (typer reports "No such command").

- [ ] **Step 3: Write the implementation**

Add to `src/ai_film/cli.py`'s import block, alongside the `reference_analysis` import:

```python
from ai_film.template_store import (
    list_templates as list_templates_service,
    save_template as save_template_service,
    show_template as show_template_service,
)
```

Add a module constant near `DEFAULT_PROJECT_PATH`:

```python
DEFAULT_TEMPLATES_PATH = Path("templates")
```

Add the three commands, after `analyze_reference_video_cmd` and before `@app.command(name="review-media")`:

```python
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
```

Edit `.claude/settings.json` — add three lines after `"Bash(ai-film analyze-reference-video *)",` and three after `"Bash(./.venv/bin/ai-film analyze-reference-video *)",`:

```json
      "Bash(ai-film analyze-reference-video *)",
      "Bash(ai-film save-template *)",
      "Bash(ai-film list-templates *)",
      "Bash(ai-film show-template *)",
```

```json
      "Bash(./.venv/bin/ai-film analyze-reference-video *)",
      "Bash(./.venv/bin/ai-film save-template *)",
      "Bash(./.venv/bin/ai-film list-templates *)",
      "Bash(./.venv/bin/ai-film show-template *)",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli_template_commands.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/cli.py .claude/settings.json tests/test_cli_template_commands.py
git commit -m "feat: wire save-template/list-templates/show-template into the CLI"
```

---

### Task 5: `export_template` and `import_template`

**Files:**
- Modify: `src/ai_film/template_store.py` (append)
- Test: `tests/test_template_store.py` (append)

**Interfaces:**
- Consumes: `validate_template` from `ai_film.schema` (Task 1).
- Produces: `export_template(template_id: str, templates_dir: Path, output_path: Path) -> None`, `import_template(from_path: Path, templates_dir: Path, template_id: str | None = None, force: bool = False) -> dict` — consumed by Task 6's CLI commands.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_template_store.py` (add `import zipfile` to the top of the file):

```python
from ai_film.template_store import export_template, import_template


def test_export_template_rejects_unknown_id(tmp_path: Path):
    with pytest.raises(ValueError, match="not found"):
        export_template("does-not-exist", tmp_path / "templates", tmp_path / "out.zip")


def test_export_then_import_round_trips_template_json_and_keyframes(tmp_path: Path):
    keyframe = tmp_path / "shot1_start.jpg"
    keyframe.write_bytes(b"FAKE-JPG")
    draft = _draft()
    draft["shot_patterns"][0]["reference_keyframe"] = "shot1_start.jpg"
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, draft)
    templates_dir = tmp_path / "templates"
    save_template(draft_path, "hero-orbit", templates_dir)

    archive_path = tmp_path / "hero-orbit.zip"
    export_template("hero-orbit", templates_dir, archive_path)
    assert archive_path.exists()
    with zipfile.ZipFile(archive_path) as zf:
        assert "template.json" in zf.namelist()
        assert "keyframes/shot1_start.jpg" in zf.namelist()

    new_templates_dir = tmp_path / "imported-templates"
    result = import_template(archive_path, new_templates_dir)

    assert result["id"] == "hero-orbit"
    imported = json.loads((new_templates_dir / "hero-orbit" / "template.json").read_text())
    assert imported["name"] == "Hero Orbit Reveal"
    assert (new_templates_dir / "hero-orbit" / "keyframes" / "shot1_start.jpg").exists()


def test_import_template_honors_id_override(tmp_path: Path):
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, _draft())
    templates_dir = tmp_path / "templates"
    save_template(draft_path, "hero-orbit", templates_dir)
    archive_path = tmp_path / "hero-orbit.zip"
    export_template("hero-orbit", templates_dir, archive_path)

    new_templates_dir = tmp_path / "imported-templates"
    result = import_template(archive_path, new_templates_dir, template_id="hero-orbit-v2")

    assert result["id"] == "hero-orbit-v2"
    assert (new_templates_dir / "hero-orbit-v2" / "template.json").exists()


def test_import_template_rejects_archive_without_template_json(tmp_path: Path):
    archive_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("not-a-template.txt", "hello")

    with pytest.raises(ValueError, match="template.json"):
        import_template(archive_path, tmp_path / "templates")


def test_import_template_rejects_invalid_template_json(tmp_path: Path):
    archive_path = tmp_path / "bad.zip"
    invalid = _draft()
    del invalid["shot_patterns"]
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("template.json", json.dumps(invalid))

    with pytest.raises(ValueError, match="schema validation"):
        import_template(archive_path, tmp_path / "templates")


def test_import_template_refuses_existing_without_force(tmp_path: Path):
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, _draft())
    templates_dir = tmp_path / "templates"
    save_template(draft_path, "hero-orbit", templates_dir)
    archive_path = tmp_path / "hero-orbit.zip"
    export_template("hero-orbit", templates_dir, archive_path)

    with pytest.raises(RuntimeError, match="already exists"):
        import_template(archive_path, templates_dir)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_template_store.py -v -k "export_template or import_template"`
Expected: FAIL with `ImportError: cannot import name 'export_template'`

- [ ] **Step 3: Write the implementation**

Add `import zipfile` to `src/ai_film/template_store.py`'s imports. Append:

```python
def export_template(template_id: str, templates_dir: Path, output_path: Path) -> None:
    source_dir = templates_dir / template_id
    if not source_dir.exists():
        raise ValueError(f"template not found: {template_id}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(source_dir.rglob("*")):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(source_dir))


def import_template(
    from_path: Path,
    templates_dir: Path,
    template_id: str | None = None,
    force: bool = False,
) -> dict:
    if not from_path.exists():
        raise ValueError(f"archive not found: {from_path}")

    with zipfile.ZipFile(from_path) as zf:
        names = zf.namelist()
        if "template.json" not in names:
            raise ValueError(f"{from_path} does not contain a template.json")
        draft = json.loads(zf.read("template.json"))
        errors = validate_template(draft)
        if errors:
            raise ValueError(f"imported template.json failed schema validation: {'; '.join(errors)}")

        resolved_id = template_id or draft.get("id")
        if not resolved_id:
            raise ValueError("no --id given and template.json has no id field")

        target_dir = templates_dir / resolved_id
        if target_dir.exists():
            if not force:
                raise RuntimeError(f"{target_dir} already exists — pass --force to overwrite")
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True)
        zf.extractall(target_dir)

    draft["id"] = resolved_id
    (target_dir / "template.json").write_text(json.dumps(draft, indent=2, ensure_ascii=False))
    return draft
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_template_store.py -v`
Expected: PASS (18 tests total so far)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/template_store.py tests/test_template_store.py
git commit -m "feat: add export_template and import_template"
```

---

### Task 6: CLI commands — `export-template`, `import-template`

**Files:**
- Modify: `src/ai_film/cli.py` (add import, add two commands after `show_template_cmd`)
- Modify: `.claude/settings.json` (add two more command names to both permission lists)
- Test: `tests/test_cli_template_commands.py` (append)

**Interfaces:**
- Consumes: `export_template`, `import_template` from `ai_film.template_store` (Task 5).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_template_commands.py`:

```python
def test_export_and_import_template_cmd_round_trip(tmp_path: Path):
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(_draft()))
    templates_dir = tmp_path / "templates"
    runner.invoke(
        app,
        [
            "save-template", "--from", str(draft_path), "--id", "hero-orbit",
            "--templates-dir", str(templates_dir),
        ],
    )
    archive_path = tmp_path / "hero-orbit.zip"

    export_result = runner.invoke(
        app,
        [
            "export-template", "--id", "hero-orbit", "--templates-dir", str(templates_dir),
            "--output", str(archive_path),
        ],
    )
    assert export_result.exit_code == 0
    assert archive_path.exists()

    new_templates_dir = tmp_path / "imported-templates"
    import_result = runner.invoke(
        app,
        [
            "import-template", "--from", str(archive_path),
            "--templates-dir", str(new_templates_dir),
        ],
    )
    assert import_result.exit_code == 0
    assert (new_templates_dir / "hero-orbit" / "template.json").exists()


def test_export_template_cmd_rejects_unknown_id(tmp_path: Path):
    result = runner.invoke(
        app,
        [
            "export-template", "--id", "does-not-exist", "--templates-dir", str(tmp_path / "templates"),
            "--output", str(tmp_path / "out.zip"),
        ],
    )
    assert result.exit_code == 1
    assert "not found" in result.output


def test_import_template_cmd_rejects_missing_archive(tmp_path: Path):
    result = runner.invoke(
        app,
        [
            "import-template", "--from", str(tmp_path / "missing.zip"),
            "--templates-dir", str(tmp_path / "templates"),
        ],
    )
    assert result.exit_code == 1
    assert "not found" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_template_commands.py -v -k "export or import"`
Expected: FAIL — commands not registered.

- [ ] **Step 3: Write the implementation**

Add to `src/ai_film/cli.py`'s template-store import block:

```python
from ai_film.template_store import (
    export_template as export_template_service,
    import_template as import_template_service,
    list_templates as list_templates_service,
    save_template as save_template_service,
    show_template as show_template_service,
)
```

Add after `show_template_cmd`:

```python
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
```

Edit `.claude/settings.json` — add two more lines after the `show-template` lines added in Task 4, in both permission blocks:

```json
      "Bash(ai-film show-template *)",
      "Bash(ai-film export-template *)",
      "Bash(ai-film import-template *)",
```

```json
      "Bash(./.venv/bin/ai-film show-template *)",
      "Bash(./.venv/bin/ai-film export-template *)",
      "Bash(./.venv/bin/ai-film import-template *)",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli_template_commands.py -v`
Expected: PASS (8 tests total)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/cli.py .claude/settings.json tests/test_cli_template_commands.py
git commit -m "feat: wire export-template/import-template into the CLI"
```

---

### Task 7: Agent and command changes

**Files:**
- Modify: `.claude/agents/ai-film-reference-analyst.md` (add a step after the human-approval gate)
- Modify: `.claude/agents/ai-film-director.md` (add a template-check note near the existing reference-video-brief check added in Spec 1)
- Modify: `.claude/agents/ai-film-storyboard.md` (add "apply a template" guidance near the existing reference-video-grounding paragraph added in Spec 1)
- Modify: `.claude/commands/ai-film-setup.md` (add an optional template-choice prompt)

No automated test for this task — these are `.md` prompt files this project's test suite does not execute (same as Spec 1's Task 7).

**Interfaces:**
- Consumes: `ai-film save-template`, `ai-film list-templates`, `ai-film show-template` (Tasks 4/6); the `template.json` schema fields (Task 1).

- [ ] **Step 1: Read the current files to find the exact anchor points**

Run:
```bash
grep -n "video_analysis_brief.json" .claude/agents/ai-film-reference-analyst.md .claude/agents/ai-film-director.md .claude/agents/ai-film-storyboard.md
```

This finds the Spec-1-added integration points each edit below attaches to. Anchor on the exact surrounding text you find — the line numbers below are where this plan's author found them; confirm they still match before editing, and if they've drifted, find the equivalent spot by reading the file's current structure.

- [ ] **Step 2: Add a "save as template?" step to `ai-film-reference-analyst.md`**

Find Step 6 ("Write the approved brief") — the step that sets `"approved": true` and states the agent does not create or modify `03_shots/*.json`. Add a new step immediately after it (renumber "When you're done" if it's numbered; if it's an unnumbered closing section, just insert before it):

```markdown
## Step 7: Offer to save as a reusable template

After writing the approved brief, ask once (not a `NEEDS_INPUT` block — this
is a low-stakes yes/no the human can answer inline, same as any other
closing question):

"Would you like to save this analysis as a reusable template for future
projects? It'll capture the camera language and pacing pattern, not the
specific characters or setting, so you can point a different story at the
same 'shot' next time."

If yes:

1. For each scene in the approved brief, write a generalized version:
   `camera` and `subject_motion` describe *behavior and composition*
   only — drop any identity-specific language (no "Superman", no "the man
   in the red cape"; rewrite as "the subject" / "a single figure" if
   needed). `framing` carries over as-is. Map each scene to a
   `shot_patterns` entry: `order` (the scene's index), `pattern_name` (a
   short slug you choose, e.g. `establish`/`reveal_orbit`/`impact_hold`),
   `camera`, `subject_motion`, `framing`, `suggested_duration_seconds`
   (the scene's duration, rounded), `reference_keyframe` (one of the
   scene's keyframe paths, relative to where you're about to write the
   draft file — or `null` if none feels representative).
2. Ask the human for a short template id (lowercase, hyphenated, e.g.
   `hero-orbit`) and a human-readable name.
3. Write the draft to `assets/reference-video/template_draft.json`:
   `{"schema_version": "1.0", "id": "<the id>", "name": "<the name>",
   "created_at": "<current UTC timestamp>", "source_note": "<one line
   about what this was extracted from>", "shot_patterns": [...]}`.
4. Run `ai-film save-template --from assets/reference-video/template_draft.json --id <the id>`.
5. Report the result (success, or the error if validation failed — fix
   the draft and retry once; if it still fails, report the error instead
   of guessing further).

If no, skip straight to your completion report.
```

- [ ] **Step 3: Add template-application guidance to `ai-film-director.md`**

Find the bullet added by Spec 1 (the one checking for `assets/reference-video/video_analysis_brief.json` with `"approved": true`). Add a new bullet immediately after it, in the same step:

```markdown
- Separately, check whether the project's `config.json` has a top-level `"template"` key. If it does, run `ai-film show-template --id <that value>` and read the result once now — its `shot_patterns` are optional grounding for pacing/structure inspiration during this brainstorm, the same way an approved reference-video brief is. If the key is absent, proceed with no template grounding — this is purely additive.
```

- [ ] **Step 4: Add template-application guidance to `ai-film-storyboard.md`**

Find the paragraph added by Spec 1 ("Reference-video grounding, if present."). Add a new paragraph immediately after it:

```markdown
**Template application, if the project names one.** Check `config.json` for a top-level `"template"` key. If present, run `ai-film show-template --id <that value>` once before drafting this scene's shots. Map your own shot breakdown to the template's `shot_patterns` **in order** — this is an example sequence structure, not a fixed shot count every scene must match: if your scene naturally produces the same number of shots as the template has patterns, seed each shot's `camera`/`action` draft from the matching pattern's `camera`/`subject_motion`/`framing`, rewritten to name this project's actual characters and environment in place of the template's generic subject language; if counts differ, use your own judgment to compress or expand (two patterns can seed one longer shot, or one pattern can be split across two shots). **Precedence: an explicit user/story requirement always overrides what the template suggests** — if the story says the character ends up off-center and the template pattern says centered framing, the story wins; never force a template's suggestion over what the scene actually calls for. If no `"template"` key is set, proceed exactly as before.
```

- [ ] **Step 5: Add an optional template-choice prompt to `ai-film-setup.md`**

Read the file's current step structure first (it already has a "Production format" step and a "write the picks" step, added earlier this project). Add a new step between the capability walkthrough and the final "write the picks" step:

```markdown
## Step: Optional template

Ask: "Do you want to seed this project's shots from a saved camera/pacing template? Run `ai-film list-templates` to see what's available — reply with an id, or say no to skip." This is entirely optional and skippable; most projects have no template.

If the user names one, write it into `config.json` as a new top-level key: `"template": "<the id>"`. Validate it exists first with `ai-film show-template --id <the id>` — if that fails, tell the user and ask again rather than writing an id that doesn't resolve to anything.
```

Adjust the exact insertion point and surrounding step numbering to match the file's real current structure — read it before editing rather than assuming the step names above are the file's exact current headings.

- [ ] **Step 6: Commit**

```bash
git add .claude/agents/ai-film-reference-analyst.md .claude/agents/ai-film-director.md .claude/agents/ai-film-storyboard.md .claude/commands/ai-film-setup.md
git commit -m "feat: wire template save/apply into the pipeline agents"
```

---

### Task 8: README documentation (English + Traditional Chinese)

**Files:**
- Modify: `README.md`
- Modify: `README.zh-TW.md`

**Interfaces:** None — documentation only.

- [ ] **Step 1: Add English documentation**

Insert a new paragraph in `README.md` immediately after the `analyze-reference-video`/`/analyze-reference` paragraph added by Spec 1 (search for `analyze-reference-video --source <path>` to find it), before the `**Video model selection is configurable per shot feature**` paragraph:

```markdown
`save-template --from <draft.json> --id <id> [--force]` validates a draft template
against the schema and saves it to `templates/<id>/`, copying any referenced keyframe
images alongside it — `/analyze-reference`'s agent offers to write this draft
automatically after you approve its analysis. `list-templates` and
`show-template --id <id>` enumerate and inspect what's saved. `export-template --id
<id> --output <file.zip>` / `import-template --from <file.zip> [--id <override>]
[--force]` bundle a template as a portable zip — handing someone a template shares a
camera/pacing recipe, not compute; they still need their own `ai-film-studio` install
and `FAL_KEY` to generate anything with it. A project opts into a saved template via
`config.json`'s top-level `"template": "<id>"` key (set during `/ai-film-setup` or by
hand) — the Director and Storyboard agents then use its `shot_patterns` as optional
grounding when drafting shots, never overriding an explicit story requirement.
```

- [ ] **Step 2: Add Traditional Chinese documentation**

Read `README.zh-TW.md`, find its translated equivalent of the `analyze-reference-video` paragraph, and insert a matching paragraph immediately after it, in Traditional Chinese, keeping every CLI command name, flag name, and file path in English exactly as `README.md` does (matching that file's existing convention):

```markdown
`save-template --from <draft.json> --id <id> [--force]` 會驗證一份 draft template
是否符合 schema，並存到 `templates/<id>/`，同時複製任何被引用到的關鍵影格圖片——
`/analyze-reference` 的 agent 在你核准分析結果後，會自動幫你寫出這份 draft。
`list-templates` 與 `show-template --id <id>` 可以列出與查看已存的 template。
`export-template --id <id> --output <file.zip>` ／ `import-template --from <file.zip>
[--id <override>] [--force]` 會把 template 包成可攜式 zip 檔——分享 template 給別人，
分享的是拍攝手法／節奏的配方，不是運算資源；對方仍然需要自己安裝 `ai-film-studio`
並擁有自己的 `FAL_KEY` 才能實際生成任何內容。專案要使用已存的 template，需要在
`config.json` 頂層加上 `"template": "<id>"`（在 `/ai-film-setup` 時設定，或事後手動
加入）——Director 與 Storyboard agent 之後會把它的 `shot_patterns` 當作選填的參考依
據，但絕不會蓋過故事本身的明確要求。
```

- [ ] **Step 3: Commit**

```bash
git add README.md README.zh-TW.md
git commit -m "docs: document the template library commands"
```

---

## Self-Review Notes

- **Spec coverage:** Architecture/File Layout (Tasks 2-3), `template.json` schema (Task 1), `save-template` (Task 2), `list-templates`/`show-template` (Task 3), CLI wiring for all five commands (Tasks 4, 6), "Applying a template" + Precedence (Task 7's storyboard/director edits), Agent changes (Task 7), Sharing/export-import (Tasks 5-6), Testing section's every named scenario (all tasks' test steps). The Asset Staleness Tracking section of the spec is intentionally NOT covered by this plan — it touches a disjoint set of files (`generation_service.py`, shot generation records) with no shared dependency on `template_store.py`, so per this skill's Scope Check guidance it is planned separately (see `docs/superpowers/plans/2026-09-09-asset-staleness.md`).
- **Placeholder scan:** No TBD/TODO; every step has complete code; Task 7 is the one task without executable test steps, and that's called out explicitly with the same justification Spec 1's equivalent task used, not silently skipped.
- **Type consistency:** `save_template`/`list_templates`/`show_template`/`export_template`/`import_template`'s signatures are defined once (Tasks 2, 3, 5) and consumed with identical signatures in the CLI (Tasks 4, 6); `template_id`/`templates_dir` parameter names and order are consistent across every function; every `shot_patterns` field name matches Task 1's schema exactly through every later task that touches it (agent files in Task 7, docs in Task 8).
