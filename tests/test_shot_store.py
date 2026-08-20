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
