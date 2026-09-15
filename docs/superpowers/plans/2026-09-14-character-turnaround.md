# Character Turnaround (Reference Sets) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Character Lock optionally produce a small set of clean, isolated, per-angle reference images (front/three-quarter/side/back) alongside the existing single `reference.png`, and let Storyboard resolve the best-matching angle for a shot instead of always hardcoding the front reference.

**Architecture:** The multi-angle set is a pure filesystem convention under `assets/characters/<name>/turnaround/<angle>/reference.png` — no schema change, no manifest JSON. It reuses the existing candidate loop (`generate-candidates`/`review`/`edit-candidate`/`select-candidate`) via a small, additive extension to `candidate_store.py`'s target parsing, plus a new pure resolver function (exposed as both a Python function and a new CLI command) that Storyboard calls instead of hardcoding a path. `characters[].reference` in `shot.json` never changes type — it stays the one resolved string it always was.

**Tech Stack:** Python 3, Typer CLI, pytest, existing `ai_film` package structure (`src/ai_film/`).

**Spec:** `docs/superpowers/specs/2026-09-14-character-turnaround-design.md`

## Global Constraints

- `characters[].reference` in `shot.json` stays a plain string forever — never a union type, never an object. (Spec: "Why `characters[].reference` never changes shape".)
- No manifest JSON for the turnaround set — "does this angle exist" is always answered by `Path.exists()`.
- The angle/orientation vocabulary is open-ended (a plain string, never a fixed enum in schema or code) but every angle segment must be validated before use in a path join: reject empty, `/`, `\`, and `..`, mirroring `template_store.py`'s `_validate_template_id` pattern exactly.
- Every turnaround angle is conditioned only on the locked primary `assets/characters/<name>/reference.png` — never on another turnaround angle's output. No chain-conditioning, ever.
- Every locked character reference — primary and every turnaround angle — must be prompted as an isolated character-only image on a plain neutral background (no scene/environment/props). This is a go-forward prompting change, not retroactive.
- No schema.py changes are needed anywhere in this plan — `SHOT_SCHEMA`'s `"characters": {"type": "array"}` (schema.py:37) has no per-item enforcement today, so adding an `orientation` key to a character entry requires zero schema changes.
- `select_candidate` and `scope_for_target` need zero code changes — both already generalize correctly to the new 4-part turnaround target shape once `target_dir` is fixed (verified by reading their current implementation; Task 2 includes a regression test proving this rather than just asserting it).

---

## Task 1: New `character_reference` module — validation and the resolver

**Files:**
- Create: `src/ai_film/character_reference.py`
- Test: `tests/test_character_reference.py`

**Interfaces:**
- Produces: `validate_angle_segment(angle: str) -> None` — raises `ValueError` on an empty string or one containing `/`, `\`, or `..`. Consumed by Task 2 (`candidate_store.target_dir`) and internally by `resolve_character_reference`.
- Produces: `resolve_character_reference(project_dir: Path, character_name: str, orientation: str | None) -> str` — returns a project-relative path string. Consumed by Task 4 (new CLI command) and by `ai-film-storyboard.md` (Task 6) indirectly through that CLI command.
- Consumes: `project_relative_path(path_str: str, project_dir: Path) -> str` from `ai_film.services.generation_service` (already exists, `services/generation_service.py:204-217`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_character_reference.py`:

```python
from pathlib import Path

import pytest

from ai_film.character_reference import resolve_character_reference, validate_angle_segment


def test_validate_angle_segment_accepts_plain_name():
    validate_angle_segment("three_quarter")  # must not raise


@pytest.mark.parametrize("bad", ["", "../etc", "a/b", "a\\b", ".."])
def test_validate_angle_segment_rejects_unsafe_values(bad):
    with pytest.raises(ValueError):
        validate_angle_segment(bad)


def test_resolve_character_reference_falls_back_when_orientation_is_none(tmp_path: Path):
    assert resolve_character_reference(tmp_path, "mara", None) == "assets/characters/mara/reference.png"


def test_resolve_character_reference_falls_back_when_angle_not_locked(tmp_path: Path):
    assert resolve_character_reference(tmp_path, "mara", "back") == "assets/characters/mara/reference.png"


def test_resolve_character_reference_returns_turnaround_path_when_locked(tmp_path: Path):
    angle_dir = tmp_path / "assets" / "characters" / "mara" / "turnaround" / "back"
    angle_dir.mkdir(parents=True)
    (angle_dir / "reference.png").write_bytes(b"BACK-REF")

    result = resolve_character_reference(tmp_path, "mara", "back")

    assert result == "assets/characters/mara/turnaround/back/reference.png"


def test_resolve_character_reference_rejects_unsafe_orientation(tmp_path: Path):
    with pytest.raises(ValueError):
        resolve_character_reference(tmp_path, "mara", "../etc")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_character_reference.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_film.character_reference'`

