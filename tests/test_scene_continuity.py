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


@pytest.mark.parametrize("bad_scene_id", ["SC01", "not-a-scene"])
def test_set_scene_continuity_rejects_malformed_scene_id(tmp_path: Path, bad_scene_id: str):
    with pytest.raises(ValueError):
        set_scene_continuity(tmp_path, bad_scene_id, "Mara Voss", "left", "right")


@pytest.mark.parametrize("bad_scene_id", ["SC01", "not-a-scene"])
def test_add_continuity_transition_rejects_malformed_scene_id(tmp_path: Path, bad_scene_id: str):
    with pytest.raises(ValueError):
        add_continuity_transition(
            tmp_path, bad_scene_id, "S01_SH03", "Mara Voss", "right", "left", "walks around",
        )


@pytest.mark.parametrize("bad_scene_id", ["SC01", "not-a-scene"])
def test_lock_continuity_master_rejects_malformed_scene_id(tmp_path: Path, bad_scene_id: str):
    with pytest.raises(ValueError):
        lock_continuity_master(tmp_path, bad_scene_id)


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
