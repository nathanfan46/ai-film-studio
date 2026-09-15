from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app

runner = CliRunner()


def test_resolve_character_reference_cmd_falls_back_to_primary(tmp_path: Path):
    result = runner.invoke(
        app, ["resolve-character-reference", "--name", "mara", "--path", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "assets/characters/mara/reference.png"


def test_resolve_character_reference_cmd_returns_locked_turnaround_angle(tmp_path: Path):
    angle_dir = tmp_path / "assets" / "characters" / "mara" / "turnaround" / "back"
    angle_dir.mkdir(parents=True)
    (angle_dir / "reference.png").write_bytes(b"BACK-REF")

    result = runner.invoke(
        app, ["resolve-character-reference", "--name", "mara", "--orientation", "back",
              "--path", str(tmp_path)],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "assets/characters/mara/turnaround/back/reference.png"


def test_resolve_character_reference_cmd_falls_back_when_angle_not_locked(tmp_path: Path):
    result = runner.invoke(
        app, ["resolve-character-reference", "--name", "mara", "--orientation", "back",
              "--path", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "assets/characters/mara/reference.png"


def test_resolve_character_reference_cmd_rejects_unsafe_orientation(tmp_path: Path):
    result = runner.invoke(
        app, ["resolve-character-reference", "--name", "mara", "--orientation", "../etc",
              "--path", str(tmp_path)],
    )
    assert result.exit_code == 1
