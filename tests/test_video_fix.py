# tests/test_video_fix.py
import shutil
import subprocess
from pathlib import Path

import pytest

from ai_film.shot_store import save_shot
from ai_film.video_fix import trim_video


def _make_tiny_video(path: Path, duration: float = 3.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"testsrc=duration={duration}:size=64x64:rate=10",
            str(path),
        ],
        check=True, capture_output=True,
    )


def _shot_with_completed_video(shot_id: str, video_path: str, duration: float, **extra_artifact) -> dict:
    return {
        "schema_version": "1.0", "id": shot_id, "status": "draft", "duration_seconds": duration,
        "continuity": {"status": "passed", "checked_at": None, "issues": []},
        "generation": {
            "image": {"status": "not_required"},
            "video": {
                "status": "completed", "provider": "fal", "model": "hailuo-2.3", "attempts": 1,
                "artifact": {
                    "path": video_path, "size_bytes": 10, "sha256": None,
                    "duration_seconds": duration, **extra_artifact,
                },
            },
            "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }


def test_trim_video_rejects_stage_without_completed_artifact(tmp_path: Path):
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    shot = _shot_with_completed_video("S01_SH01", "05_video/S01_SH01.mp4", 6.0)
    shot["generation"]["video"] = {"status": "pending", "attempts": 0}
    save_shot(shot_path, shot)
    with pytest.raises(ValueError):
        trim_video(tmp_path, shot_path, end_seconds=2.0)


def test_trim_video_rejects_non_positive_end_seconds(tmp_path: Path):
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_completed_video("S01_SH01", "05_video/S01_SH01.mp4", 6.0))
    with pytest.raises(ValueError):
        trim_video(tmp_path, shot_path, end_seconds=0)


def test_trim_video_rejects_end_seconds_not_shorter_than_current(tmp_path: Path):
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_completed_video("S01_SH01", "05_video/S01_SH01.mp4", 6.0))
    with pytest.raises(ValueError):
        trim_video(tmp_path, shot_path, end_seconds=6.0)


def test_trim_video_raises_when_ffmpeg_missing(tmp_path: Path, monkeypatch):
    video_path = tmp_path / "05_video" / "S01_SH01.mp4"
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"FAKE-MP4")
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_completed_video("S01_SH01", "05_video/S01_SH01.mp4", 6.0))

    monkeypatch.setattr("ai_film.video_fix.shutil.which", lambda name: None)
    with pytest.raises(RuntimeError):
        trim_video(tmp_path, shot_path, end_seconds=2.0)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_trim_video_cuts_and_archives_prior_version(tmp_path: Path):
    video_path = tmp_path / "05_video" / "S01_SH01.mp4"
    _make_tiny_video(video_path, duration=6.0)
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_completed_video("S01_SH01", "05_video/S01_SH01.mp4", 6.0))

    stage = trim_video(tmp_path, shot_path, end_seconds=2.0)

    assert stage["version"] == 2
    assert len(stage["history"]) == 1
    assert stage["history"][0]["superseded_reason"] == "trim"
    archived_path = tmp_path / stage["history"][0]["artifact"]["path"]
    assert archived_path.exists()
    new_path = tmp_path / stage["artifact"]["path"]
    assert new_path.exists()
    assert new_path == video_path
    assert stage["artifact"]["duration_seconds"] == 2.0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_trim_video_preserves_lipsynced_flag(tmp_path: Path):
    video_path = tmp_path / "05_video" / "S01_SH01.mp4"
    _make_tiny_video(video_path, duration=6.0)
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(
        shot_path,
        _shot_with_completed_video("S01_SH01", "05_video/S01_SH01.mp4", 6.0, lipsynced=True),
    )

    stage = trim_video(tmp_path, shot_path, end_seconds=2.0)

    assert stage["artifact"]["lipsynced"] is True
