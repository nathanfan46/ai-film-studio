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


def test_full_turnaround_flow_resolves_to_locked_back_angle(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)

    reference_dir = project_dir / "assets" / "characters" / "mara"
    reference_dir.mkdir(parents=True)
    (reference_dir / "reference.png").write_bytes(b"PRIMARY-REF")

    target = "character:mara:turnaround:back"
    approve = runner.invoke(
        app, ["approve-generation", "--scope", "bibles", "--targets", target, "--path", str(project_dir)],
    )
    assert approve.exit_code == 0, approve.output

    generate = runner.invoke(
        app, ["generate-candidates", "--target", target, "--count", "1",
              "--prompt", "mara, back view, isolated character reference",
              "--path", str(project_dir)],
    )
    assert generate.exit_code == 0, generate.output

    candidate_set = json.loads(
        (project_dir / "assets" / "characters" / "mara" / "turnaround" / "back" / "candidates.json").read_text()
    )
    candidate_id = candidate_set["candidates"][0]["id"]

    select = runner.invoke(
        app, ["select-candidate", "--target", target, "--id", candidate_id, "--path", str(project_dir)],
    )
    assert select.exit_code == 0, select.output

    resolve_with_orientation = runner.invoke(
        app, ["resolve-character-reference", "--name", "mara", "--orientation", "back",
              "--path", str(project_dir)],
    )
    assert resolve_with_orientation.exit_code == 0, resolve_with_orientation.output
    assert resolve_with_orientation.stdout.strip() == "assets/characters/mara/turnaround/back/reference.png"

    resolve_without_orientation = runner.invoke(
        app, ["resolve-character-reference", "--name", "mara", "--path", str(project_dir)],
    )
    assert resolve_without_orientation.exit_code == 0, resolve_without_orientation.output
    assert resolve_without_orientation.stdout.strip() == "assets/characters/mara/reference.png"


def test_full_turnaround_flow_falls_back_before_any_angle_is_locked(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    reference_dir = project_dir / "assets" / "characters" / "mara"
    reference_dir.mkdir(parents=True)
    (reference_dir / "reference.png").write_bytes(b"PRIMARY-REF")

    result = runner.invoke(
        app, ["resolve-character-reference", "--name", "mara", "--orientation", "side",
              "--path", str(project_dir)],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "assets/characters/mara/reference.png"
