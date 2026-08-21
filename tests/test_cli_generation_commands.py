import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.shot_store import load_shot, save_shot

runner = CliRunner()


def _shot(shot_id: str) -> dict:
    return {
        "schema_version": "1.0",
        "id": shot_id,
        "status": "draft",
        "duration_seconds": 2,
        "continuity": {"status": "pending", "checked_at": None, "issues": []},
        "action": "girl steps out of darkness",
        "visual": {"style": "cinematic sci-fi"},
        "camera": {"shot": "close_up", "movement": "slow_push_in"},
        "characters": [],
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
    save_shot(project_dir / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    return project_dir


def test_generate_image_blocked_without_approval(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    result = runner.invoke(
        app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir)]
    )
    assert result.exit_code == 1


def _approve(project_dir: Path, shot_id: str = "S01_SH01") -> None:
    runner.invoke(
        app,
        ["approve-generation", "--scope", "storyboard", "--targets", shot_id, "--path", str(project_dir)],
    )


def test_generate_image_succeeds_after_approval(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    _approve(project_dir)
    result = runner.invoke(
        app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir)]
    )
    assert result.exit_code == 0
    assert "completed" in result.output
    assert (project_dir / "04_storyboard" / "S01_SH01.png").exists()


def test_generate_image_rejects_unknown_provider_name(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    config = json.loads((project_dir / "config.json").read_text())
    config["providers"]["image"]["provider"] = "not-a-real-provider"
    (project_dir / "config.json").write_text(json.dumps(config))
    _approve(project_dir)

    result = runner.invoke(
        app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir)]
    )

    assert result.exit_code == 1
    assert result.output.strip() != ""
    assert "not-a-real-provider" in result.output
    # No uncaught exception should propagate out of the command — resolve_provider's
    # ValueError must be caught and turned into a clean typer.Exit(1), not a traceback.
    assert not isinstance(result.exception, ValueError)


def test_generate_video_succeeds_after_approval(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    _approve(project_dir)
    result = runner.invoke(
        app, ["generate-video", "--shot", "S01_SH01", "--path", str(project_dir)]
    )
    assert result.exit_code == 0
    assert "completed" in result.output
    assert (project_dir / "05_video" / "S01_SH01.mp4").exists()


def test_generate_voice_succeeds_after_approval(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    _approve(project_dir)
    result = runner.invoke(
        app, ["generate-voice", "--shot", "S01_SH01", "--path", str(project_dir)]
    )
    assert result.exit_code == 0
    assert "completed" in result.output
    assert (project_dir / "06_audio" / "dialogue" / "S01_SH01.wav").exists()


def test_generate_sfx_succeeds_after_approval(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    _approve(project_dir)
    result = runner.invoke(
        app,
        [
            "generate-sfx", "--shot", "S01_SH01", "--prompt", "distant thunder rumble",
            "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 0
    assert "completed" in result.output
    assert (project_dir / "06_audio" / "sfx" / "S01_SH01.wav").exists()


def test_generate_music_succeeds_after_approval(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    _approve(project_dir)
    result = runner.invoke(
        app,
        [
            "generate-music", "--shot", "S01_SH01", "--prompt", "tense low strings",
            "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 0
    assert "completed" in result.output
    assert (project_dir / "06_audio" / "music" / "S01_SH01.wav").exists()


def test_check_continuity_updates_shot(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    result = runner.invoke(
        app,
        [
            "check-continuity", "--shot", "S01_SH01", "--status", "passed",
            "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 0
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    assert shot["continuity"]["status"] == "passed"


def test_check_continuity_records_issues(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    runner.invoke(
        app,
        [
            "check-continuity", "--shot", "S01_SH01", "--status", "failed",
            "--issue", "jacket color mismatch", "--path", str(project_dir),
        ],
    )
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    assert shot["continuity"]["issues"] == ["jacket color mismatch"]


def test_approve_generation_rejects_unknown_scope(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    result = runner.invoke(
        app,
        ["approve-generation", "--scope", "bogus", "--targets", "S01_SH01", "--path", str(project_dir)],
    )
    assert result.exit_code == 1


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_render_reports_preflight_failure_when_no_shots_generated(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    result = runner.invoke(app, ["render", "--path", str(project_dir)])
    assert result.exit_code == 1
    assert "preflight" in result.output.lower()
