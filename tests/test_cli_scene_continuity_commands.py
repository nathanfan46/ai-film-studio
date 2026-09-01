# tests/test_cli_scene_continuity_commands.py
from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.scene_continuity import load_continuity
from ai_film.shot_store import save_shot

runner = CliRunner()


def _shot_with_locked_image(shot_id: str, image_path: str) -> dict:
    return {
        "schema_version": "1.0", "id": shot_id, "status": "draft", "duration_seconds": 4,
        "continuity": {"status": "passed", "checked_at": None, "issues": []},
        "generation": {
            "image": {
                "status": "completed", "attempts": 1,
                "artifact": {"path": image_path, "size_bytes": 4, "sha256": None},
            },
            "video": {"status": "not_required"}, "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"}, "music": {"status": "not_required"},
        },
    }


def test_set_scene_continuity_cmd_writes_character_state(tmp_path: Path):
    result = runner.invoke(
        app,
        [
            "set-scene-continuity", "--scene", "S01", "--character", "Mara Voss",
            "--screen-side", "left", "--facing", "right", "--path", str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    data = load_continuity(tmp_path, "S01")
    assert data["spatial"]["Mara Voss"] == {"screen_side": "left", "facing": "right"}


def test_set_scene_continuity_cmd_rejects_bad_screen_side(tmp_path: Path):
    result = runner.invoke(
        app,
        [
            "set-scene-continuity", "--scene", "S01", "--character", "Mara Voss",
            "--screen-side", "sideways", "--facing", "right", "--path", str(tmp_path),
        ],
    )
    assert result.exit_code == 1


def test_set_scene_continuity_cmd_rejects_conflicting_change_without_force(tmp_path: Path):
    runner.invoke(
        app,
        [
            "set-scene-continuity", "--scene", "S01", "--character", "Mara Voss",
            "--screen-side", "left", "--facing", "right", "--path", str(tmp_path),
        ],
    )
    result = runner.invoke(
        app,
        [
            "set-scene-continuity", "--scene", "S01", "--character", "Mara Voss",
            "--screen-side", "right", "--facing", "left", "--path", str(tmp_path),
        ],
    )
    assert result.exit_code == 1


def test_set_scene_continuity_cmd_force_overwrites(tmp_path: Path):
    runner.invoke(
        app,
        [
            "set-scene-continuity", "--scene", "S01", "--character", "Mara Voss",
            "--screen-side", "left", "--facing", "right", "--path", str(tmp_path),
        ],
    )
    result = runner.invoke(
        app,
        [
            "set-scene-continuity", "--scene", "S01", "--character", "Mara Voss",
            "--screen-side", "right", "--facing", "left", "--force", "--path", str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    data = load_continuity(tmp_path, "S01")
    assert data["spatial"]["Mara Voss"] == {"screen_side": "right", "facing": "left"}


def test_add_continuity_transition_cmd_writes_transition(tmp_path: Path):
    result = runner.invoke(
        app,
        [
            "add-continuity-transition", "--scene", "S01", "--after-shot", "S01_SH03",
            "--character", "Mara Voss", "--screen-side", "right", "--facing", "left",
            "--reason", "walks around the doctor", "--path", str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    data = load_continuity(tmp_path, "S01")
    assert data["transitions"][0]["after_shot"] == "S01_SH03"
    assert data["transitions"][0]["changes"]["Mara Voss"] == {
        "screen_side": "right", "facing": "left",
    }


def test_show_continuity_cmd_reports_no_file_when_absent(tmp_path: Path):
    save_shot(
        tmp_path / "03_shots" / "S01_SH01.json",
        _shot_with_locked_image("S01_SH01", "04_storyboard/S01_SH01.png"),
    )
    result = runner.invoke(
        app, ["show-continuity", "--shot", "S01_SH01", "--path", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "no continuity file" in result.output


def test_show_continuity_cmd_prints_effective_state(tmp_path: Path):
    save_shot(
        tmp_path / "03_shots" / "S01_SH01.json",
        _shot_with_locked_image("S01_SH01", "04_storyboard/S01_SH01.png"),
    )
    runner.invoke(
        app,
        [
            "set-scene-continuity", "--scene", "S01", "--character", "Mara Voss",
            "--screen-side", "left", "--facing", "right", "--path", str(tmp_path),
        ],
    )
    result = runner.invoke(
        app, ["show-continuity", "--shot", "S01_SH01", "--path", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "Mara Voss" in result.output
    assert "screen_side=left" in result.output
    assert "facing=right" in result.output


def test_lock_continuity_master_cmd_requires_master_shot_file_to_exist(tmp_path: Path):
    runner.invoke(
        app,
        [
            "set-scene-continuity", "--scene", "S01", "--character", "Mara Voss",
            "--screen-side", "left", "--facing", "right", "--master-shot", "S01_SH01",
            "--path", str(tmp_path),
        ],
    )
    result = runner.invoke(
        app, ["lock-continuity-master", "--scene", "S01", "--path", str(tmp_path)]
    )
    assert result.exit_code == 1


def test_lock_continuity_master_cmd_snapshots_image(tmp_path: Path):
    image_path = tmp_path / "04_storyboard" / "S01_SH01.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"ORIGINAL-IMAGE")
    save_shot(
        tmp_path / "03_shots" / "S01_SH01.json",
        _shot_with_locked_image("S01_SH01", "04_storyboard/S01_SH01.png"),
    )
    runner.invoke(
        app,
        [
            "set-scene-continuity", "--scene", "S01", "--character", "Mara Voss",
            "--screen-side", "left", "--facing", "right", "--master-shot", "S01_SH01",
            "--path", str(tmp_path),
        ],
    )
    result = runner.invoke(
        app, ["lock-continuity-master", "--scene", "S01", "--path", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    data = load_continuity(tmp_path, "S01")
    assert (tmp_path / data["master_reference_image"]).read_bytes() == b"ORIGINAL-IMAGE"
