import json
from pathlib import Path

import pytest

from ai_film.approval import approve_generation, is_approved, load_config


def _init_config(project_dir: Path) -> None:
    (project_dir / "config.json").write_text(json.dumps({"providers": {}, "generation": {}}))


def test_is_approved_false_before_any_approval(tmp_path: Path):
    _init_config(tmp_path)
    assert is_approved(tmp_path, "storyboard", "S01_SH01") is False


def test_approve_generation_marks_listed_targets_approved(tmp_path: Path):
    _init_config(tmp_path)
    approve_generation(tmp_path, "storyboard", ["S01_SH01", "S01_SH02"], estimated_cost=4.82)
    assert is_approved(tmp_path, "storyboard", "S01_SH01") is True
    assert is_approved(tmp_path, "storyboard", "S01_SH02") is True


def test_approve_generation_does_not_cover_ids_outside_snapshot(tmp_path: Path):
    _init_config(tmp_path)
    approve_generation(tmp_path, "storyboard", ["S01_SH01"], estimated_cost=1.0)
    assert is_approved(tmp_path, "storyboard", "S01_SH02") is False


def test_bibles_and_storyboard_scopes_are_independent(tmp_path: Path):
    _init_config(tmp_path)
    approve_generation(tmp_path, "bibles", ["char:girl"], estimated_cost=0.4)
    assert is_approved(tmp_path, "bibles", "char:girl") is True
    assert is_approved(tmp_path, "storyboard", "S01_SH01") is False


def test_approve_generation_rejects_unknown_scope(tmp_path: Path):
    _init_config(tmp_path)
    with pytest.raises(ValueError):
        approve_generation(tmp_path, "not-a-scope", ["x"])


def test_approve_generation_writes_immutable_approval_log(tmp_path: Path):
    _init_config(tmp_path)
    approve_generation(tmp_path, "storyboard", ["S01_SH01"], estimated_cost=1.0)
    approvals_dir = tmp_path / "99_logs" / "approvals"
    assert len(list(approvals_dir.glob("*.json"))) == 1


def test_config_reflects_current_approval_state(tmp_path: Path):
    _init_config(tmp_path)
    approve_generation(tmp_path, "storyboard", ["S01_SH01"], estimated_cost=1.0, revision=1)
    config = load_config(tmp_path)
    record = config["generation_approval"]["storyboard"]
    assert record["approved"] is True
    assert record["scope"]["target_ids"] == ["S01_SH01"]
    assert record["scope"]["revision"] == 1
