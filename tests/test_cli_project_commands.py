import json
from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.feedback_store import add_feedback_entry
from ai_film.shot_store import save_shot

runner = CliRunner()


def _shot(shot_id: str) -> dict:
    return {
        "schema_version": "1.0",
        "id": shot_id,
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


def test_init_creates_project_structure(tmp_path: Path):
    project_dir = tmp_path / "project"
    result = runner.invoke(app, ["init", "The Last Ship", "--path", str(project_dir)])
    assert result.exit_code == 0
    assert (project_dir / "config.json").exists()
    assert (project_dir / "03_shots").is_dir()


def test_models_lists_image_catalog():
    result = runner.invoke(app, ["models", "--capability", "image"])
    assert result.exit_code == 0
    assert "nano-banana" in result.output


def test_status_reports_shot_id_and_status(tmp_path: Path):
    project_dir = tmp_path / "project"
    save_shot(project_dir / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    result = runner.invoke(app, ["status", "--path", str(project_dir)])
    assert result.exit_code == 0
    assert "S01_SH01" in result.output
    assert "draft" in result.output


def test_validate_reports_invalid_shot_and_exits_nonzero(tmp_path: Path):
    project_dir = tmp_path / "project"
    shots_dir = project_dir / "03_shots"
    shots_dir.mkdir(parents=True)
    (shots_dir / "S01_SH01.json").write_text(json.dumps({"id": "S01_SH01"}))
    result = runner.invoke(app, ["validate", "--path", str(project_dir)])
    assert result.exit_code == 1
    assert "INVALID" in result.output


def test_validate_passes_on_well_formed_shot(tmp_path: Path):
    project_dir = tmp_path / "project"
    save_shot(project_dir / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    result = runner.invoke(app, ["validate", "--path", str(project_dir)])
    assert result.exit_code == 0


def test_status_ignores_feedback_files_and_reports_shot_status(tmp_path: Path):
    """Regression test: status should skip feedback.json files."""
    project_dir = tmp_path / "project"
    save_shot(project_dir / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    # Add feedback to create a feedback.json sibling
    add_feedback_entry(project_dir, "S01_SH01", target="sync", note="test feedback")
    result = runner.invoke(app, ["status", "--path", str(project_dir)])
    assert result.exit_code == 0
    assert "S01_SH01" in result.output
    assert "draft" in result.output


def test_validate_ignores_feedback_files(tmp_path: Path):
    """Regression test: validate should skip feedback.json files and report shot validity."""
    project_dir = tmp_path / "project"
    save_shot(project_dir / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    # Add feedback to create a feedback.json sibling
    add_feedback_entry(project_dir, "S01_SH01", target="sync", note="test feedback")
    result = runner.invoke(app, ["validate", "--path", str(project_dir)])
    assert result.exit_code == 0
