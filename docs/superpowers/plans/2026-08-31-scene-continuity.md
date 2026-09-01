# Scene Continuity (Spatial Canon) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix character blocking (screen-left/right, facing) drifting between shots in the same scene by giving each scene a persisted spatial canon that generation reads from and the Continuity check validates against.

**Architecture:** A new lightweight, unvalidated-by-jsonschema JSON store (`src/ai_film/scene_continuity.py`, mirroring `feedback_store.py`) holds each scene's canonical `spatial` state, an explicit `transitions` list (the only legitimate way that state changes), and a `master_reference_image` — a one-time, frozen snapshot of the scene's first locked shot, never live-tracked. Four new no-spend CLI commands manage it. Generation (`cli.py`'s reference-building path and `build_image_prompt`) reads the canon; `.claude/agents/ai-film-storyboard.md` is taught to write it.

**Tech Stack:** Python 3, Typer CLI, pytest, plain JSON (no jsonschema for this store).

**Spec:** `docs/superpowers/specs/2026-08-31-scene-continuity-design.md`

## Global Constraints

- No jsonschema for the continuity file — inline validation against constant tuples only, matching `feedback_store.py`'s weight class, not `shot.json`'s.
- The master reference is a **frozen snapshot at lock time, never live-resolved**. A later regeneration of the master shot's own image must never change `master_reference_image`. This is the single most important invariant in the whole design — Task 1 includes a test that fails if this is "simplified" back to live resolution.
- `after_shot < shot_id` is **strict** inequality on scene-relative shot number (parsed the same way `shot_store.py`'s `previous_shot_id` does), never `<=`, never lexical string comparison.
- `--force` on `set-scene-continuity` and `lock-continuity-master` changes **only** the stored continuity file — never touches, regenerates, or re-validates any `shot.json`.
- No enforced maximum on the number of characters in `spatial` — do not hard-code or assume exactly 2.
- `build_video_prompt` in `prompts.py` is explicitly **out of scope** — do not modify it. (It calls `build_image_prompt(shot)` with no `spatial` argument, which is fine since `spatial` defaults to `None`.)
- Every existing test must keep passing throughout. Run `./.venv/bin/pytest -q` before Task 1 and after every task; the repo is at **269 passing** before this plan starts.
- Never touch, read-then-write, or `git add`/commit anything under `one-more-life/` or `.env` — these are gitignored, and `one-more-life/` is a live project directory shared with the user's friend.
- Follow existing code style exactly: `from __future__ import annotations` at the top of every new module, no comments except where a hidden constraint/invariant needs explaining (this codebase's actual convention, visible throughout `feedback_store.py`/`audio_fix.py`/`video_fix.py`), `typer.Option(..., "--flag-name")` for required options, `ValueError`/`RuntimeError` caught in CLI commands and turned into `typer.Exit(code=1)` after `typer.echo(str(exc), err=True)`.

---

## Task 1: `scene_continuity.py` — data model, validation, and the effective-state algorithm

**Files:**
- Create: `src/ai_film/scene_continuity.py`
- Test: `tests/test_scene_continuity.py`

**Interfaces:**
- Consumes: `ai_film.shot_store.load_shot` (for `lock_continuity_master`'s internal shot lookup).
- Produces (used by Task 2 and Task 3):
  - `VALID_SCREEN_SIDES: tuple[str, ...]` = `("left", "center", "right")`
  - `VALID_FACINGS: tuple[str, ...]` = `("left", "right", "camera", "away")`
  - `continuity_path(project_dir: Path, scene_id: str) -> Path`
  - `load_continuity(project_dir: Path, scene_id: str) -> dict`
  - `save_continuity(project_dir: Path, scene_id: str, data: dict) -> None`
  - `scene_id_for_shot(shot_id: str) -> str | None`
  - `set_scene_continuity(project_dir: Path, scene_id: str, character: str, screen_side: str, facing: str, master_shot: str | None = None, force: bool = False) -> dict`
  - `add_continuity_transition(project_dir: Path, scene_id: str, after_shot: str, character: str, screen_side: str, facing: str, reason: str) -> dict`
  - `lock_continuity_master(project_dir: Path, scene_id: str, force: bool = False) -> dict`
  - `effective_spatial_state(continuity: dict, shot_id: str) -> dict[str, dict]`

### Step 1: Write the failing tests

Create `tests/test_scene_continuity.py`:

```python
# tests/test_scene_continuity.py
from pathlib import Path

import pytest

from ai_film.scene_continuity import (
    add_continuity_transition,
    continuity_path,
    effective_spatial_state,
    load_continuity,
    lock_continuity_master,
    save_continuity,
    scene_id_for_shot,
    set_scene_continuity,
)
from ai_film.shot_store import save_shot


def test_scene_id_for_shot_extracts_scene_prefix():
    assert scene_id_for_shot("S01_SH03") == "S01"
    assert scene_id_for_shot("S12_SH01") == "S12"


def test_scene_id_for_shot_returns_none_for_non_matching_id():
    assert scene_id_for_shot("not-a-shot-id") is None


def test_load_continuity_returns_default_shape_when_absent(tmp_path: Path):
    data = load_continuity(tmp_path, "S01")
    assert data == {
        "scene_id": "S01", "master_shot": None,
        "master_reference_image": None, "spatial": {}, "transitions": [],
    }


def test_save_continuity_round_trips(tmp_path: Path):
    data = load_continuity(tmp_path, "S01")
    data["spatial"]["Mara Voss"] = {"screen_side": "left", "facing": "right"}
    save_continuity(tmp_path, "S01", data)
    assert load_continuity(tmp_path, "S01")["spatial"]["Mara Voss"] == {
        "screen_side": "left", "facing": "right",
    }
    assert continuity_path(tmp_path, "S01").exists()


def test_set_scene_continuity_rejects_unknown_screen_side(tmp_path: Path):
    with pytest.raises(ValueError):
        set_scene_continuity(tmp_path, "S01", "Mara Voss", "sideways", "camera")


def test_set_scene_continuity_rejects_unknown_facing(tmp_path: Path):
    with pytest.raises(ValueError):
        set_scene_continuity(tmp_path, "S01", "Mara Voss", "left", "sideways")


def test_set_scene_continuity_upserts_new_character(tmp_path: Path):
    data = set_scene_continuity(tmp_path, "S01", "Mara Voss", "left", "right")
    assert data["spatial"]["Mara Voss"] == {"screen_side": "left", "facing": "right"}


def test_set_scene_continuity_same_values_twice_is_a_harmless_noop(tmp_path: Path):
    set_scene_continuity(tmp_path, "S01", "Mara Voss", "left", "right")
    data = set_scene_continuity(tmp_path, "S01", "Mara Voss", "left", "right")
    assert data["spatial"]["Mara Voss"] == {"screen_side": "left", "facing": "right"}


def test_set_scene_continuity_rejects_conflicting_change_without_force(tmp_path: Path):
    set_scene_continuity(tmp_path, "S01", "Mara Voss", "left", "right")
    with pytest.raises(ValueError):
        set_scene_continuity(tmp_path, "S01", "Mara Voss", "right", "left")


def test_set_scene_continuity_force_overwrites_conflicting_change(tmp_path: Path):
    set_scene_continuity(tmp_path, "S01", "Mara Voss", "left", "right")
    data = set_scene_continuity(tmp_path, "S01", "Mara Voss", "right", "left", force=True)
    assert data["spatial"]["Mara Voss"] == {"screen_side": "right", "facing": "left"}


def test_set_scene_continuity_no_enforced_character_maximum(tmp_path: Path):
    set_scene_continuity(tmp_path, "S01", "A", "left", "right")
    set_scene_continuity(tmp_path, "S01", "B", "center", "camera")
    data = set_scene_continuity(tmp_path, "S01", "C", "right", "away")
    assert set(data["spatial"]) == {"A", "B", "C"}


def test_set_scene_continuity_master_shot_rejects_wrong_scene(tmp_path: Path):
    with pytest.raises(ValueError):
        set_scene_continuity(
            tmp_path, "S01", "Mara Voss", "left", "right", master_shot="S02_SH01",
        )


def test_set_scene_continuity_sets_master_shot(tmp_path: Path):
    data = set_scene_continuity(
        tmp_path, "S01", "Mara Voss", "left", "right", master_shot="S01_SH01",
    )
    assert data["master_shot"] == "S01_SH01"


def test_set_scene_continuity_rejects_conflicting_master_shot_without_force(tmp_path: Path):
    set_scene_continuity(tmp_path, "S01", "Mara Voss", "left", "right", master_shot="S01_SH01")
    with pytest.raises(ValueError):
        set_scene_continuity(
            tmp_path, "S01", "Mara Voss", "left", "right", master_shot="S01_SH02",
        )


def test_add_continuity_transition_creates_entry(tmp_path: Path):
    data = add_continuity_transition(
        tmp_path, "S01", "S01_SH03", "Mara Voss", "right", "left", "walks around the doctor",
    )
    assert data["transitions"] == [
        {
            "after_shot": "S01_SH03",
            "changes": {"Mara Voss": {"screen_side": "right", "facing": "left"}},
            "reason": "walks around the doctor",
        }
    ]


def test_add_continuity_transition_merges_same_after_shot(tmp_path: Path):
    add_continuity_transition(
        tmp_path, "S01", "S01_SH03", "Mara Voss", "right", "left", "walks around the doctor",
    )
    data = add_continuity_transition(
        tmp_path, "S01", "S01_SH03", "Doctor", "left", "right", "steps aside",
    )
    assert len(data["transitions"]) == 1
    assert data["transitions"][0]["changes"] == {
        "Mara Voss": {"screen_side": "right", "facing": "left"},
        "Doctor": {"screen_side": "left", "facing": "right"},
    }
    assert data["transitions"][0]["reason"] == "steps aside"


def test_add_continuity_transition_rejects_wrong_scene(tmp_path: Path):
    with pytest.raises(ValueError):
        add_continuity_transition(
            tmp_path, "S01", "S02_SH01", "Mara Voss", "right", "left", "wrong scene",
        )


def test_add_continuity_transition_rejects_unknown_values(tmp_path: Path):
    with pytest.raises(ValueError):
        add_continuity_transition(
            tmp_path, "S01", "S01_SH03", "Mara Voss", "sideways", "left", "bad value",
        )


def test_effective_spatial_state_with_no_transitions_returns_spatial_unchanged():
    continuity = {
        "spatial": {"A": {"screen_side": "left", "facing": "right"}},
        "transitions": [],
    }
    assert effective_spatial_state(continuity, "S01_SH05") == {
        "A": {"screen_side": "left", "facing": "right"}
    }


def test_effective_spatial_state_transition_boundary_is_strict():
    """The spec's own worked example, made literal: a transition declared
    after_shot=S01_SH01 is NOT yet true at SH01 itself — only starting at
    SH02. This is the off-by-one case the spec's second review round
    exists specifically to prevent regressing."""
    continuity = {
        "spatial": {"A": {"screen_side": "left", "facing": "right"}},
        "transitions": [
            {
                "after_shot": "S01_SH01",
                "changes": {"A": {"screen_side": "center", "facing": "camera"}},
                "reason": "moves to center",
            }
        ],
    }
    assert effective_spatial_state(continuity, "S01_SH01")["A"] == {
        "screen_side": "left", "facing": "right",
    }
    assert effective_spatial_state(continuity, "S01_SH02")["A"] == {
        "screen_side": "center", "facing": "camera",
    }


def test_effective_spatial_state_folds_transitions_in_shot_order_not_list_order():
    continuity = {
        "spatial": {"A": {"screen_side": "left", "facing": "right"}},
        "transitions": [
            {
                "after_shot": "S01_SH05",
                "changes": {"A": {"screen_side": "right", "facing": "left"}},
                "reason": "later transition, listed first",
            },
            {
                "after_shot": "S01_SH02",
                "changes": {"A": {"screen_side": "center", "facing": "camera"}},
                "reason": "earlier transition, listed second",
            },
        ],
    }
    # At SH03: only the SH02 transition has applied (SH05's hasn't yet),
    # even though it's listed second in the file.
    assert effective_spatial_state(continuity, "S01_SH03")["A"] == {
        "screen_side": "center", "facing": "camera",
    }
    # At SH06: both have applied, in shot-number order, so SH05's wins.
    assert effective_spatial_state(continuity, "S01_SH06")["A"] == {
        "screen_side": "right", "facing": "left",
    }


def test_effective_spatial_state_transition_naming_one_character_leaves_other_untouched():
    continuity = {
        "spatial": {
            "A": {"screen_side": "left", "facing": "right"},
            "B": {"screen_side": "right", "facing": "left"},
        },
        "transitions": [
            {
                "after_shot": "S01_SH02",
                "changes": {"A": {"screen_side": "center", "facing": "camera"}},
                "reason": "only A moves",
            }
        ],
    }
    state = effective_spatial_state(continuity, "S01_SH03")
    assert state["A"] == {"screen_side": "center", "facing": "camera"}
    assert state["B"] == {"screen_side": "right", "facing": "left"}


def test_effective_spatial_state_no_character_maximum():
    continuity = {
        "spatial": {
            "A": {"screen_side": "left", "facing": "right"},
            "B": {"screen_side": "center", "facing": "camera"},
            "C": {"screen_side": "right", "facing": "away"},
        },
        "transitions": [
            {
                "after_shot": "S01_SH01",
                "changes": {"C": {"screen_side": "left", "facing": "right"}},
                "reason": "C moves",
            }
        ],
    }
    state = effective_spatial_state(continuity, "S01_SH02")
    assert state["A"] == {"screen_side": "left", "facing": "right"}
    assert state["B"] == {"screen_side": "center", "facing": "camera"}
    assert state["C"] == {"screen_side": "left", "facing": "right"}


def _shot_with_locked_image(shot_id: str, image_path: str) -> dict:
    return {
        "schema_version": "1.0", "id": shot_id, "status": "draft", "duration_seconds": 4,
        "continuity": {"status": "passed", "checked_at": None, "issues": []},
        "generation": {
            "image": {
                "status": "completed", "attempts": 1,
                "artifact": {"path": image_path, "size_bytes": 4, "sha256": None},
            },
            "video": {"status": "not_required"}, "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"}, "music": {"status": "not_required"},
        },
    }


def test_lock_continuity_master_requires_master_shot_set(tmp_path: Path):
    with pytest.raises(ValueError):
        lock_continuity_master(tmp_path, "S01")


def test_lock_continuity_master_requires_locked_image(tmp_path: Path):
    set_scene_continuity(tmp_path, "S01", "A", "left", "right", master_shot="S01_SH01")
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    shot_path.parent.mkdir(parents=True)
    save_shot(shot_path, {
        "schema_version": "1.0", "id": "S01_SH01", "status": "draft", "duration_seconds": 4,
        "continuity": {"status": "pending", "checked_at": None, "issues": []},
        "generation": {
            "image": {"status": "pending", "attempts": 0}, "video": {"status": "not_required"},
            "voice": {"status": "not_required"}, "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    })
    with pytest.raises(ValueError):
        lock_continuity_master(tmp_path, "S01")


def test_lock_continuity_master_snapshots_the_artifact(tmp_path: Path):
    set_scene_continuity(tmp_path, "S01", "A", "left", "right", master_shot="S01_SH01")
    image_path = tmp_path / "04_storyboard" / "S01_SH01.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"ORIGINAL-IMAGE")
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_locked_image("S01_SH01", "04_storyboard/S01_SH01.png"))

    data = lock_continuity_master(tmp_path, "S01")

    assert data["master_reference_image"] == "02_scenes/S01_master_reference.png"
    snapshot_path = tmp_path / data["master_reference_image"]
    assert snapshot_path.read_bytes() == b"ORIGINAL-IMAGE"


def test_lock_continuity_master_rejects_relock_without_force(tmp_path: Path):
    set_scene_continuity(tmp_path, "S01", "A", "left", "right", master_shot="S01_SH01")
    image_path = tmp_path / "04_storyboard" / "S01_SH01.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"ORIGINAL-IMAGE")
    save_shot(
        tmp_path / "03_shots" / "S01_SH01.json",
        _shot_with_locked_image("S01_SH01", "04_storyboard/S01_SH01.png"),
    )
    lock_continuity_master(tmp_path, "S01")

    with pytest.raises(ValueError):
        lock_continuity_master(tmp_path, "S01")


def test_lock_continuity_master_is_frozen_not_live(tmp_path: Path):
    """The single most important invariant in this design: once locked,
    the master reference does NOT follow a later regeneration of the
    master shot's own image. If this test fails, the master reference has
    been "simplified" back to a live pointer — see the spec's
    Master-shot lifecycle section for why that's wrong."""
    set_scene_continuity(tmp_path, "S01", "A", "left", "right", master_shot="S01_SH01")
    image_path = tmp_path / "04_storyboard" / "S01_SH01.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"ORIGINAL-IMAGE")
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_locked_image("S01_SH01", "04_storyboard/S01_SH01.png"))

    data = lock_continuity_master(tmp_path, "S01")
    snapshot_path = tmp_path / data["master_reference_image"]
    assert snapshot_path.read_bytes() == b"ORIGINAL-IMAGE"

    # Simulate a regeneration of the master shot's own image in place.
    image_path.write_bytes(b"REGENERATED-IMAGE")

    # Re-read the continuity file fresh, exactly as a later generation call would.
    refreshed = load_continuity(tmp_path, "S01")
    assert refreshed["master_reference_image"] == "02_scenes/S01_master_reference.png"
    assert snapshot_path.read_bytes() == b"ORIGINAL-IMAGE"


def test_lock_continuity_master_force_relocks(tmp_path: Path):
    set_scene_continuity(tmp_path, "S01", "A", "left", "right", master_shot="S01_SH01")
    image_path = tmp_path / "04_storyboard" / "S01_SH01.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"FIRST-LOCK")
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_locked_image("S01_SH01", "04_storyboard/S01_SH01.png"))
    data = lock_continuity_master(tmp_path, "S01")
    snapshot_path = tmp_path / data["master_reference_image"]

    image_path.write_bytes(b"SECOND-LOCK")
    data = lock_continuity_master(tmp_path, "S01", force=True)

    assert snapshot_path.read_bytes() == b"SECOND-LOCK"
```

### Step 2: Run the tests to verify they fail

Run: `./.venv/bin/pytest tests/test_scene_continuity.py -v`
Expected: every test FAILS with `ModuleNotFoundError: No module named 'ai_film.scene_continuity'` (the module doesn't exist yet).

### Step 3: Implement `src/ai_film/scene_continuity.py`

```python
# src/ai_film/scene_continuity.py
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from ai_film.shot_store import load_shot

VALID_SCREEN_SIDES = ("left", "center", "right")
VALID_FACINGS = ("left", "right", "camera", "away")

_SHOT_ID_RE = re.compile(r"^(S\d+)_SH(\d+)$")


def scene_id_for_shot(shot_id: str) -> str | None:
    match = _SHOT_ID_RE.match(shot_id)
    return match.group(1) if match else None


def _shot_number(shot_id: str) -> int:
    match = _SHOT_ID_RE.match(shot_id)
    if match is None:
        raise ValueError(f"shot id {shot_id!r} doesn't match S<SS>_SH<NN>")
    return int(match.group(2))


def continuity_path(project_dir: Path, scene_id: str) -> Path:
    return project_dir / "02_scenes" / f"{scene_id}.continuity.json"


def load_continuity(project_dir: Path, scene_id: str) -> dict:
    path = continuity_path(project_dir, scene_id)
    if not path.exists():
        return {
            "scene_id": scene_id, "master_shot": None,
            "master_reference_image": None, "spatial": {}, "transitions": [],
        }
    return json.loads(path.read_text())


def save_continuity(project_dir: Path, scene_id: str, data: dict) -> None:
    path = continuity_path(project_dir, scene_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".continuity-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _validate_state(screen_side: str, facing: str) -> None:
    if screen_side not in VALID_SCREEN_SIDES:
        raise ValueError(f"screen_side must be one of {VALID_SCREEN_SIDES}, got {screen_side!r}")
    if facing not in VALID_FACINGS:
        raise ValueError(f"facing must be one of {VALID_FACINGS}, got {facing!r}")


def set_scene_continuity(
    project_dir: Path,
    scene_id: str,
    character: str,
    screen_side: str,
    facing: str,
    master_shot: str | None = None,
    force: bool = False,
) -> dict:
    _validate_state(screen_side, facing)
    if master_shot is not None and scene_id_for_shot(master_shot) != scene_id:
        raise ValueError(f"master_shot {master_shot!r} does not belong to scene {scene_id!r}")

    data = load_continuity(project_dir, scene_id)
    new_state = {"screen_side": screen_side, "facing": facing}
    existing_state = data["spatial"].get(character)
    if existing_state is not None and existing_state != new_state and not force:
        raise ValueError(
            f"{character!r} already has an established state {existing_state} in "
            f"scene {scene_id!r} — pass force=True to deliberately revise it"
        )
    data["spatial"][character] = new_state

    if master_shot is not None:
        existing_master = data.get("master_shot")
        if existing_master is not None and existing_master != master_shot and not force:
            raise ValueError(
                f"scene {scene_id!r} already has master_shot={existing_master!r} "
                f"— pass force=True to deliberately change it"
            )
        data["master_shot"] = master_shot

    save_continuity(project_dir, scene_id, data)
    return data


def add_continuity_transition(
    project_dir: Path,
    scene_id: str,
    after_shot: str,
    character: str,
    screen_side: str,
    facing: str,
    reason: str,
) -> dict:
    _validate_state(screen_side, facing)
    if scene_id_for_shot(after_shot) != scene_id:
        raise ValueError(f"after_shot {after_shot!r} does not belong to scene {scene_id!r}")

    data = load_continuity(project_dir, scene_id)
    new_state = {"screen_side": screen_side, "facing": facing}
    for transition in data["transitions"]:
        if transition["after_shot"] == after_shot:
            transition["changes"][character] = new_state
            transition["reason"] = reason
            save_continuity(project_dir, scene_id, data)
            return data
    data["transitions"].append({
        "after_shot": after_shot,
        "changes": {character: new_state},
        "reason": reason,
    })
    save_continuity(project_dir, scene_id, data)
    return data


def lock_continuity_master(project_dir: Path, scene_id: str, force: bool = False) -> dict:
    data = load_continuity(project_dir, scene_id)
    master_shot = data.get("master_shot")
    if not master_shot:
        raise ValueError(
            f"scene {scene_id!r} has no master_shot set — call set_scene_continuity first"
        )
    if data.get("master_reference_image") and not force:
        raise ValueError(
            f"scene {scene_id!r} already has a locked master reference "
            f"— pass force=True to deliberately re-lock it"
        )

    shot_path = project_dir / "03_shots" / f"{master_shot}.json"
    if not shot_path.exists():
        raise ValueError(f"master shot {master_shot!r} has no shot.json at {shot_path}")
    shot_data = load_shot(shot_path)
    artifact = shot_data.get("generation", {}).get("image", {}).get("artifact")
    if not artifact or not artifact.get("path"):
        raise ValueError(f"master shot {master_shot!r} has no locked image yet")

    source = project_dir / artifact["path"]
    dest = project_dir / "02_scenes" / f"{scene_id}_master_reference.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(source, dest)
    data["master_reference_image"] = str(dest.relative_to(project_dir))
    save_continuity(project_dir, scene_id, data)
    return data


def effective_spatial_state(continuity: dict, shot_id: str) -> dict[str, dict]:
    target_num = _shot_number(shot_id)
    state = {name: dict(values) for name, values in continuity.get("spatial", {}).items()}
    transitions = sorted(
        continuity.get("transitions", []), key=lambda t: _shot_number(t["after_shot"]),
    )
    for transition in transitions:
        if _shot_number(transition["after_shot"]) < target_num:
            for character, new_values in transition["changes"].items():
                state[character] = dict(new_values)
    return state
```

### Step 4: Run the tests to verify they pass

Run: `./.venv/bin/pytest tests/test_scene_continuity.py -v`
Expected: all tests PASS.

### Step 5: Run the full suite

Run: `./.venv/bin/pytest -q`
Expected: 269 + (however many tests `test_scene_continuity.py` adds) passing, 0 failing.

### Step 6: Commit

```bash
git add src/ai_film/scene_continuity.py tests/test_scene_continuity.py
git commit -m "feat: add scene_continuity.py — spatial canon data model"
```

---

## Task 2: Four CLI commands + settings.json permissions

**Files:**
- Modify: `src/ai_film/cli.py` (add import block near line 37-39, add four `@app.command` functions near `check-continuity` at line 369-389)
- Modify: `.claude/settings.json`
- Test: `tests/test_cli_scene_continuity_commands.py`

**Interfaces:**
- Consumes: Task 1's `set_scene_continuity`, `add_continuity_transition`, `lock_continuity_master`, `effective_spatial_state`, `load_continuity`, `scene_id_for_shot` (all from `ai_film.scene_continuity`).
- Produces (used by Task 3): the CLI commands themselves — Task 3 doesn't call these directly, but must not collide with the same names.

### Step 1: Write the failing tests

Create `tests/test_cli_scene_continuity_commands.py`:

```python
# tests/test_cli_scene_continuity_commands.py
from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.scene_continuity import load_continuity
from ai_film.shot_store import save_shot

runner = CliRunner()


def _shot_with_locked_image(shot_id: str, image_path: str) -> dict:
    return {
        "schema_version": "1.0", "id": shot_id, "status": "draft", "duration_seconds": 4,
        "continuity": {"status": "passed", "checked_at": None, "issues": []},
        "generation": {
            "image": {
                "status": "completed", "attempts": 1,
                "artifact": {"path": image_path, "size_bytes": 4, "sha256": None},
            },
            "video": {"status": "not_required"}, "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"}, "music": {"status": "not_required"},
        },
    }


def test_set_scene_continuity_cmd_writes_character_state(tmp_path: Path):
    result = runner.invoke(
        app,
        [
            "set-scene-continuity", "--scene", "S01", "--character", "Mara Voss",
            "--screen-side", "left", "--facing", "right", "--path", str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    data = load_continuity(tmp_path, "S01")
    assert data["spatial"]["Mara Voss"] == {"screen_side": "left", "facing": "right"}


def test_set_scene_continuity_cmd_rejects_bad_screen_side(tmp_path: Path):
    result = runner.invoke(
        app,
        [
            "set-scene-continuity", "--scene", "S01", "--character", "Mara Voss",
            "--screen-side", "sideways", "--facing", "right", "--path", str(tmp_path),
        ],
    )
    assert result.exit_code == 1


def test_set_scene_continuity_cmd_rejects_conflicting_change_without_force(tmp_path: Path):
    runner.invoke(
        app,
        [
            "set-scene-continuity", "--scene", "S01", "--character", "Mara Voss",
            "--screen-side", "left", "--facing", "right", "--path", str(tmp_path),
        ],
    )
    result = runner.invoke(
        app,
        [
            "set-scene-continuity", "--scene", "S01", "--character", "Mara Voss",
            "--screen-side", "right", "--facing", "left", "--path", str(tmp_path),
        ],
    )
    assert result.exit_code == 1


def test_set_scene_continuity_cmd_force_overwrites(tmp_path: Path):
    runner.invoke(
        app,
        [
            "set-scene-continuity", "--scene", "S01", "--character", "Mara Voss",
            "--screen-side", "left", "--facing", "right", "--path", str(tmp_path),
        ],
    )
    result = runner.invoke(
        app,
        [
            "set-scene-continuity", "--scene", "S01", "--character", "Mara Voss",
            "--screen-side", "right", "--facing", "left", "--force", "--path", str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    data = load_continuity(tmp_path, "S01")
    assert data["spatial"]["Mara Voss"] == {"screen_side": "right", "facing": "left"}


def test_add_continuity_transition_cmd_writes_transition(tmp_path: Path):
    result = runner.invoke(
        app,
        [
            "add-continuity-transition", "--scene", "S01", "--after-shot", "S01_SH03",
            "--character", "Mara Voss", "--screen-side", "right", "--facing", "left",
            "--reason", "walks around the doctor", "--path", str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    data = load_continuity(tmp_path, "S01")
    assert data["transitions"][0]["after_shot"] == "S01_SH03"
    assert data["transitions"][0]["changes"]["Mara Voss"] == {
        "screen_side": "right", "facing": "left",
    }


def test_show_continuity_cmd_reports_no_file_when_absent(tmp_path: Path):
    save_shot(
        tmp_path / "03_shots" / "S01_SH01.json",
        _shot_with_locked_image("S01_SH01", "04_storyboard/S01_SH01.png"),
    )
    result = runner.invoke(
        app, ["show-continuity", "--shot", "S01_SH01", "--path", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "no continuity file" in result.output


def test_show_continuity_cmd_prints_effective_state(tmp_path: Path):
    save_shot(
        tmp_path / "03_shots" / "S01_SH01.json",
        _shot_with_locked_image("S01_SH01", "04_storyboard/S01_SH01.png"),
    )
    runner.invoke(
        app,
        [
            "set-scene-continuity", "--scene", "S01", "--character", "Mara Voss",
            "--screen-side", "left", "--facing", "right", "--path", str(tmp_path),
        ],
    )
    result = runner.invoke(
        app, ["show-continuity", "--shot", "S01_SH01", "--path", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "Mara Voss" in result.output
    assert "screen_side=left" in result.output
    assert "facing=right" in result.output


def test_lock_continuity_master_cmd_requires_master_shot_file_to_exist(tmp_path: Path):
    runner.invoke(
        app,
        [
            "set-scene-continuity", "--scene", "S01", "--character", "Mara Voss",
            "--screen-side", "left", "--facing", "right", "--master-shot", "S01_SH01",
            "--path", str(tmp_path),
        ],
    )
    result = runner.invoke(
        app, ["lock-continuity-master", "--scene", "S01", "--path", str(tmp_path)]
    )
    assert result.exit_code == 1


def test_lock_continuity_master_cmd_snapshots_image(tmp_path: Path):
    image_path = tmp_path / "04_storyboard" / "S01_SH01.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"ORIGINAL-IMAGE")
    save_shot(
        tmp_path / "03_shots" / "S01_SH01.json",
        _shot_with_locked_image("S01_SH01", "04_storyboard/S01_SH01.png"),
    )
    runner.invoke(
        app,
        [
            "set-scene-continuity", "--scene", "S01", "--character", "Mara Voss",
            "--screen-side", "left", "--facing", "right", "--master-shot", "S01_SH01",
            "--path", str(tmp_path),
        ],
    )
    result = runner.invoke(
        app, ["lock-continuity-master", "--scene", "S01", "--path", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    data = load_continuity(tmp_path, "S01")
    assert (tmp_path / data["master_reference_image"]).read_bytes() == b"ORIGINAL-IMAGE"
```

### Step 2: Run the tests to verify they fail

Run: `./.venv/bin/pytest tests/test_cli_scene_continuity_commands.py -v`
Expected: FAIL — the CLI commands don't exist yet (typer reports "No such command").

### Step 3: Add the import block to `cli.py`

In `src/ai_film/cli.py`, immediately after the existing line (currently line 39):

```python
from ai_film.video_diagnostics import diagnose_video as diagnose_video_service
```

add:

```python
from ai_film.scene_continuity import (
    add_continuity_transition as add_continuity_transition_service,
    effective_spatial_state,
    load_continuity,
    lock_continuity_master as lock_continuity_master_service,
    scene_id_for_shot,
    set_scene_continuity as set_scene_continuity_service,
)
```

### Step 4: Add the four CLI commands

In `src/ai_film/cli.py`, immediately after the existing `check_continuity_cmd` function (currently ending at line 389, right before `@app.command(name="approve-generation")`), insert:

```python
@app.command(name="set-scene-continuity")
def set_scene_continuity_cmd(
    scene: str = typer.Option(..., "--scene"),
    character: str = typer.Option(..., "--character"),
    screen_side: str = typer.Option(..., "--screen-side", help="left|center|right"),
    facing: str = typer.Option(..., "--facing", help="left|right|camera|away"),
    master_shot: str = typer.Option(None, "--master-shot"),
    force: bool = typer.Option(False, "--force"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Set one character's initial screen_side/facing in a scene's spatial
    canon. Fails on a conflicting re-set unless --force — never silently
    overwrites an established state."""
    try:
        set_scene_continuity_service(
            path, scene, character, screen_side, facing,
            master_shot=master_shot, force=force,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{scene}: {character} set to screen_side={screen_side} facing={facing}")


@app.command(name="add-continuity-transition")
def add_continuity_transition_cmd(
    scene: str = typer.Option(..., "--scene"),
    after_shot: str = typer.Option(..., "--after-shot"),
    character: str = typer.Option(..., "--character"),
    screen_side: str = typer.Option(..., "--screen-side"),
    facing: str = typer.Option(..., "--facing"),
    reason: str = typer.Option(..., "--reason"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Declare (or extend) an explicit blocking change for a scene, taking
    effect starting the shot immediately after --after-shot."""
    try:
        add_continuity_transition_service(
            path, scene, after_shot, character, screen_side, facing, reason,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(
        f"{scene}: transition after {after_shot} — {character} -> "
        f"screen_side={screen_side} facing={facing}"
    )


@app.command(name="lock-continuity-master")
def lock_continuity_master_cmd(
    scene: str = typer.Option(..., "--scene"),
    force: bool = typer.Option(False, "--force"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Freeze the scene's master_shot's current locked image as the scene's
    permanent master reference — a one-time snapshot, never live-tracked."""
    try:
        data = lock_continuity_master_service(path, scene, force=force)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{scene}: master reference locked at {data['master_reference_image']}")


@app.command(name="show-continuity")
def show_continuity_cmd(
    shot: str = typer.Option(..., "--shot"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Print the effective spatial state (screen_side/facing per
    character) for a shot, folding in every transition that applies by
    that shot."""
    scene_id = scene_id_for_shot(shot)
    if scene_id is None:
        typer.echo(f"{shot}: doesn't match the S<SS>_SH<NN> shot-id convention", err=True)
        raise typer.Exit(code=1)
    continuity = load_continuity(path, scene_id)
    if not continuity.get("spatial") and not continuity.get("transitions"):
        typer.echo(f"{shot}: no continuity file for scene {scene_id}")
        return
    state = effective_spatial_state(continuity, shot)
    if not state:
        typer.echo(f"{shot}: continuity file exists but no characters have a recorded state yet")
        return
    for character, values in state.items():
        typer.echo(
            f"{character}: screen_side={values['screen_side']} facing={values['facing']}"
        )
```

### Step 5: Add permission entries to `.claude/settings.json`

In `.claude/settings.json`, the `permissions.allow` array currently ends with (bare form) `"Bash(ai-film resolve-feedback *)"` and (prefixed form) `"Bash(./.venv/bin/ai-film resolve-feedback *)"`. Add four new bare-form entries right after `"Bash(ai-film diagnose-video *)"` and four matching prefixed-form entries right after `"Bash(./.venv/bin/ai-film diagnose-video *)"`, so the full file reads:

```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "permissions": {
    "allow": [
      "Bash(ai-film init *)",
      "Bash(ai-film version)",
      "Bash(ai-film validate *)",
      "Bash(ai-film status *)",
      "Bash(ai-film models *)",
      "Bash(ai-film check-continuity *)",
      "Bash(ai-film review *)",
      "Bash(ai-film select-candidate *)",
      "Bash(ai-film review-media *)",
      "Bash(ai-film diagnose-video *)",
      "Bash(ai-film set-scene-continuity *)",
      "Bash(ai-film lock-continuity-master *)",
      "Bash(ai-film add-continuity-transition *)",
      "Bash(ai-film show-continuity *)",
      "Bash(ai-film add-feedback *)",
      "Bash(ai-film resolve-feedback *)",
      "Bash(./.venv/bin/ai-film init *)",
      "Bash(./.venv/bin/ai-film version)",
      "Bash(./.venv/bin/ai-film validate *)",
      "Bash(./.venv/bin/ai-film status *)",
      "Bash(./.venv/bin/ai-film models *)",
      "Bash(./.venv/bin/ai-film check-continuity *)",
      "Bash(./.venv/bin/ai-film review *)",
      "Bash(./.venv/bin/ai-film select-candidate *)",
      "Bash(./.venv/bin/ai-film review-media *)",
      "Bash(./.venv/bin/ai-film diagnose-video *)",
      "Bash(./.venv/bin/ai-film set-scene-continuity *)",
      "Bash(./.venv/bin/ai-film lock-continuity-master *)",
      "Bash(./.venv/bin/ai-film add-continuity-transition *)",
      "Bash(./.venv/bin/ai-film show-continuity *)",
      "Bash(./.venv/bin/ai-film add-feedback *)",
      "Bash(./.venv/bin/ai-film resolve-feedback *)"
    ]
  }
}
```

### Step 6: Run the tests to verify they pass

Run: `./.venv/bin/pytest tests/test_cli_scene_continuity_commands.py -v`
Expected: all tests PASS.

### Step 7: Run the full suite

Run: `./.venv/bin/pytest -q`
Expected: all tests passing, no regressions.

### Step 8: Commit

```bash
git add src/ai_film/cli.py .claude/settings.json tests/test_cli_scene_continuity_commands.py
git commit -m "feat: add set-scene-continuity, add-continuity-transition, lock-continuity-master, show-continuity commands"
```

---

## Task 3: Generation-time integration — reference ordering + prompt spatial fragment

**Files:**
- Modify: `src/ai_film/cli.py:146-169` (`_character_and_environment_references`/`_image_references`), and the three call sites that invoke `build_image_prompt` (`generate_image_cmd` ~line 207, `_build_stage_call`'s `image` branch ~line 558, `generate_candidates_cmd` ~line 632)
- Modify: `src/ai_film/prompts.py`
- Test: `tests/test_cli_generation_commands.py`, `tests/test_prompts.py`

**Interfaces:**
- Consumes: Task 1's `load_continuity`, `effective_spatial_state`, `scene_id_for_shot` (already imported into `cli.py` by Task 2).
- Produces: `build_image_prompt(shot: dict, spatial: dict | None = None) -> str` — the new optional second parameter later tasks and the agent-facing behavior depend on. `build_video_prompt` is unchanged (still calls `build_image_prompt(shot)` with no second argument).

### Step 1: Write the failing tests

Add to `tests/test_prompts.py`, after the existing `test_build_image_prompt_skips_characters_without_reference` test:

```python
def test_build_image_prompt_includes_spatial_fragment_when_given():
    shot = {"action": "they talk", "visual": {}, "camera": {}}
    spatial = {
        "Mara Voss": {"screen_side": "left", "facing": "right"},
        "Doctor": {"screen_side": "right", "facing": "left"},
    }
    prompt = build_image_prompt(shot, spatial=spatial)
    assert "Mara Voss is screen-left, facing right" in prompt
    assert "Doctor is screen-right, facing left" in prompt
    assert "maintain these relative positions" in prompt


def test_build_image_prompt_omits_spatial_fragment_when_none_or_empty():
    shot = {"action": "an empty corridor", "visual": {}, "camera": {}}
    assert "screen-" not in build_image_prompt(shot)
    assert "screen-" not in build_image_prompt(shot, spatial=None)
    assert "screen-" not in build_image_prompt(shot, spatial={})


def test_build_video_prompt_unaffected_by_spatial_state():
    """build_video_prompt is explicitly out of scope for this design — it
    calls build_image_prompt(shot) with no spatial argument, so even a
    shot with a full spatial canon produces the same video prompt as
    before this feature existed."""
    shot = {
        "action": "they talk", "visual": {}, "camera": {"movement": "static"},
        "characters": [],
    }
    prompt = build_video_prompt(shot)
    assert "screen-" not in prompt
```

Add to `tests/test_cli_generation_commands.py`, after `test_generate_image_chains_locked_predecessor_last`:

```python
def test_generate_image_includes_master_reference_between_characters_and_previous(
    tmp_path: Path, monkeypatch
):
    from ai_film.scene_continuity import lock_continuity_master, set_scene_continuity

    project_dir = _init_mock_project(tmp_path)
    shot1 = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot1["generation"]["image"] = {
        "status": "completed", "attempts": 1,
        "artifact": {"path": "04_storyboard/S01_SH01.png", "size_bytes": 4, "sha256": None},
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot1)
    (project_dir / "04_storyboard").mkdir(parents=True, exist_ok=True)
    (project_dir / "04_storyboard" / "S01_SH01.png").write_bytes(b"MASTER-IMAGE")
    set_scene_continuity(project_dir, "S01", "A", "left", "right", master_shot="S01_SH01")
    lock_continuity_master(project_dir, "S01")

    shot2 = _shot("S01_SH02")
    shot2["characters"] = [{"name": "A", "reference": "assets/characters/A/reference.png"}]
    save_shot(project_dir / "03_shots" / "S01_SH02.json", shot2)
    save_shot(project_dir / "03_shots" / "S01_SH03.json", _shot("S01_SH03"))
    _approve(project_dir, "S01_SH03")

    provider = _RecordingImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH03", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    # S01_SH03 has no locked predecessor (S01_SH02 was never generated), but the
    # scene's master reference is still attached — the fixed anchor is independent
    # of the local previous-shot chain.
    assert provider.requests[-1].reference_paths == [
        str(project_dir / "02_scenes" / "S01_master_reference.png")
    ]


def test_generate_image_attaches_master_and_previous_shot_as_distinct_references(
    tmp_path: Path, monkeypatch
):
    """S01_SH02's previous shot (S01_SH01) IS the scene's master shot, but
    the master reference is a frozen COPY (02_scenes/S01_master_reference.png)
    while the previous-shot reference is S01_SH01's own live artifact
    (04_storyboard/S01_SH01.png) — two different paths by construction, so
    both attach. This is the expected, common case: master and previous
    are independent anchors and neither replaces the other, even when
    they happen to originate from the same shot."""
    from ai_film.scene_continuity import lock_continuity_master, set_scene_continuity

    project_dir = _init_mock_project(tmp_path)
    shot1 = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot1["generation"]["image"] = {
        "status": "completed", "attempts": 1,
        "artifact": {"path": "04_storyboard/S01_SH01.png", "size_bytes": 4, "sha256": None},
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot1)
    (project_dir / "04_storyboard").mkdir(parents=True, exist_ok=True)
    (project_dir / "04_storyboard" / "S01_SH01.png").write_bytes(b"MASTER-IMAGE")
    set_scene_continuity(project_dir, "S01", "A", "left", "right", master_shot="S01_SH01")
    lock_continuity_master(project_dir, "S01")

    save_shot(project_dir / "03_shots" / "S01_SH02.json", _shot("S01_SH02"))
    _approve(project_dir, "S01_SH02")

    provider = _RecordingImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH02", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == [
        str(project_dir / "02_scenes" / "S01_master_reference.png"),
        str(project_dir / "04_storyboard" / "S01_SH01.png"),
    ]


def test_generate_image_dedup_guard_fires_on_literal_path_equality(tmp_path: Path, monkeypatch):
    """The frozen-snapshot design means master_reference_image
    (02_scenes/...) and a previous-shot reference (04_storyboard/...) can
    never naturally collide — but _image_references' de-dup guard exists
    to honor the "never send the same image twice" invariant regardless,
    and must actually work if the two ever do resolve to the same path
    (e.g. a future change, or a hand-edited continuity file). Force the
    collision directly against the continuity file to prove the guard
    branch itself is correct, since the normal CLI flow can't reach it."""
    from ai_film.scene_continuity import continuity_path

    project_dir = _init_mock_project(tmp_path)
    shot1 = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot1["generation"]["image"] = {
        "status": "completed", "attempts": 1,
        "artifact": {"path": "04_storyboard/S01_SH01.png", "size_bytes": 4, "sha256": None},
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot1)
    (project_dir / "04_storyboard").mkdir(parents=True, exist_ok=True)
    (project_dir / "04_storyboard" / "S01_SH01.png").write_bytes(b"IMAGE")
    save_shot(project_dir / "03_shots" / "S01_SH02.json", _shot("S01_SH02"))
    _approve(project_dir, "S01_SH02")

    continuity_path(project_dir, "S01").parent.mkdir(parents=True, exist_ok=True)
    continuity_path(project_dir, "S01").write_text(json.dumps({
        "scene_id": "S01", "master_shot": "S01_SH01",
        # Deliberately points at the SAME path previous_shot_image_reference
        # will resolve for S01_SH02, to force the collision.
        "master_reference_image": "04_storyboard/S01_SH01.png",
        "spatial": {}, "transitions": [],
    }))

    provider = _RecordingImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH02", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == [
        str(project_dir / "04_storyboard" / "S01_SH01.png"),
    ]


def test_generate_image_never_self_references_the_master_shot(tmp_path: Path, monkeypatch):
    from ai_film.scene_continuity import set_scene_continuity

    project_dir = _init_mock_project(tmp_path)
    set_scene_continuity(project_dir, "S01", "A", "left", "right", master_shot="S01_SH01")
    _approve(project_dir)

    provider = _RecordingImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    # S01_SH01 is regenerating itself — it must never be told to reference
    # its own not-yet-existent master snapshot, and lock-continuity-master
    # hasn't run yet anyway (master_reference_image is still absent).
    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == []


def test_generate_image_passes_effective_spatial_state_into_prompt(tmp_path: Path, monkeypatch):
    from ai_film.scene_continuity import set_scene_continuity

    project_dir = _init_mock_project(tmp_path)
    set_scene_continuity(project_dir, "S01", "Mara Voss", "left", "right")
    _approve(project_dir)

    provider = _RecordingImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert "Mara Voss is screen-left, facing right" in provider.requests[-1].prompt
```

### Step 2: Run the tests to verify they fail

Run: `./.venv/bin/pytest tests/test_prompts.py tests/test_cli_generation_commands.py -v`
Expected: the new tests FAIL (`build_image_prompt` doesn't accept `spatial` yet; reference lists don't include a master reference yet).

### Step 3: Update `prompts.py`

In `src/ai_film/prompts.py`, add a new helper function before `build_image_prompt` and change `build_image_prompt`'s signature:

```python
def _spatial_fragment(spatial: dict | None) -> str:
    if not spatial:
        return ""
    parts = [
        f"{character} is screen-{values['screen_side']}, facing {values['facing']}"
        for character, values in spatial.items()
    ]
    return (
        "; ".join(parts)
        + " — maintain these relative positions unless the shot's action "
        "explicitly changes them."
    )


def build_image_prompt(shot: dict, spatial: dict | None = None) -> str:
    parts = []
    legend = _reference_legend(shot)
    if legend:
        parts.append(legend)
    fragment = _spatial_fragment(spatial)
    if fragment:
        parts.append(fragment)
    parts.append(shot.get("action", ""))
    visual = shot.get("visual", {})
    if visual.get("style"):
        parts.append(f"style: {visual['style']}")
    if visual.get("lighting"):
        parts.append(f"lighting: {visual['lighting']}")
    camera = shot.get("camera", {})
    if camera.get("shot"):
        parts.append(f"{camera['shot']} shot")
    return ", ".join(part for part in parts if part)
```

`build_video_prompt` is unchanged — leave it exactly as-is (it already calls `build_image_prompt(shot)` with a single argument, which remains valid since `spatial` now defaults to `None`).

### Step 4: Update `cli.py`'s reference-building and prompt-building call sites

Replace `_image_references` (currently lines 156-169 of `src/ai_film/cli.py`):

```python
def _image_references(
    path: Path, shot_id: str, shot_data: dict, quiet: bool = False
) -> list[str]:
    references = _character_and_environment_references(path, shot_data)

    prev_ref = previous_shot_image_reference(path, shot_id)
    prev_abs = str(path / prev_ref) if prev_ref else None

    scene_id = scene_id_for_shot(shot_id)
    if scene_id is not None:
        continuity = load_continuity(path, scene_id)
        master_ref = continuity.get("master_reference_image")
        if master_ref and continuity.get("master_shot") != shot_id:
            master_abs = str(path / master_ref)
            if master_abs != prev_abs:
                references.append(master_abs)

    if prev_abs:
        references.append(prev_abs)
    elif previous_shot_id(shot_id) is not None and not quiet:
        typer.echo(
            f"note: {shot_id}'s predecessor in this scene has no locked image yet — "
            f"generating without a continuity anchor",
            err=True,
        )
    return references
```

Add a small helper right after it:

```python
def _effective_spatial(path: Path, shot_id: str) -> dict:
    scene_id = scene_id_for_shot(shot_id)
    if scene_id is None:
        return {}
    return effective_spatial_state(load_continuity(path, scene_id), shot_id)
```

Then update the three `build_image_prompt(shot_data)` call sites to pass `spatial=`:

In `generate_image_cmd` (currently around line 207):

```python
            prompt=build_image_prompt(shot_data, spatial=_effective_spatial(path, shot)),
```

(replacing `prompt=build_image_prompt(shot_data),`)

In `_build_stage_call`'s `image` branch (currently around line 558):

```python
            prompt=build_image_prompt(shot_data, spatial=_effective_spatial(path, shot_id)),
```

(replacing `prompt=build_image_prompt(shot_data),` — note this branch uses the local variable `shot_id`, not `shot`)

In `generate_candidates_cmd` (currently around line 632):

```python
        if prompt is None:
            prompt = build_image_prompt(shot_data, spatial=_effective_spatial(path, shot_id))
```

(replacing `prompt = build_image_prompt(shot_data)` — this branch already has a local `shot_id` variable from `target.split(":")[1]` a few lines above)

### Step 5: Run the tests to verify they pass

Run: `./.venv/bin/pytest tests/test_prompts.py tests/test_cli_generation_commands.py -v`
Expected: all tests PASS.

### Step 6: Run the full suite

Run: `./.venv/bin/pytest -q`
Expected: all tests passing, no regressions.

### Step 7: Commit

```bash
git add src/ai_film/cli.py src/ai_film/prompts.py tests/test_prompts.py tests/test_cli_generation_commands.py
git commit -m "feat: wire scene continuity into image generation (reference ordering + prompt fragment)"
```

---

## Task 4: `.claude/agents/ai-film-storyboard.md` — teach the Storyboard agent to author and check the canon

**Files:**
- Modify: `.claude/agents/ai-film-storyboard.md`

**Interfaces:**
- Consumes: Task 2's four CLI commands (`set-scene-continuity`, `add-continuity-transition`, `lock-continuity-master`, `show-continuity`) — this task only changes prose, not code, so it depends on those commands existing and behaving exactly as documented, but has no direct code dependency.

This task has no pytest cycle — it's a prose-only change to an agent instruction file, the same kind of task the existing `2026-08-23-agent-layer.md` plan already used as precedent for markdown-only tasks in this project.

### Step 1: Add the Step 2 addition

In `.claude/agents/ai-film-storyboard.md`, find this exact paragraph (the last paragraph of Step 2, immediately before `## Step 3: Continuity check`):

```
After writing a scene's shot files, run `ai-film validate` and fix anything it reports before moving on.
```

Replace it with:

```
After writing a scene's shot files, run `ai-film validate` and fix anything it reports before moving on.

**Scene spatial canon, for a scene's first shot only:** when you write the very first shot of a scene (`S<SS>_SH01`), decide each on-screen character's initial blocking — which side of frame they're on and which way they face — before writing that shot's `action` text, so the action can describe the same layout you're about to record. Record it with one `set-scene-continuity` call per on-screen character, plus `--master-shot` on at least one of those calls:

```bash
ai-film set-scene-continuity --scene S01 --character "Mara Voss" --screen-side left --facing right --master-shot S01_SH01
ai-film set-scene-continuity --scene S01 --character "Doctor" --screen-side right --facing left
```

`--screen-side` is one of `left|center|right`, `--facing` is one of `left|right|camera|away`. This is a one-time decision for the scene, not something you redo for every shot — every later shot in the scene inherits it automatically through generation. If a later shot in the same scene needs a deliberate blocking change (a character crosses the room, walks around another), declare it explicitly instead of just writing new `action` text and hoping it reads as consistent:

```bash
ai-film add-continuity-transition --scene S01 --after-shot S01_SH03 --character "Mara Voss" --screen-side right --facing left --reason "Mara walks around the Doctor to reach the door."
```

This takes effect starting the *next* shot after `S01_SH03`, not at `S01_SH03` itself.
```

### Step 2: Add the Step 5 addition

In the same file, find this exact paragraph (Step 5, the final sentence of the numbered step 4, "lock it"):

```
This writes the image into that shot's `generation.image.artifact` and marks it completed — the same effect `generate-image` would have, so nothing downstream needs to know it came from the candidate loop. `select-candidate` is re-runnable with a different `--id` if the user changes their mind later — just another `type: selection` round trip.
```

Replace it with:

```
This writes the image into that shot's `generation.image.artifact` and marks it completed — the same effect `generate-image` would have, so nothing downstream needs to know it came from the candidate loop. `select-candidate` is re-runnable with a different `--id` if the user changes their mind later — just another `type: selection` round trip.

**If this was the scene's first shot** (`S<SS>_SH01`), immediately run `ai-film lock-continuity-master --scene <SS>` right after locking it, before moving on to the scene's next shot:

```bash
ai-film lock-continuity-master --scene S01
```

This freezes that shot's just-locked image as the scene's permanent spatial anchor — a one-time snapshot, not something that updates if the shot is ever regenerated later. This is the only point in the whole run where this command is needed; every other shot in the scene generates against the canon `lock-continuity-master` just fixed in place.
```

### Step 3: Add the Step 3 addition

In the same file, find this exact sentence, the second sentence of Step 3:

```
Then run:

```bash
ai-film check-continuity --shot <id> --status <passed|warning|failed>
```
```

Replace it with:

```
Additionally, for a shot in a scene that has a spatial canon, run `ai-film show-continuity --shot <id>` before judging, and apply this rule: **a shot's described blocking may differ from the effective spatial state only if a transition already explains the difference by that shot** — otherwise this is a `failed` continuity result, not "probably fine." This check is about **relative spatial relationships and declared facing, not pixel-level framing** — a close-up filling the frame with one character is not a violation of `screen_side: right` just because the other character isn't visible; the canon constrains where a character *would be* if shown, not that every shot must show every character. Composition and shot-size decisions remain your normal judgment call, layered on top of (never contradicting) the canon. If a generated shot's own image looks like it drifted from the canon and no transition explains it, the fix is to regenerate that shot against the existing canon — never to edit the canon to match what got generated; only a deliberate, story-driven blocking decision (via `add-continuity-transition`) is allowed to change what the canon says is true.

Then run:

```bash
ai-film check-continuity --shot <id> --status <passed|warning|failed>
```
```

### Step 4: Verify the edits read coherently

Read the full file back and confirm: the Step 2 addition sits between the existing final paragraph and the `## Step 3` heading; the Step 3 addition sits inside Step 3, before the existing `check-continuity` command; the Step 5 addition sits inside the numbered list's item 4, after `select-candidate`'s existing explanation. No other text in the file should have changed.

### Step 5: Commit

```bash
git add .claude/agents/ai-film-storyboard.md
git commit -m "docs: teach the Storyboard agent to author and check the scene spatial canon"
```

---

## Task 5: End-to-end integration test

**Files:**
- Test: `tests/test_scene_continuity_golden_path.py`

**Interfaces:**
- Consumes: everything from Tasks 1-3 (CLI commands, reference ordering, prompt fragment) through the `ai-film` CLI surface only — this test exercises the exact command sequence an agent following Task 4's updated instructions would run, using the mock provider (no real network calls), mirroring `tests/test_media_review_golden_path.py`'s style.

### Step 1: Write the test

Create `tests/test_scene_continuity_golden_path.py`:

```python
# tests/test_scene_continuity_golden_path.py
import json
from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.scene_continuity import load_continuity
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
        "schema_version": "1.0", "id": shot_id, "status": "draft", "duration_seconds": 4,
        "continuity": {"status": "pending", "checked_at": None, "issues": []},
        "action": "two people talk in a corridor", "visual": {"style": "muted drama"},
        "camera": {"shot": "medium", "movement": "static"},
        "characters": [
            {"name": "A", "reference": "assets/characters/A/reference.png"},
            {"name": "B", "reference": "assets/characters/B/reference.png"},
        ],
        "generation": {
            "image": {"status": "pending", "attempts": 0},
            "video": {"status": "not_required"}, "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"}, "music": {"status": "not_required"},
        },
    }


def test_full_scene_continuity_golden_path(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    for shot_id in ("S01_SH01", "S01_SH02", "S01_SH03"):
        save_shot(project_dir / "03_shots" / f"{shot_id}.json", _shot(shot_id))
    result = runner.invoke(
        app,
        [
            "approve-generation", "--scope", "storyboard",
            "--targets", "S01_SH01,S01_SH02,S01_SH03", "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 0, result.output

    # Storyboard agent authors the scene's spatial canon while writing SH01.
    result = runner.invoke(
        app,
        [
            "set-scene-continuity", "--scene", "S01", "--character", "A",
            "--screen-side", "left", "--facing", "right", "--master-shot", "S01_SH01",
            "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app,
        [
            "set-scene-continuity", "--scene", "S01", "--character", "B",
            "--screen-side", "right", "--facing", "left", "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 0, result.output

    # SH01 generates and locks; its image becomes the frozen master reference.
    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app, ["lock-continuity-master", "--scene", "S01", "--path", str(project_dir)]
    )
    assert result.exit_code == 0, result.output
    continuity = load_continuity(project_dir, "S01")
    assert continuity["master_reference_image"] == "02_scenes/S01_master_reference.png"
    assert (project_dir / continuity["master_reference_image"]).exists()

    # SH02 generates: no transition yet, so its prompt/reference list should
    # reflect the initial canon and include the frozen master reference.
    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH02", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app, ["show-continuity", "--shot", "S01_SH02", "--path", str(project_dir)]
    )
    assert result.exit_code == 0, result.output
    assert "A: screen_side=left facing=right" in result.output
    assert "B: screen_side=right facing=left" in result.output

    # A deliberate blocking change is declared for SH03 onward.
    result = runner.invoke(
        app,
        [
            "add-continuity-transition", "--scene", "S01", "--after-shot", "S01_SH02",
            "--character", "A", "--screen-side", "right", "--facing", "left",
            "--reason", "A crosses the room.", "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 0, result.output

    # SH02 itself is still governed by the OLD state (the transition boundary
    # is strict — it takes effect starting the shot AFTER after_shot).
    result = runner.invoke(
        app, ["show-continuity", "--shot", "S01_SH02", "--path", str(project_dir)]
    )
    assert "A: screen_side=left facing=right" in result.output

    # SH03 sees the NEW state.
    result = runner.invoke(
        app, ["show-continuity", "--shot", "S01_SH03", "--path", str(project_dir)]
    )
    assert result.exit_code == 0, result.output
    assert "A: screen_side=right facing=left" in result.output
    assert "B: screen_side=right facing=left" in result.output

    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH03", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output

    # The master reference stays exactly what it was at lock time throughout —
    # regenerating SH01 later (--force, since its image stage is already
    # completed) must not change it.
    result = runner.invoke(
        app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir), "--force"]
    )
    assert result.exit_code == 0, result.output
    continuity_after = load_continuity(project_dir, "S01")
    assert continuity_after["master_reference_image"] == continuity["master_reference_image"]
```

### Step 2: Run the test to verify it fails, then passes

Run: `./.venv/bin/pytest tests/test_scene_continuity_golden_path.py -v`

If Tasks 1-3 are already complete and correct, this should PASS immediately — it exercises no new code, only the CLI surface those tasks already built. If it fails, that's a real integration bug Tasks 1-3's unit/CLI-level tests didn't catch (e.g. a wiring mismatch between two tasks) — fix the underlying code, not this test, unless the test itself is wrong per the spec.

### Step 3: Run the full suite one final time

Run: `./.venv/bin/pytest -q`
Expected: every test in the repo passes — this plan's own additions plus the pre-existing 269.

### Step 4: Commit

```bash
git add tests/test_scene_continuity_golden_path.py
git commit -m "test: add end-to-end scene continuity golden path"
```
