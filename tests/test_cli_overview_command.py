from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.shot_store import save_shot

runner = CliRunner()


def _init_project(tmp_path: Path) -> Path:
    project_dir = tmp_path / "project"
    runner.invoke(app, ["init", "Test Film", "--path", str(project_dir)])
    return project_dir


def test_overview_builds_and_opens_without_launching_a_real_browser(
    tmp_path: Path, monkeypatch
):
    project_dir = _init_project(tmp_path)
    save_shot(
        project_dir / "03_shots" / "S01_SH01.json",
        {
            "schema_version": "1.0", "id": "S01_SH01", "status": "draft", "duration_seconds": 2,
            "continuity": {"status": "pending", "checked_at": None, "issues": []},
            "generation": {
                "image": {"status": "pending", "attempts": 0},
                "video": {"status": "pending", "attempts": 0},
                "voice": {"status": "not_required"},
                "sfx": {"status": "not_required"},
                "music": {"status": "not_required"},
            },
        },
    )
    opened = {}
    monkeypatch.setattr(
        "ai_film.cli.open_in_browser", lambda path: opened.setdefault("path", path)
    )

    result = runner.invoke(app, ["overview", "--path", str(project_dir)])

    assert result.exit_code == 0, result.output
    out_path = project_dir / "07_review" / "overview.html"
    assert out_path.exists()
    assert opened["path"] == out_path
    assert str(out_path) in result.output


def test_overview_works_on_a_freshly_initialized_project_with_no_shots(
    tmp_path: Path, monkeypatch
):
    project_dir = _init_project(tmp_path)
    monkeypatch.setattr("ai_film.cli.open_in_browser", lambda path: None)

    result = runner.invoke(app, ["overview", "--path", str(project_dir)])

    assert result.exit_code == 0, result.output
    assert (project_dir / "07_review" / "overview.html").exists()