- [ ] **Step 3: Write the implementation**

Create `src/ai_film/character_reference.py`:

```python
"""Filesystem-truth resolver for a character's reference image, across the
optional turnaround (multi-angle) set. See
docs/superpowers/specs/2026-09-14-character-turnaround-design.md."""

from __future__ import annotations

from pathlib import Path

from ai_film.services.generation_service import project_relative_path


def validate_angle_segment(angle: str) -> None:
    if not angle or "/" in angle or "\\" in angle or ".." in angle:
        raise ValueError(f"invalid orientation/angle segment: {angle!r}")


def resolve_character_reference(
    project_dir: Path, character_name: str, orientation: str | None
) -> str:
    if orientation:
        validate_angle_segment(orientation)
        candidate = (
            project_dir
            / "assets"
            / "characters"
            / character_name
            / "turnaround"
            / orientation
            / "reference.png"
        )
        if candidate.exists():
            return project_relative_path(str(candidate), project_dir)
    return f"assets/characters/{character_name}/reference.png"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_character_reference.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/character_reference.py tests/test_character_reference.py
git commit -m "feat: add character_reference module for turnaround angle resolution"
```

---

## Task 2: `candidate_store.py` — accept the turnaround target shape

**Files:**
- Modify: `src/ai_film/candidate_store.py:1-33` (imports and `target_dir`)
- Test: `tests/test_candidate_store.py` (extend)
- Test: `tests/services/test_candidate_service.py` (extend, regression test for `select_candidate`)

**Interfaces:**
- Consumes: `validate_angle_segment` from Task 1's `ai_film.character_reference`.
- Produces: `target_dir(project_dir, target)` now also accepts `character:<name>:turnaround:<angle>` (4 parts, `parts[2] == "turnaround"`), returning `project_dir / "assets" / "characters" / <name> / "turnaround" / <angle>`. `scope_for_target` and `select_candidate` are unchanged and already correct for this shape (see Global Constraints) — this task's tests must prove that, not just implement `target_dir`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_candidate_store.py` (same file already importing `target_dir`, `scope_for_target`, `pytest`, `Path`):

```python
def test_target_dir_character_turnaround(tmp_path: Path):
    assert target_dir(tmp_path, "character:girl:turnaround:side") == (
        tmp_path / "assets" / "characters" / "girl" / "turnaround" / "side"
    )


def test_target_dir_rejects_malformed_turnaround_segment_count(tmp_path: Path):
    with pytest.raises(ValueError):
        target_dir(tmp_path, "character:girl:turnaround")


def test_target_dir_rejects_wrong_literal_third_segment(tmp_path: Path):
    with pytest.raises(ValueError):
        target_dir(tmp_path, "character:girl:notturnaround:side")


@pytest.mark.parametrize("angle", ["", "../etc", "a/b", "a\\b", ".."])
def test_target_dir_rejects_unsafe_turnaround_angle(tmp_path: Path, angle):
    with pytest.raises(ValueError):
        target_dir(tmp_path, f"character:girl:turnaround:{angle}")


def test_scope_for_target_turnaround_is_bibles():
    assert scope_for_target("character:girl:turnaround:side") == "bibles"
```

Add to `tests/services/test_candidate_service.py` (same file already importing `add_candidates`, `select_candidate`, `Path`):

