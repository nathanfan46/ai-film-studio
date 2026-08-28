import json
from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.feedback_store import add_feedback_entry
from ai_film.shot_store import load_shot, save_shot

runner = CliRunner()


def _shot(shot_id: str) -> dict:
    return {
        "schema_version": "1.0", "id": shot_id, "status": "draft", "duration_seconds": 2,
        "continuity": {"status": "pending", "checked_at": None, "issues": []},
        "action": "a", "visual": {}, "camera": {}, "characters": [],
        "generation": {
            "image": {"status": "pending", "attempts": 0},
            "video": {"status": "pending", "attempts": 0},
            "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }


def _init_mock_project(tmp_path: Path) -> Path:
    project_dir = tmp_path / "project"
    runner.invoke(app, ["init", "Test Film", "--path", str(project_dir)])
    config = json.loads((project_dir / "config.json").read_text())
    for stage in config["providers"]:
        config["providers"][stage]["provider"] = "mock"
    (project_dir / "config.json").write_text(json.dumps(config))
    for shot_id in ("S01_SH01", "S01_SH02", "S01_SH03"):
        save_shot(project_dir / "03_shots" / f"{shot_id}.json", _shot(shot_id))
    return project_dir


def test_generate_all_generates_every_approved_shot(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    shot_ids = ["S01_SH01", "S01_SH02", "S01_SH03"]
    runner.invoke(
        app,
        ["approve-generation", "--scope", "storyboard", "--targets", ",".join(shot_ids), "--path", str(project_dir)],
    )
    result = runner.invoke(app, ["generate-all", "--stage", "image", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    for shot_id in shot_ids:
        shot = load_shot(project_dir / "03_shots" / f"{shot_id}.json")
        assert shot["generation"]["image"]["status"] == "completed"


def test_generate_all_reports_failures_without_stopping_other_shots(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    runner.invoke(
        app,
        ["approve-generation", "--scope", "storyboard", "--targets", "S01_SH01,S01_SH02", "--path", str(project_dir)],
    )
    result = runner.invoke(app, ["generate-all", "--stage", "image", "--path", str(project_dir)])
    assert result.exit_code == 1
    assert "S01_SH03" in result.output
    shot1 = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    assert shot1["generation"]["image"]["status"] == "completed"


def test_generate_all_rejects_unsupported_stage(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    result = runner.invoke(app, ["generate-all", "--stage", "sfx", "--path", str(project_dir)])
    assert result.exit_code == 1


def test_generate_all_generates_every_shot_for_video_stage(tmp_path: Path):
    """The video branch of _build_stage_call is wired identically to the image
    branch but was never exercised by any test until now."""
    project_dir = _init_mock_project(tmp_path)
    shot_ids = ["S01_SH01", "S01_SH02", "S01_SH03"]
    runner.invoke(
        app,
        [
            "approve-generation", "--scope", "storyboard", "--targets", ",".join(shot_ids),
            "--path", str(project_dir),
        ],
    )
    result = runner.invoke(app, ["generate-all", "--stage", "video", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    for shot_id in shot_ids:
        shot = load_shot(project_dir / "03_shots" / f"{shot_id}.json")
        assert shot["generation"]["video"]["status"] == "completed"
        artifact_path = Path(shot["generation"]["video"]["artifact"]["path"])
        assert (project_dir / artifact_path).exists()


def test_generate_all_generates_every_shot_for_voice_stage(tmp_path: Path):
    """The voice branch of _build_stage_call uses a different call signature
    (text=/speaker= from dialogue, not prompt=) and was never exercised by any
    test until now — a signature mismatch here would be invisible until real
    runtime use."""
    project_dir = _init_mock_project(tmp_path)
    shot_ids = ["S01_SH01", "S01_SH02", "S01_SH03"]
    runner.invoke(
        app,
        [
            "approve-generation", "--scope", "storyboard", "--targets", ",".join(shot_ids),
            "--path", str(project_dir),
        ],
    )
    result = runner.invoke(app, ["generate-all", "--stage", "voice", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    for shot_id in shot_ids:
        shot = load_shot(project_dir / "03_shots" / f"{shot_id}.json")
        assert shot["generation"]["voice"]["status"] == "completed"
        artifact_path = Path(shot["generation"]["voice"]["artifact"]["path"])
        assert (project_dir / artifact_path).exists()


def test_generate_all_ignores_feedback_files_and_generates_all_shots(tmp_path: Path):
    """Regression test: generate-all should skip feedback.json files."""
    project_dir = _init_mock_project(tmp_path)
    shot_ids = ["S01_SH01", "S01_SH02", "S01_SH03"]
    # Add feedback to one shot to create a feedback.json sibling
    add_feedback_entry(project_dir, "S01_SH01", target="sync", note="test feedback")
    runner.invoke(
        app,
        ["approve-generation", "--scope", "storyboard", "--targets", ",".join(shot_ids), "--path", str(project_dir)],
    )
    result = runner.invoke(app, ["generate-all", "--stage", "image", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    for shot_id in shot_ids:
        shot = load_shot(project_dir / "03_shots" / f"{shot_id}.json")
        assert shot["generation"]["image"]["status"] == "completed"
