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