```python
def test_select_candidate_copies_to_turnaround_reference_png(tmp_path: Path):
    target = "character:girl:turnaround:side"
    directory = tmp_path / "assets" / "characters" / "girl" / "turnaround" / "side" / "candidates"
    directory.mkdir(parents=True)
    (directory / "001.png").write_bytes(b"SIDE-CANDIDATE")
    add_candidates(tmp_path, target, [{
        "id": "001", "path": "candidates/001.png", "provider": "mock", "model": "nano-banana",
        "prompt": "a girl, side view", "parent": None, "operation": "generate", "job": None,
        "estimated_cost": None, "created_at": "2026-08-22T00:00:00Z",
    }])

    result = select_candidate(tmp_path, target, "001")

    assert result == {
        "target": target,
        "selected": "001",
        "canonical_path": "assets/characters/girl/turnaround/side/reference.png",
    }
    reference_path = tmp_path / "assets" / "characters" / "girl" / "turnaround" / "side" / "reference.png"
    assert reference_path.read_bytes() == b"SIDE-CANDIDATE"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_candidate_store.py tests/services/test_candidate_service.py -v -k turnaround`
Expected: FAIL — the new `test_target_dir_character_turnaround` and `test_scope_for_target_turnaround_is_bibles` fail with `ValueError: malformed character target`; `test_select_candidate_copies_to_turnaround_reference_png` fails the same way (propagated from `target_dir` inside `select_candidate`). The three "rejects" tests currently pass by accident (any 3+/4-part character target already raises `ValueError`, just with the wrong message) — that's fine, they'll still pass after Step 3 for the right reason.

- [ ] **Step 3: Write the implementation**

Edit `src/ai_film/candidate_store.py`. Add the import:

```python
from ai_film.character_reference import validate_angle_segment
```

Replace the `character` branch of `target_dir`:

```python
def target_dir(project_dir: Path, target: str) -> Path:
    parts = target.split(":")
    kind = parts[0]
    if kind == "character":
        if len(parts) == 2:
            return project_dir / "assets" / "characters" / parts[1]
        if len(parts) == 4 and parts[2] == "turnaround":
            validate_angle_segment(parts[3])
            return project_dir / "assets" / "characters" / parts[1] / "turnaround" / parts[3]
        raise ValueError(f"malformed character target {target!r}")
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
```

