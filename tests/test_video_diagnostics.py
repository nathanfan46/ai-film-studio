# tests/test_video_diagnostics.py
import shutil
import subprocess
from pathlib import Path

import pytest

from ai_film.shot_store import save_shot
from ai_film.video_diagnostics import diagnose_video


def _make_tiny_video(path: Path, duration: float = 2.0, with_audio: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=64x64:rate=10"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-shortest"]
    cmd += ["-t", str(duration), str(path)]
    subprocess.run(cmd, check=True, capture_output=True)


def _shot_with_completed_video(shot_id: str, video_path: str, duration: float) -> dict:
    return {
        "schema_version": "1.0", "id": shot_id, "status": "draft", "duration_seconds": duration,
        "continuity": {"status": "passed", "checked_at": None, "issues": []},
        "generation": {
            "image": {"status": "not_required"},
            "video": {
                "status": "completed", "provider": "fal", "model": "hailuo-2.3", "attempts": 1,
                "artifact": {
                    "path": video_path, "size_bytes": 10, "sha256": None, "duration_seconds": duration,
                },
            },
            "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }


def test_diagnose_video_rejects_missing_artifact(tmp_path: Path):
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    shot = _shot_with_completed_video("S01_SH01", "05_video/S01_SH01.mp4", 2.0)
    shot["generation"]["video"] = {"status": "pending", "attempts": 0}
    save_shot(shot_path, shot)
    with pytest.raises(ValueError):
        diagnose_video(tmp_path, shot_path)


def test_diagnose_video_rejects_non_positive_interval(tmp_path: Path):
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_completed_video("S01_SH01", "05_video/S01_SH01.mp4", 2.0))
    with pytest.raises(ValueError):
        diagnose_video(tmp_path, shot_path, interval_seconds=0)


def test_diagnose_video_raises_when_ffmpeg_missing(tmp_path: Path, monkeypatch):
    video_path = tmp_path / "05_video" / "S01_SH01.mp4"
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"FAKE-MP4")
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_completed_video("S01_SH01", "05_video/S01_SH01.mp4", 2.0))

    monkeypatch.setattr("ai_film.video_diagnostics.shutil.which", lambda name: None)
    with pytest.raises(RuntimeError):
        diagnose_video(tmp_path, shot_path)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_diagnose_video_extracts_frames_and_detects_silence(tmp_path: Path):
    video_path = tmp_path / "05_video" / "S01_SH01.mp4"
    _make_tiny_video(video_path, duration=2.0, with_audio=True)
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_completed_video("S01_SH01", "05_video/S01_SH01.mp4", 2.0))

    report = diagnose_video(tmp_path, shot_path, interval_seconds=0.5)

    assert report["shot_id"] == "S01_SH01"
    assert len(report["frames"]) == 4
    for frame in report["frames"]:
        assert (tmp_path / frame["path"]).exists()
    assert report["silence_windows"] is not None
    assert report["silence_windows"][0]["start"] == 0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_diagnose_video_reports_no_audio_track(tmp_path: Path):
    video_path = tmp_path / "05_video" / "S01_SH01.mp4"
    _make_tiny_video(video_path, duration=1.0, with_audio=False)
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_completed_video("S01_SH01", "05_video/S01_SH01.mp4", 1.0))

    report = diagnose_video(tmp_path, shot_path, interval_seconds=0.5)

    assert report["silence_windows"] is None
