import shutil
import subprocess
from pathlib import Path

import pytest

from ai_film.reference_analysis import (
    _detect_scene_cuts,
    _probe_duration,
    _probe_stream_info,
    _require_ffmpeg,
    _scenes_from_cuts,
)


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


def _make_two_scene_video(path: Path, seg_duration: float = 2.0, rate: int = 10, size: str = "64x64") -> None:
    """A synthetic clip with exactly one hard scene cut at seg_duration:
    seg_duration seconds of a flat color, then seg_duration seconds of
    ffmpeg's own testsrc2 moving pattern. Verified empirically (during
    spec design) that ffmpeg's scene filter correctly detects the
    boundary between these two segments."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=blue:s={size}:d={seg_duration}:r={rate}",
            "-f", "lavfi", "-i", f"testsrc2=s={size}:d={seg_duration}:r={rate}",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", str(path),
        ],
        check=True, capture_output=True,
    )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_detect_scene_cuts_finds_the_boundary(tmp_path: Path):
    video_path = tmp_path / "two_scene.mp4"
    _make_two_scene_video(video_path, seg_duration=2.0)
    cuts = _detect_scene_cuts(video_path)
    assert len(cuts) == 1
    assert cuts[0] == pytest.approx(2.0, abs=0.15)


def test_scenes_from_cuts_builds_boundary_pairs():
    assert _scenes_from_cuts([2.0], 4.0) == [(0.0, 2.0), (2.0, 4.0)]


def test_scenes_from_cuts_handles_no_cuts():
    assert _scenes_from_cuts([], 4.0) == [(0.0, 4.0)]


def test_scenes_from_cuts_deduplicates_cut_at_zero():
    assert _scenes_from_cuts([0.0], 4.0) == [(0.0, 4.0)]


from ai_film.reference_analysis import _mean_interior_score, _scene_scores, _visual_change_level


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_scene_scores_spike_at_the_cut(tmp_path: Path):
    video_path = tmp_path / "two_scene.mp4"
    _make_two_scene_video(video_path, seg_duration=2.0)
    scores = _scene_scores(video_path)
    cut_scores = [score for t, score in scores if abs(t - 2.0) < 0.05]
    assert cut_scores and cut_scores[0] > 0.4


def test_mean_interior_score_excludes_boundaries():
    scores = [(0.0, 0.9), (1.0, 0.01), (2.0, 0.9)]
    assert _mean_interior_score(0.0, 2.0, scores) == pytest.approx(0.01)


def test_mean_interior_score_returns_zero_when_no_interior_frames():
    assert _mean_interior_score(0.0, 2.0, []) == 0.0


def test_visual_change_level_buckets():
    assert _visual_change_level(0.01) == "low"
    assert _visual_change_level(0.02) == "medium"
    assert _visual_change_level(0.08) == "medium"
    assert _visual_change_level(0.081) == "high"
