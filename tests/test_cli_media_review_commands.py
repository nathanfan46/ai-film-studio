# tests/test_cli_media_review_commands.py
import json
from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.feedback_store import load_feedback
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
        "schema_version": "1.0", "id": shot_id, "status": "draft", "duration_seconds": 5,
        "continuity": {"status": "passed", "checked_at": None, "issues": []},
        "generation": {
            "image": {"status": "not_required"},
            "video": {"status": "not_required"},
            "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }


def test_add_feedback_writes_entry(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    save_shot(project_dir / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    result = runner.invoke(
        app,
        [
            "add-feedback", "--shot", "S01_SH01", "--target", "video",
            "--at", "3.29", "--note", "too calm", "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "FB-001" in result.output
    data = load_feedback(project_dir, "S01_SH01")
    assert data["entries"][0]["note"] == "too calm"


def test_add_feedback_rejects_unknown_target(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    save_shot(project_dir / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    result = runner.invoke(
        app,
        [
            "add-feedback", "--shot", "S01_SH01", "--target", "bogus",
            "--note", "x", "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 1


def test_resolve_feedback_marks_entry_resolved(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    save_shot(project_dir / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    runner.invoke(
        app,
        [
            "add-feedback", "--shot", "S01_SH01", "--target", "sync",
            "--note", "early", "--path", str(project_dir),
        ],
    )
    result = runner.invoke(
        app,
        ["resolve-feedback", "--shot", "S01_SH01", "--id", "FB-001", "--path", str(project_dir)],
    )
    assert result.exit_code == 0, result.output
    data = load_feedback(project_dir, "S01_SH01")
    assert data["entries"][0]["status"] == "resolved"


def test_resolve_feedback_rejects_unknown_id(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    save_shot(project_dir / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    result = runner.invoke(
        app,
        ["resolve-feedback", "--shot", "S01_SH01", "--id", "FB-999", "--path", str(project_dir)],
    )
    assert result.exit_code == 1


def test_apply_audio_offset_rejects_stage_without_artifact(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    save_shot(project_dir / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    result = runner.invoke(
        app,
        [
            "apply-audio-offset", "--shot", "S01_SH01", "--track", "voice",
            "--offset-ms", "100", "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 1


def test_review_media_builds_and_opens_page(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    save_shot(project_dir / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    monkeypatch.setattr("ai_film.cli.open_in_browser", lambda path: None)
    result = runner.invoke(app, ["review-media", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert (project_dir / "07_review" / "S01_SH01.html").exists()


def test_review_media_rejects_unknown_shot(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    result = runner.invoke(app, ["review-media", "--shot", "NOPE", "--path", str(project_dir)])
    assert result.exit_code == 1
