# tests/test_render_relative_path_regression.py
"""
Regression test for the "render fails on every invocation using the default/
relative --path" bug (final whole-branch review, Finding 1).

Root cause: generation.<stage>.artifact.path used to be written as
str(output_path), where output_path = path / "05_video" / f"{shot}.mp4" is
already project-prefixed. render.py's build_manifest() copied that
project-prefixed path verbatim into the manifest, and preflight()/render()
then RE-JOINED it against project_dir a second time. This only produced the
right path by accident when --path was absolute, because
Path("/abs") / "/abs/x" collapses to "/abs/x" -- which is exactly what
test_golden_path.py exercises (pytest's tmp_path is always absolute).

This test drives the CLI with a genuinely RELATIVE --path (achieved via
monkeypatch.chdir into a temp directory, the same technique pytest itself
recommends) so the double-join bug is actually exercised: on the old code,
Path("project") / "project/05_video/S01_SH01.mp4" produces
"project/project/05_video/S01_SH01.mp4", which does not exist on disk, and
render fails preflight on every invocation.
"""
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
def test_render_succeeds_with_relative_project_path(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    relative_path = Path("project")  # deliberately relative, NOT tmp_path-based

    result = runner.invoke(app, ["init", "The Last Ship", "--path", str(relative_path)])
    assert result.exit_code == 0, result.output
    assert (relative_path / "config.json").exists()

    config = json.loads((relative_path / "config.json").read_text())
    for stage in config["providers"]:
        config["providers"][stage]["provider"] = "mock"
    (relative_path / "config.json").write_text(json.dumps(config))

    shot_ids = ["S01_SH01", "S01_SH02"]
    for shot_id in shot_ids:
        save_shot(relative_path / "03_shots" / f"{shot_id}.json", _shot(shot_id))

    result = runner.invoke(
        app,
        [
            "approve-generation", "--scope", "storyboard", "--targets", ",".join(shot_ids),
            "--path", str(relative_path),
        ],
    )
    assert result.exit_code == 0, result.output

    for shot_id in shot_ids:
        result = runner.invoke(
            app, ["generate-image", "--shot", shot_id, "--path", str(relative_path)]
        )
        assert result.exit_code == 0, result.output
        result = runner.invoke(
            app, ["generate-video", "--shot", shot_id, "--path", str(relative_path)]
        )
        assert result.exit_code == 0, result.output

    for shot_id in shot_ids:
        shot = load_shot(relative_path / "03_shots" / f"{shot_id}.json")
        artifact_path = shot["generation"]["video"]["artifact"]["path"]

        # The regression itself: artifact.path must be project-RELATIVE
        # ("05_video/S01_SH01.mp4"), never project-prefixed
        # ("project/05_video/S01_SH01.mp4"), which is what causes render.py's
        # preflight()/render() to double-join it against project_dir.
        assert artifact_path == f"05_video/{shot_id}.mp4"

        _overwrite_with_real_mp4(relative_path / artifact_path)

    result = runner.invoke(app, ["render", "--path", str(relative_path)])
    assert result.exit_code == 0, result.output

    final_path = relative_path / "final" / "reel_001.mp4"
    assert final_path.exists()
    assert final_path.stat().st_size > 0

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", str(final_path)],
        capture_output=True, text=True,
    )
    assert probe.returncode == 0
