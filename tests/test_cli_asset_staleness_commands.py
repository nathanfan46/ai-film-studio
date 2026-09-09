from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.project import init_project
from ai_film.services.generation_service import sha256_of_file
from ai_film.shot_store import save_shot

runner = CliRunner()


def _shot_with_source_assets(shot_id: str, source_assets: list[dict]) -> dict:
    return {
        "schema_version": "1.0", "id": shot_id, "status": "draft", "duration_seconds": 4,
        "continuity": {"status": "passed", "checked_at": None, "issues": []},
        "generation": {
            "image": {"status": "completed", "source_assets": source_assets},
            "video": {"status": "not_required"},
            "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }


def test_check_stale_cmd_reports_no_stale_shots(tmp_path: Path):
    project_dir = init_project(tmp_path / "proj", "Test")

    result = runner.invoke(app, ["check-stale", "--path", str(project_dir)])

    assert result.exit_code == 0
    assert "no stale shots" in result.output.lower()


def test_check_stale_cmd_reports_a_stale_shot(tmp_path: Path):
    project_dir = init_project(tmp_path / "proj", "Test")
    reference = project_dir / "assets" / "characters" / "mara" / "reference.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"MARA-V1")
    old_hash = sha256_of_file(reference)
    save_shot(project_dir / "03_shots" / "S01_SH01.json", _shot_with_source_assets(
        "S01_SH01", [{"path": "assets/characters/mara/reference.png", "sha256": old_hash}]
    ))
    reference.write_bytes(b"MARA-V2-CHANGED")

    result = runner.invoke(app, ["check-stale", "--path", str(project_dir)])

    assert result.exit_code == 0
    assert "S01_SH01" in result.output
    assert "assets/characters/mara/reference.png" in result.output
