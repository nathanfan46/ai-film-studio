import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.project import init_project

runner = CliRunner()


def _make_tiny_video(path: Path, duration: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"testsrc=duration={duration}:size=64x64:rate=10",
            "-t", str(duration), str(path),
        ],
        check=True, capture_output=True,
    )


def test_analyze_reference_video_cmd_rejects_missing_source(tmp_path: Path):
    project_dir = init_project(tmp_path / "proj", "Test")
    result = runner.invoke(
        app,
        ["analyze-reference-video", "--source", str(tmp_path / "missing.mp4"), "--path", str(project_dir)],
    )
    assert result.exit_code == 1
    assert "not found" in result.output


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_analyze_reference_video_cmd_success(tmp_path: Path):
    project_dir = init_project(tmp_path / "proj", "Test")
    source = tmp_path / "ref.mp4"
    _make_tiny_video(source, duration=1.0)

    result = runner.invoke(
        app,
        ["analyze-reference-video", "--source", str(source), "--path", str(project_dir)],
    )

    assert result.exit_code == 0
    assert "analyzed ref.mp4" in result.output
    assert (project_dir / "assets" / "reference-video" / "video_analysis_brief.json").exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_analyze_reference_video_cmd_refuses_second_run_without_force(tmp_path: Path):
    project_dir = init_project(tmp_path / "proj", "Test")
    source = tmp_path / "ref.mp4"
    _make_tiny_video(source, duration=1.0)
    runner.invoke(app, ["analyze-reference-video", "--source", str(source), "--path", str(project_dir)])

    result = runner.invoke(
        app,
        ["analyze-reference-video", "--source", str(source), "--path", str(project_dir)],
    )

    assert result.exit_code == 1
    assert "already exists" in result.output
