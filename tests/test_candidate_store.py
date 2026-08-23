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
