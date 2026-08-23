# tests/test_candidate_golden_path.py
import json
from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.shot_store import load_shot

runner = CliRunner()


def test_full_candidate_loop_generate_review_edit_select(tmp_path: Path, monkeypatch):
    project_dir = tmp_path / "project"

    # 1. init and switch to mock providers
    result = runner.invoke(app, ["init", "The Last Ship", "--path", str(project_dir)])
    assert result.exit_code == 0
    config_path = project_dir / "config.json"
    config = json.loads(config_path.read_text())
    for stage in config["providers"]:
        config["providers"][stage]["provider"] = "mock"
    config_path.write_text(json.dumps(config))

    # 2. cost gate blocks candidate generation until approved
    result = runner.invoke(
        app,
        ["generate-candidates", "--target", "character:girl", "--count", "4",
         "--prompt", "a girl, cinematic sci-fi style", "--path", str(project_dir)],
    )
    assert result.exit_code == 1

    # 3. approve, then generate 4 candidates in one call
    result = runner.invoke(
        app,
        ["approve-generation", "--scope", "bibles", "--targets", "character:girl",
         "--path", str(project_dir)],
    )
    assert result.exit_code == 0

    result = runner.invoke(
        app,
        ["generate-candidates", "--target", "character:girl", "--count", "4",
         "--prompt", "a girl, cinematic sci-fi style", "--path", str(project_dir)],
    )
    assert result.exit_code == 0, result.output
    for i in range(1, 5):
        assert (
            project_dir / "assets" / "characters" / "girl" / "candidates" / f"{i:03d}.png"
        ).exists()

    # 4. review builds a gallery without opening a real browser in tests
    monkeypatch.setattr("ai_film.cli.open_in_browser", lambda path: None)
    result = runner.invoke(app, ["review", "--target", "character:girl", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    gallery_path = project_dir / "assets" / "characters" / "girl" / "candidates" / "review.html"
    assert gallery_path.exists()
    for i in range(1, 5):
        assert f"{i:03d}" in gallery_path.read_text()

    # 5. discuss + edit candidate 002 (regeneration fallback, since MockImageProvider
    #    defaults to supports_edit=False)
    result = runner.invoke(
        app,
        ["edit-candidate", "--target", "character:girl", "--id", "002",
         "--instruction", "black jacket instead of white", "--path", str(project_dir)],
    )
    assert result.exit_code == 0, result.output
    edited_path = project_dir / "assets" / "characters" / "girl" / "candidates" / "005.png"
    assert edited_path.exists()

    # 6. review again reflects the new candidate and its lineage
    result = runner.invoke(app, ["review", "--target", "character:girl", "--path", str(project_dir)])
    assert result.exit_code == 0
    gallery_content = gallery_path.read_text()
    assert "005" in gallery_content
    assert "edit of 002" in gallery_content

    # 7. select the edited candidate, locking it in
    result = runner.invoke(
        app,
        ["select-candidate", "--target", "character:girl", "--id", "005", "--path", str(project_dir)],
    )
    assert result.exit_code == 0, result.output
    reference_path = project_dir / "assets" / "characters" / "girl" / "reference.png"
    assert reference_path.exists()
    assert reference_path.read_bytes() == edited_path.read_bytes()

    # 8. changing the mind: re-select an earlier candidate overwrites the lock, no error
    result = runner.invoke(
        app,
        ["select-candidate", "--target", "character:girl", "--id", "001", "--path", str(project_dir)],
    )
    assert result.exit_code == 0
    candidate_001 = project_dir / "assets" / "characters" / "girl" / "candidates" / "001.png"
    assert reference_path.read_bytes() == candidate_001.read_bytes()

    # 9. none of the core engine's existing commands are affected: run the ordinary
    #    shot pipeline end to end alongside the candidate flow, to confirm the two
    #    coexist without interference (this is the "downstream stays unaware of
    #    candidates" guarantee from the spec, exercised for real).
    shot = {
        "schema_version": "1.0", "id": "S01_SH01", "status": "draft", "duration_seconds": 2,
        "continuity": {"status": "pending", "checked_at": None, "issues": []},
        "action": "girl steps out of darkness", "visual": {"style": "cinematic sci-fi"},
        "camera": {"shot": "close_up"}, "characters": [],
        "generation": {
            "image": {"status": "pending", "attempts": 0},
            "video": {"status": "not_required"},
            "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }
    (project_dir / "03_shots" / "S01_SH01.json").write_text(json.dumps(shot))
    result = runner.invoke(
        app,
        ["check-continuity", "--shot", "S01_SH01", "--status", "passed", "--path", str(project_dir)],
    )
    assert result.exit_code == 0
    result = runner.invoke(
        app,
        ["approve-generation", "--scope", "storyboard", "--targets", "S01_SH01",
         "--path", str(project_dir)],
    )
    assert result.exit_code == 0
    result = runner.invoke(
        app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir)]
    )
    assert result.exit_code == 0, result.output
    final_shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    assert final_shot["generation"]["image"]["status"] == "completed"