(`scope_for_target`, `select_candidate` unchanged — no edits needed there.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_candidate_store.py tests/services/test_candidate_service.py -v`
Expected: PASS (all tests in both files, including every pre-existing one — this change is purely additive)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/candidate_store.py tests/test_candidate_store.py tests/services/test_candidate_service.py
git commit -m "feat: extend candidate_store target_dir for turnaround character targets"
```

---

## Task 3: `cli.py` — auto-attach the primary reference for turnaround targets

**Files:**
- Modify: `src/ai_film/cli.py:1435-1466` (`generate_candidates_cmd`'s reference-gathering and `_run` closure)
- Test: `tests/test_cli_candidate_commands.py` (extend)

**Interfaces:**
- Consumes: Task 2's `target_dir` turnaround support (transitively, via `generate_candidates_service`).
- Produces: `generate-candidates --target character:<name>:turnaround:<angle> ...` now auto-attaches `assets/characters/<name>/reference.png` as `reference_paths`, without the caller passing `--reference` (there is no such flag — this mirrors how `shot:` targets already auto-gather references via `_image_references`). Plain `character:<name>` and `env:<name>` targets are unaffected (still no auto-attached reference).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli_candidate_commands.py`. First add this import near the top of the file if not already present:

```python
import ai_film.cli as cli_module
```

Then add the tests:

```python
def test_generate_candidates_turnaround_auto_attaches_primary_reference(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    reference_dir = project_dir / "assets" / "characters" / "girl"
    reference_dir.mkdir(parents=True)
    (reference_dir / "reference.png").write_bytes(b"PRIMARY-REF")
    _approve_bibles(project_dir, "character:girl:turnaround:side")

    captured = {}
    original = cli_module.generate_candidates_service

    def _spy(*args, **kwargs):
        captured["reference_paths"] = kwargs.get("reference_paths")
        return original(*args, **kwargs)

    monkeypatch.setattr(cli_module, "generate_candidates_service", _spy)

    result = runner.invoke(
        app,
        ["generate-candidates", "--target", "character:girl:turnaround:side", "--count", "2",
         "--prompt", "a girl, side view, isolated character reference", "--path", str(project_dir)],
    )

    assert result.exit_code == 0, result.output
    assert captured["reference_paths"] == [str(reference_dir / "reference.png")]
    for i in range(1, 3):
        assert (reference_dir / "turnaround" / "side" / "candidates" / f"{i:03d}.png").exists()


def test_generate_candidates_plain_character_target_has_no_auto_reference(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    _approve_bibles(project_dir, "character:girl")
    captured = {}
    original = cli_module.generate_candidates_service

    def _spy(*args, **kwargs):
        captured["reference_paths"] = kwargs.get("reference_paths")
        return original(*args, **kwargs)

    monkeypatch.setattr(cli_module, "generate_candidates_service", _spy)

    result = runner.invoke(
        app,
        ["generate-candidates", "--target", "character:girl", "--count", "1",
         "--prompt", "a girl", "--path", str(project_dir)],
    )

    assert result.exit_code == 0, result.output
    assert captured["reference_paths"] is None


def test_generate_candidates_turnaround_always_references_primary_not_other_angles(
    tmp_path: Path, monkeypatch
):
    project_dir = _init_mock_project(tmp_path)
    reference_dir = project_dir / "assets" / "characters" / "girl"
    reference_dir.mkdir(parents=True)
    (reference_dir / "reference.png").write_bytes(b"PRIMARY-REF")

    captured_reference_paths = []
    original = cli_module.generate_candidates_service

    def _spy(*args, **kwargs):
        captured_reference_paths.append(kwargs.get("reference_paths"))
        return original(*args, **kwargs)

    monkeypatch.setattr(cli_module, "generate_candidates_service", _spy)

    for angle in ("three_quarter", "side", "back"):
        target = f"character:girl:turnaround:{angle}"
        _approve_bibles(project_dir, target)
        result = runner.invoke(
            app,
            ["generate-candidates", "--target", target, "--count", "1",
             "--prompt", f"a girl, {angle} view, isolated character reference",
             "--path", str(project_dir)],
        )
        assert result.exit_code == 0, result.output

        candidate_set = json.loads(
            (project_dir / "assets" / "characters" / "girl" / "turnaround" / angle / "candidates.json").read_text()
        )
        candidate_id = candidate_set["candidates"][0]["id"]
        select_result = runner.invoke(
            app, ["select-candidate", "--target", target, "--id", candidate_id, "--path", str(project_dir)],
        )
        assert select_result.exit_code == 0, select_result.output

    expected = [str(reference_dir / "reference.png")]
    assert captured_reference_paths == [expected, expected, expected]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_candidate_commands.py -v -k turnaround`
Expected: FAIL — `test_generate_candidates_turnaround_auto_attaches_primary_reference` fails its `captured["reference_paths"] == [...]` assertion (`captured["reference_paths"]` is `None`, since nothing attaches a reference for a turnaround target yet — Task 2 already lets the target resolve to the right directory, but `generate-candidates` itself doesn't know to gather a reference for it until this task's change). `test_generate_candidates_turnaround_always_references_primary_not_other_angles` fails the same way (`captured_reference_paths` is `[None, None, None]` instead of 3 copies of the primary path). `test_generate_candidates_plain_character_target_has_no_auto_reference` already passes before this task's change (plain `character:` targets never gathered a reference and still won't) — that's expected; it's a regression guard, not a red test.

- [ ] **Step 3: Write the implementation**

Edit `src/ai_film/cli.py`'s `generate_candidates_cmd`, replacing the reference-gathering block:

```python
    references: list[str] = []
    shot_data: dict | None = None
    spatial = None
    variant_mode = False
    if target.startswith("shot:"):
        shot_id = target.split(":")[1]
        shot_data = load_shot(path / "03_shots" / f"{shot_id}.json")
        references = _image_references(path, shot_id, shot_data)
        variant_mode = prompt is None
        if variant_mode:
            spatial = _effective_spatial(path, shot_id, shot_data)
    else:
        target_parts = target.split(":")
        if target_parts[0] == "character" and len(target_parts) == 4 and target_parts[2] == "turnaround":
            references = [str(path / "assets" / "characters" / target_parts[1] / "reference.png")]
```

And in the `_run()` closure, change the `kwargs` line:

```python
        kwargs = {"reference_paths": references} if (target.startswith("shot:") or references) else {}
```

(Everything else in `generate_candidates_cmd` is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli_candidate_commands.py -v`
Expected: PASS (all tests in the file, including every pre-existing one)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/cli.py tests/test_cli_candidate_commands.py
git commit -m "feat: auto-attach primary reference for turnaround candidate generation"
```

---

## Task 4: `cli.py` — new `resolve-character-reference` command

**Files:**
- Modify: `src/ai_film/cli.py` (add one import, one new `@app.command`)
- Test: Create `tests/test_cli_character_reference_commands.py`

**Interfaces:**
- Consumes: `resolve_character_reference` from Task 1's `ai_film.character_reference`.
- Produces: `ai-film resolve-character-reference --name <name> [--orientation <angle>] --path <project_dir>` — prints the resolved path (plain text, one line, no JSON envelope — matching this file's established convention for status output) to stdout and exits 0, or prints the `ValueError` message to stderr and exits 1 on an unsafe orientation. Consumed by `ai-film-storyboard.md` (Task 6).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_character_reference_commands.py`:

```python
from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app

runner = CliRunner()


def test_resolve_character_reference_cmd_falls_back_to_primary(tmp_path: Path):
    result = runner.invoke(
        app, ["resolve-character-reference", "--name", "mara", "--path", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "assets/characters/mara/reference.png"


def test_resolve_character_reference_cmd_returns_locked_turnaround_angle(tmp_path: Path):
    angle_dir = tmp_path / "assets" / "characters" / "mara" / "turnaround" / "back"
    angle_dir.mkdir(parents=True)
    (angle_dir / "reference.png").write_bytes(b"BACK-REF")

    result = runner.invoke(
        app, ["resolve-character-reference", "--name", "mara", "--orientation", "back",
              "--path", str(tmp_path)],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "assets/characters/mara/turnaround/back/reference.png"


def test_resolve_character_reference_cmd_falls_back_when_angle_not_locked(tmp_path: Path):
    result = runner.invoke(
        app, ["resolve-character-reference", "--name", "mara", "--orientation", "back",
              "--path", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "assets/characters/mara/reference.png"


def test_resolve_character_reference_cmd_rejects_unsafe_orientation(tmp_path: Path):
    result = runner.invoke(
        app, ["resolve-character-reference", "--name", "mara", "--orientation", "../etc",
              "--path", str(tmp_path)],
    )
    assert result.exit_code == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_character_reference_commands.py -v`
Expected: FAIL — Typer/Click reports "No such command 'resolve-character-reference'" (exit code 2) for all four tests.

- [ ] **Step 3: Write the implementation**

Add this import to `src/ai_film/cli.py`'s import block (near the other `ai_film.*` imports):

```python
from ai_film.character_reference import resolve_character_reference as resolve_character_reference_service
```

Add this new command anywhere among the other candidate/gallery commands in `cli.py`:

```python
@app.command(name="resolve-character-reference")
def resolve_character_reference_cmd(
    name: str = typer.Option(..., "--name"),
    orientation: str = typer.Option(None, "--orientation"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Print the characters[].reference path to use for this character, given
    an optional orientation judgment. Falls back to the primary reference.png
    when orientation is omitted or that angle isn't locked yet."""
    try:
        resolved = resolve_character_reference_service(path, name, orientation)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(resolved)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli_character_reference_commands.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/cli.py tests/test_cli_character_reference_commands.py
git commit -m "feat: add resolve-character-reference CLI command"
```

---

## Task 5: `ai-film-character.md` — isolation prompt and the turnaround offer

**Files:**
- Modify: `.claude/agents/ai-film-character.md`

**Interfaces:**
- Consumes: `character:<name>:turnaround:<angle>` targets (Tasks 2-3) via the same `generate-candidates`/`review`/`edit-candidate`/`select-candidate` commands Step 4-6 already use for the primary reference — no new CLI surface for this agent beyond what Tasks 3-4 already ship.
- Produces: (for a human reading the finished doc) a locked `assets/characters/<name>/turnaround/<angle>/reference.png` per completed angle, and a completion report distinguishing "Character Reference Set locked" from plain "character locked."

This task has no pytest cycle (it edits an instruction document, not code) — verify with the grep commands in Step 2 instead.

- [ ] **Step 1: Edit Step 4's prompt-building instruction**

In `.claude/agents/ai-film-character.md`, find this sentence (currently the first sentence of "## Step 4: Generate and review candidates"):

```
Build an image prompt from the appearance section you just wrote (style + build + clothing + distinguishing features, comma-separated, matching the cinematic tone from `story.md`).
```

Replace it with:

```
Build an image prompt from the appearance section you just wrote (style + build + clothing + distinguishing features, comma-separated, matching the cinematic tone from `story.md`), and always append this isolation requirement so the locked reference has no scene content to fight with later: "isolated character reference, plain uniform neutral background, no environment, no scenery, no architectural elements, no props, no narrative setting, no cinematic background effects, full body, consistent studio-style lighting".
```

- [ ] **Step 2: Verify the isolation edit**

Run: `grep -n "isolated character reference, plain uniform neutral background" .claude/agents/ai-film-character.md`
Expected: one match, inside "## Step 4".

- [ ] **Step 3: Insert the new Step 7, after Step 6 and before "When you're done"**

In `.claude/agents/ai-film-character.md`, find the boundary between "## Step 6: Lock it in" (ending with "...just another `type: selection` round trip.") and "## When you're done". Insert this new section between them:

```markdown
## Step 7: Offer a turnaround set (optional, skippable)

Once `reference.png` is locked, emit a `NEEDS_INPUT` with `type: confirmation`, `id: turnaround_offer`, asking: "Generate a turnaround set (three-quarter, side, back) for continuity across angles? This runs the candidate generation workflow for each of these three angles — you can stop after any of them." Stop your turn there.

- If the `HUMAN_RESPONSE` answer is no (or anything other than a clear yes): skip to "When you're done" and report the character as locked with no turnaround set — this is a complete, normal outcome, not a partial one.
- If yes: estimate cost for all three angles at once (same per-candidate default count and cost table as Step 3, `3 × <N>` candidates total across the three angles) and emit a fresh `NEEDS_INPUT` (`type: cost_approval`, `id: cost_approval_turnaround`). Stop your turn there.

Only once resumed with a matching `HUMAN_RESPONSE`:

- If `approved: true`, run:

```bash
ai-film approve-generation --scope bibles --targets character:CHARACTER_NAME:turnaround:three_quarter,character:CHARACTER_NAME:turnaround:side,character:CHARACTER_NAME:turnaround:back
```

  This is a separate target-string set from Step 3's `character:CHARACTER_NAME` approval — approving the primary reference does not also approve these three turnaround targets, and vice versa.

- If `approved: false`, read the `message` (if any), revise (fewer angles, a different count), and emit a *new* `cost_approval` `NEEDS_INPUT` with a fresh `id` — never reuse `cost_approval_turnaround`.

For each of the three angles, in order (`three_quarter`, then `side`, then `back`):

1. Build the angle's prompt from the same appearance section as Step 4, plus the same isolation requirement, plus this angle's own view (e.g. "three-quarter view", "side profile view", "back view, facing away from camera") plus an explicit consistency instruction: "preserve the exact outfit, hairstyle, and proportions from the reference image — do not redesign." This reduces avoidable visual drift between angles; it is not a guarantee the model won't drift a detail, which is exactly why you still review each candidate below rather than auto-selecting one.
2. Run:

```bash
ai-film generate-candidates --target character:CHARACTER_NAME:turnaround:<angle> --count <N> --prompt "<prompt>"
```

   This always conditions on the already-locked primary `reference.png` automatically (never on another angle's output, even one you already locked earlier in this same loop) — you don't need to pass a reference path yourself. Handle a generation failure exactly like Step 4's: a `type: confirmation` `NEEDS_INPUT` showing the exact error and the retry/adjust/stop choices.
3. Run `ai-film review --target character:CHARACTER_NAME:turnaround:<angle>` and Read each candidate PNG directly, same as Step 4.
4. Discuss/refine using the same `type: clarification`/`type: selection` round trips as Step 5 (`edit-candidate --target character:CHARACTER_NAME:turnaround:<angle> ...`), then lock the chosen candidate:

```bash
ai-film select-candidate --target character:CHARACTER_NAME:turnaround:<angle> --id <candidate-id>
```

5. Before moving to the next angle, emit one more `type: confirmation` `NEEDS_INPUT` (`id: turnaround_next_<angle>`) asking whether to continue to the next angle or stop here — a human is allowed to lock `three_quarter` and `side` and decide `back` isn't worth it for this character; that is a complete, normal outcome, not a failure, and the resolver already falls back to the primary reference for any angle never locked.
```

- [ ] **Step 4: Update "When you're done"**

Find:

```
Once `reference.png` is locked, your final message is a genuine completion, not a `NEEDS_INPUT` — report: the bible path, the final candidate id selected, and the `reference.png` path. If you stopped early (Step 1 re-entry, or the user asked to pause), say exactly what state you left things in so a re-dispatch of this same agent picks up correctly.
```

Replace with:

```
Once `reference.png` is locked and Step 7's turnaround offer has been resolved (declined, or one or more angles completed/skipped), your final message is a genuine completion, not a `NEEDS_INPUT` — report: the bible path, the final candidate id selected for the primary reference, the `reference.png` path, and — if any turnaround angles were completed — which ones ("Character Reference Set locked: front + three_quarter + side") versus "character locked" alone when none were. If you stopped early (Step 1 re-entry, or the user asked to pause at any point including mid-turnaround), say exactly what state you left things in so a re-dispatch of this same agent picks up correctly.
```

- [ ] **Step 5: Verify the new step and completion text**

Run: `grep -n "## Step 7: Offer a turnaround set" .claude/agents/ai-film-character.md`
Expected: one match.

Run: `grep -n "Character Reference Set locked" .claude/agents/ai-film-character.md`
Expected: one match, inside the "When you're done" section.

- [ ] **Step 6: Commit**

```bash
git add .claude/agents/ai-film-character.md
git commit -m "docs: add turnaround offer step and isolation prompt to ai-film-character"
```

---

## Task 6: `ai-film-storyboard.md` — orientation judgment and resolver call

**Files:**
- Modify: `.claude/agents/ai-film-storyboard.md:74-75` (shot JSON template's `characters` entry) and the paragraph immediately following the template (currently ending "...never invent a name.")

**Interfaces:**
- Consumes: `ai-film resolve-character-reference --name <name> [--orientation <angle>] --path <project_dir>` (Task 4).

This task has no pytest cycle — verify with the grep commands in Step 2.

- [ ] **Step 1: Edit the shot JSON template and its instructions**

In `.claude/agents/ai-film-storyboard.md`, find this line inside the shot JSON template (currently the sole entry under `"characters"`):

```
    {"name": "<character name>", "reference": "assets/characters/<character name>/reference.png"}
```

Replace it with:

```
    {"name": "<character name>", "reference": "<resolved via ai-film resolve-character-reference — see below>", "orientation": "<optional — omit this key entirely unless this shot needs a specific angle>"}
```

Then find the existing paragraph that begins "`id` must match the filename stem exactly." and ends "...never invent a name." Immediately after that paragraph (still before the "After writing a scene's shot files, run `ai-film validate`..." line), insert this new paragraph:

```markdown
For each character in a shot's `characters` list, decide `orientation` before writing `reference`: it identifies which character-facing view should be used as the visual identity reference for this shot — it is not `camera` (shot framing) and not the camera's position relative to the character. A shot written as the character walking away from camera implies `orientation: "back"` regardless of whether `camera.shot` is wide or close; an over-the-shoulder shot implies `orientation: "three_quarter"` or `"side"` depending on blocking. When in doubt, or when the shot doesn't call for anything unusual, omit `orientation` entirely from that character's entry — most shots need no explicit angle. Whether or not you set `orientation`, resolve `reference` by running:

```bash
ai-film resolve-character-reference --name "<character name>" --orientation "<orientation, or omit this flag entirely if you didn't set one>"
```

and use its stdout (one project-relative path, e.g. `assets/characters/Mara Voss/turnaround/back/reference.png`, or the fallback `assets/characters/Mara Voss/reference.png`) as that character's `reference` value verbatim — never hand-construct this path yourself. This resolves to the locked back/side/three-quarter angle when one exists and was requested, and transparently falls back to the primary reference otherwise, so it is always safe to call even for a character with no turnaround set at all.
```

- [ ] **Step 2: Verify the edits**

Run: `grep -n "resolve-character-reference" .claude/agents/ai-film-storyboard.md`
Expected: at least two matches (the paragraph's prose mention and the `bash` code block).

Run: `grep -n '"orientation"' .claude/agents/ai-film-storyboard.md`
Expected: at least one match, inside the shot JSON template.

- [ ] **Step 3: Commit**

```bash
git add .claude/agents/ai-film-storyboard.md
git commit -m "docs: resolve character reference via new CLI command in ai-film-storyboard"
```

---

## Task 7: End-to-end integration test

**Files:**
- Create: `tests/test_character_turnaround_e2e.py`

**Interfaces:**
- Consumes: `generate-candidates`, `select-candidate`, `approve-generation`, `resolve-character-reference` CLI commands (Tasks 2-4), exercised together through Typer's `CliRunner` against the `mock` provider — proving the full chain works end-to-end, not just each piece in isolation.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_character_turnaround_e2e.py`:

```python
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


def test_full_turnaround_flow_resolves_to_locked_back_angle(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)

    reference_dir = project_dir / "assets" / "characters" / "mara"
    reference_dir.mkdir(parents=True)
    (reference_dir / "reference.png").write_bytes(b"PRIMARY-REF")

    target = "character:mara:turnaround:back"
    approve = runner.invoke(
        app, ["approve-generation", "--scope", "bibles", "--targets", target, "--path", str(project_dir)],
    )
    assert approve.exit_code == 0, approve.output

    generate = runner.invoke(
        app, ["generate-candidates", "--target", target, "--count", "1",
              "--prompt", "mara, back view, isolated character reference",
              "--path", str(project_dir)],
    )
    assert generate.exit_code == 0, generate.output

    candidate_set = json.loads(
        (project_dir / "assets" / "characters" / "mara" / "turnaround" / "back" / "candidates.json").read_text()
    )
    candidate_id = candidate_set["candidates"][0]["id"]

    select = runner.invoke(
        app, ["select-candidate", "--target", target, "--id", candidate_id, "--path", str(project_dir)],
    )
    assert select.exit_code == 0, select.output

    resolve_with_orientation = runner.invoke(
        app, ["resolve-character-reference", "--name", "mara", "--orientation", "back",
              "--path", str(project_dir)],
    )
    assert resolve_with_orientation.exit_code == 0, resolve_with_orientation.output
    assert resolve_with_orientation.stdout.strip() == "assets/characters/mara/turnaround/back/reference.png"

    resolve_without_orientation = runner.invoke(
        app, ["resolve-character-reference", "--name", "mara", "--path", str(project_dir)],
    )
    assert resolve_without_orientation.exit_code == 0, resolve_without_orientation.output
    assert resolve_without_orientation.stdout.strip() == "assets/characters/mara/reference.png"


def test_full_turnaround_flow_falls_back_before_any_angle_is_locked(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    reference_dir = project_dir / "assets" / "characters" / "mara"
    reference_dir.mkdir(parents=True)
    (reference_dir / "reference.png").write_bytes(b"PRIMARY-REF")

    result = runner.invoke(
        app, ["resolve-character-reference", "--name", "mara", "--orientation", "side",
              "--path", str(project_dir)],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "assets/characters/mara/reference.png"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_character_turnaround_e2e.py -v`
Expected: FAIL before Tasks 1-4 are all merged into the tree this test file runs against — if run after Tasks 1-4 are already committed (the normal execution order for this plan), these should already PASS on first run; if so, skip straight to Step 3 and confirm rather than forcing an artificial failure.

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest tests/test_character_turnaround_e2e.py -v`
Expected: PASS (2 tests)

- [ ] **Step 4: Run the full test suite**

Run: `pytest`
Expected: PASS, 0 failures — this feature is entirely additive, so every pre-existing test must still pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add tests/test_character_turnaround_e2e.py
git commit -m "test: add end-to-end coverage for the character turnaround flow"
```
