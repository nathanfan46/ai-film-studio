# tests/test_golden_path.py
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.shot_store import load_shot, save_shot

runner = CliRunner()


def _shot(shot_id: str, duration_seconds: float = 1.0) -> dict:
    return {
        "schema_version": "1.0",
        "id": shot_id,
        "status": "draft",
        "duration_seconds": duration_seconds,
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


def _overwrite_with_real_mp4(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=32x32:d=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "1", str(path),
        ],
        check=True, capture_output=True,
    )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_golden_path_produces_playable_final_video(tmp_path: Path):
    project_dir = tmp_path / "project"

    # 1. project initialized
    result = runner.invoke(app, ["init", "The Last Ship", "--path", str(project_dir)])
    assert result.exit_code == 0
    assert (project_dir / "config.json").exists()

    config = json.loads((project_dir / "config.json").read_text())
    for stage in config["providers"]:
        config["providers"][stage]["provider"] = "mock"
    (project_dir / "config.json").write_text(json.dumps(config))

    shot_ids = ["S01_SH01", "S01_SH02"]
    for shot_id in shot_ids:
        save_shot(project_dir / "03_shots" / f"{shot_id}.json", _shot(shot_id))

    # 2. shot.json validates against schema
    result = runner.invoke(app, ["validate", "--path", str(project_dir)])
    assert result.exit_code == 0

    # 3. continuity check passes
    for shot_id in shot_ids:
        result = runner.invoke(
            app, ["check-continuity", "--shot", shot_id, "--status", "passed", "--path", str(project_dir)]
        )
        assert result.exit_code == 0

    # 4. cost gate blocks generation until approved
    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 1

    # 5. approval unblocks it, and is recorded immutably
    result = runner.invoke(
        app,
        ["approve-generation", "--scope", "storyboard", "--targets", ",".join(shot_ids),
         "--estimated-cost", "0.50", "--path", str(project_dir)],
    )
    assert result.exit_code == 0
    approval_logs = list((project_dir / "99_logs" / "approvals").glob("*.json"))
    assert len(approval_logs) == 1

    # 6. provider jobs submit and complete for every shot
    for shot_id in shot_ids:
        result = runner.invoke(app, ["generate-image", "--shot", shot_id, "--path", str(project_dir)])
        assert result.exit_code == 0, result.output
        result = runner.invoke(app, ["generate-video", "--shot", shot_id, "--path", str(project_dir)])
        assert result.exit_code == 0, result.output

    # 7. artifacts exist; substitute real, valid video bytes for the render step
    #    (mock providers proved the job/cost-gate/logging bookkeeping above; ffmpeg
    #    now proves the render step works against real media)
    for shot_id in shot_ids:
        shot = load_shot(project_dir / "03_shots" / f"{shot_id}.json")
        video_path = Path(shot["generation"]["video"]["artifact"]["path"])
        assert video_path.exists()
        _overwrite_with_real_mp4(project_dir / video_path if not video_path.is_absolute() else video_path)

    # 8. ai-film status reports all shots completed
    result = runner.invoke(app, ["status", "--path", str(project_dir)])
    assert result.exit_code == 0
    assert result.output.count("completed") >= len(shot_ids)

    # 9. render preflight passes, render succeeds, final video exists and is playable
    result = runner.invoke(app, ["render", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output

    final_path = project_dir / "final" / "reel_001.mp4"
    assert final_path.exists()
    assert final_path.stat().st_size > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", str(final_path)],
        capture_output=True, text=True,
    )
    assert probe.returncode == 0
