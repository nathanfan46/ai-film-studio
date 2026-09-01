# tests/test_scene_continuity_golden_path.py
import json
from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.scene_continuity import load_continuity
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
        "schema_version": "1.0", "id": shot_id, "status": "draft", "duration_seconds": 4,
        "continuity": {"status": "pending", "checked_at": None, "issues": []},
        "action": "two people talk in a corridor", "visual": {"style": "muted drama"},
        "camera": {"shot": "medium", "movement": "static"},
        "characters": [
            {"name": "A", "reference": "assets/characters/A/reference.png"},
            {"name": "B", "reference": "assets/characters/B/reference.png"},
        ],
        "generation": {
            "image": {"status": "pending", "attempts": 0},
            "video": {"status": "not_required"}, "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"}, "music": {"status": "not_required"},
        },
    }


def test_full_scene_continuity_golden_path(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    for shot_id in ("S01_SH01", "S01_SH02", "S01_SH03"):
        save_shot(project_dir / "03_shots" / f"{shot_id}.json", _shot(shot_id))
    result = runner.invoke(
        app,
        [
            "approve-generation", "--scope", "storyboard",
            "--targets", "S01_SH01,S01_SH02,S01_SH03", "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 0, result.output

    # Storyboard agent authors the scene's spatial canon while writing SH01.
    result = runner.invoke(
        app,
        [
            "set-scene-continuity", "--scene", "S01", "--character", "A",
            "--screen-side", "left", "--facing", "right", "--master-shot", "S01_SH01",
            "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app,
        [
            "set-scene-continuity", "--scene", "S01", "--character", "B",
            "--screen-side", "right", "--facing", "left", "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 0, result.output

    # SH01 generates and locks; its image becomes the frozen master reference.
    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app, ["lock-continuity-master", "--scene", "S01", "--path", str(project_dir)]
    )
    assert result.exit_code == 0, result.output
    continuity = load_continuity(project_dir, "S01")
    assert continuity["master_reference_image"] == "02_scenes/S01_master_reference.png"
    assert (project_dir / continuity["master_reference_image"]).exists()

    # SH02 generates: no transition yet, so its prompt/reference list should
    # reflect the initial canon and include the frozen master reference.
    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH02", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app, ["show-continuity", "--shot", "S01_SH02", "--path", str(project_dir)]
    )
    assert result.exit_code == 0, result.output
    assert "A: screen_side=left facing=right" in result.output
    assert "B: screen_side=right facing=left" in result.output

    # A deliberate blocking change is declared for SH03 onward.
    result = runner.invoke(
        app,
        [
            "add-continuity-transition", "--scene", "S01", "--after-shot", "S01_SH02",
            "--character", "A", "--screen-side", "right", "--facing", "left",
            "--reason", "A crosses the room.", "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 0, result.output

    # SH02 itself is still governed by the OLD state (the transition boundary
    # is strict — it takes effect starting the shot AFTER after_shot).
    result = runner.invoke(
        app, ["show-continuity", "--shot", "S01_SH02", "--path", str(project_dir)]
    )
    assert "A: screen_side=left facing=right" in result.output

    # SH03 sees the NEW state.
    result = runner.invoke(
        app, ["show-continuity", "--shot", "S01_SH03", "--path", str(project_dir)]
    )
    assert result.exit_code == 0, result.output
    assert "A: screen_side=right facing=left" in result.output
    assert "B: screen_side=right facing=left" in result.output

    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH03", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output

    # The master reference stays exactly what it was at lock time throughout —
    # regenerating SH01 later (--force, since its image stage is already
    # completed) must not change it.
    result = runner.invoke(
        app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir), "--force"]
    )
    assert result.exit_code == 0, result.output
    continuity_after = load_continuity(project_dir, "S01")
    assert continuity_after["master_reference_image"] == continuity["master_reference_image"]
