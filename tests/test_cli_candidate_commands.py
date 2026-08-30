import json
from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.providers.mock.image import MockImageProvider
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


def test_generate_candidates_for_shot_target_passes_character_references(
    tmp_path: Path, monkeypatch
):
    """generate-candidates for a shot:*:image target must gather the shot's locked-in
    character reference images and thread them through to the provider, same as the
    pre-existing generate-image command does — otherwise storyboard candidates are
    generated with zero conditioning on the character's selected reference.png."""
    project_dir = _init_mock_project(tmp_path)
    shot_path = project_dir / "03_shots" / "S01_SH01.json"
    save_shot(
        shot_path,
        {
            "schema_version": "1.0",
            "id": "S01_SH01",
            "status": "draft",
            "duration_seconds": 2,
            "continuity": {"status": "pending", "checked_at": None, "issues": []},
            "action": "girl steps out of darkness",
            "visual": {"style": "cinematic sci-fi"},
            "camera": {"shot": "close_up", "movement": "slow_push_in"},
            "characters": [{"name": "girl", "reference": "assets/characters/girl/reference.png"}],
            "generation": {
                "image": {"status": "pending", "attempts": 0},
                "video": {"status": "pending", "attempts": 0},
                "voice": {"status": "not_required"},
                "sfx": {"status": "not_required"},
                "music": {"status": "not_required"},
            },
        },
    )
    runner.invoke(
        app,
        ["approve-generation", "--scope", "storyboard", "--targets", "shot:S01_SH01:image",
         "--path", str(project_dir)],
    )
    provider = MockImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(
        app,
        ["generate-candidates", "--target", "shot:S01_SH01:image", "--count", "2",
         "--path", str(project_dir)],
    )

    assert result.exit_code == 0, result.output
    assert len(provider._requests) == 1
    sent_request = next(iter(provider._requests.values()))
    assert sent_request.reference_paths == [str(project_dir / "assets/characters/girl/reference.png")]


def test_generate_candidates_for_character_target_sends_no_references(
    tmp_path: Path, monkeypatch
):
    """character:/env: targets have no shot to read characters from, so
    reference_paths must default to empty — no behavior change there."""
    project_dir = _init_mock_project(tmp_path)
    _approve_bibles(project_dir)
    provider = MockImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(
        app,
        ["generate-candidates", "--target", "character:girl", "--count", "2",
         "--prompt", "a girl, sci-fi style", "--path", str(project_dir)],
    )

    assert result.exit_code == 0, result.output
    sent_request = next(iter(provider._requests.values()))
    assert sent_request.reference_paths == []


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
