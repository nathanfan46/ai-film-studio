import json
from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app

runner = CliRunner()


def _init_mock_project(tmp_path: Path) -> Path:
    project_dir = tmp_path / "project"
    runner.invoke(app, ["init", "Test Film", "--path", str(project_dir)])
    config = json.loads((project_dir / "config.json").read_text())
    for stage in config["providers"]:
        config["providers"][stage]["provider"] = "mock"
    (project_dir / "config.json").write_text(json.dumps(config))
    return project_dir


def _approve_bibles(project_dir: Path, target: str = "character:girl") -> None:
    runner.invoke(
        app,
        ["approve-generation", "--scope", "bibles", "--targets", target, "--path", str(project_dir)],
    )


def test_generate_candidates_blocked_without_approval(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)

    result = runner.invoke(
        app,
        ["generate-candidates", "--target", "character:girl", "--count", "4",
         "--prompt", "a girl, sci-fi style", "--path", str(project_dir)],
    )

    assert result.exit_code == 1


def test_generate_candidates_succeeds_after_approval(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    _approve_bibles(project_dir)

    result = runner.invoke(
        app,
        ["generate-candidates", "--target", "character:girl", "--count", "4",
         "--prompt", "a girl, sci-fi style", "--path", str(project_dir)],
    )

    assert result.exit_code == 0, result.output
    for i in range(1, 5):
        assert (project_dir / "assets" / "characters" / "girl" / "candidates" / f"{i:03d}.png").exists()


def test_generate_candidates_requires_prompt_for_character_target(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    _approve_bibles(project_dir)

    result = runner.invoke(
        app,
        ["generate-candidates", "--target", "character:girl", "--count", "4",
         "--path", str(project_dir)],
    )

    assert result.exit_code == 1
    assert "--prompt" in result.output


def test_review_errors_clearly_on_empty_pool(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)

    result = runner.invoke(app, ["review", "--target", "character:girl", "--path", str(project_dir)])

    assert result.exit_code == 1


def test_review_builds_gallery_without_opening_browser_in_tests(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    _approve_bibles(project_dir)
    runner.invoke(
        app,
        ["generate-candidates", "--target", "character:girl", "--count", "2",
         "--prompt", "a girl", "--path", str(project_dir)],
    )
    opened = {}
    monkeypatch.setattr(
        "ai_film.cli.open_in_browser", lambda path: opened.setdefault("path", path)
    )

    result = runner.invoke(app, ["review", "--target", "character:girl", "--path", str(project_dir)])

    assert result.exit_code == 0, result.output
    assert opened["path"] == (
        project_dir / "assets" / "characters" / "girl" / "candidates" / "review.html"
    )


def test_select_candidate_locks_in_the_pick(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    _approve_bibles(project_dir)
    runner.invoke(
        app,
        ["generate-candidates", "--target", "character:girl", "--count", "2",
         "--prompt", "a girl", "--path", str(project_dir)],
    )

    result = runner.invoke(
        app,
        ["select-candidate", "--target", "character:girl", "--id", "002", "--path", str(project_dir)],
    )

    assert result.exit_code == 0, result.output
    assert (project_dir / "assets" / "characters" / "girl" / "reference.png").exists()


def test_edit_candidate_blocked_without_approval(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    _approve_bibles(project_dir)
    runner.invoke(
        app,
        ["generate-candidates", "--target", "character:girl", "--count", "1",
         "--prompt", "a girl", "--path", str(project_dir)],
    )
    # Revoke approval by re-approving a different, unrelated target only.
    config_path = project_dir / "config.json"
    config = json.loads(config_path.read_text())
    config["generation_approval"] = {}
    config_path.write_text(json.dumps(config))

    result = runner.invoke(
        app,
        ["edit-candidate", "--target", "character:girl", "--id", "001",
         "--instruction", "black jacket", "--path", str(project_dir)],
    )

    assert result.exit_code == 1


def test_edit_candidate_adds_a_new_candidate(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    _approve_bibles(project_dir)
    runner.invoke(
        app,
        ["generate-candidates", "--target", "character:girl", "--count", "1",
         "--prompt", "a girl", "--path", str(project_dir)],
    )

    result = runner.invoke(
        app,
        ["edit-candidate", "--target", "character:girl", "--id", "001",
         "--instruction", "black jacket instead of white", "--path", str(project_dir)],
    )

    assert result.exit_code == 0, result.output
    assert (project_dir / "assets" / "characters" / "girl" / "candidates" / "002.png").exists()
