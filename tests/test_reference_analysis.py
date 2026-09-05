import shutil
import subprocess
from pathlib import Path

import pytest

from ai_film.reference_analysis import _probe_duration, _probe_stream_info, _require_ffmpeg


def _make_tiny_video(path: Path, duration: float = 2.0, size: str = "64x64", rate: int = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"testsrc=duration={duration}:size={size}:rate={rate}",
            "-t", str(duration), str(path),
        ],
        check=True, capture_output=True,
    )


def test_require_ffmpeg_raises_when_missing(monkeypatch):
    monkeypatch.setattr("ai_film.reference_analysis.shutil.which", lambda name: None)
    with pytest.raises(RuntimeError):
        _require_ffmpeg()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_probe_duration_reads_real_duration(tmp_path: Path):
    video_path = tmp_path / "clip.mp4"
    _make_tiny_video(video_path, duration=2.0)
    assert _probe_duration(video_path) == pytest.approx(2.0, abs=0.15)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_probe_stream_info_reads_resolution_and_fps(tmp_path: Path):
    video_path = tmp_path / "clip.mp4"
    _make_tiny_video(video_path, duration=1.0, size="64x48", rate=10)
    info = _probe_stream_info(video_path)
    assert info["resolution"] == "64x48"
    assert info["fps"] == pytest.approx(10.0, abs=0.01)
