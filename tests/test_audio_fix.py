# tests/test_audio_fix.py
import shutil
import subprocess
from pathlib import Path

import pytest

from ai_film.audio_fix import apply_audio_offset
from ai_film.shot_store import save_shot


def _make_tiny_wav(path: Path, duration: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono",
            "-t", str(duration), str(path),
        ],
        check=True, capture_output=True,
    )


def _shot_with_completed_voice(shot_id: str, voice_path: str) -> dict:
    return {
        "schema_version": "1.0", "id": shot_id, "status": "draft", "duration_seconds": 2,
        "continuity": {"status": "passed", "checked_at": None, "issues": []},
        "generation": {
            "image": {"status": "not_required"},
            "video": {"status": "not_required"},
            "voice": {
                "status": "completed", "provider": "fal", "model": "csm-1b", "attempts": 1,
                "artifact": {
                    "path": voice_path, "size_bytes": 10, "sha256": None, "duration_seconds": 1.0,
                },
            },
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }


def test_apply_audio_offset_rejects_unknown_track(tmp_path: Path):
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_completed_voice("S01_SH01", "06_audio/dialogue/S01_SH01.wav"))
    with pytest.raises(ValueError):
        apply_audio_offset(tmp_path, shot_path, track="video", offset_ms=100)


def test_apply_audio_offset_rejects_stage_without_completed_artifact(tmp_path: Path):
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    shot = _shot_with_completed_voice("S01_SH01", "06_audio/dialogue/S01_SH01.wav")
    shot["generation"]["music"] = {"status": "pending", "attempts": 0}
    save_shot(shot_path, shot)
    with pytest.raises(ValueError):
        apply_audio_offset(tmp_path, shot_path, track="music", offset_ms=100)


def test_apply_audio_offset_raises_when_ffmpeg_missing(tmp_path: Path, monkeypatch):
    voice_path = tmp_path / "06_audio" / "dialogue" / "S01_SH01.wav"
    voice_path.parent.mkdir(parents=True)
    voice_path.write_bytes(b"FAKE-WAV")
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_completed_voice("S01_SH01", "06_audio/dialogue/S01_SH01.wav"))

    monkeypatch.setattr("ai_film.audio_fix.shutil.which", lambda name: None)
    with pytest.raises(RuntimeError):
        apply_audio_offset(tmp_path, shot_path, track="voice", offset_ms=100)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_apply_audio_offset_delays_track_and_archives_prior_version(tmp_path: Path):
    voice_path = tmp_path / "06_audio" / "dialogue" / "S01_SH01.wav"
    _make_tiny_wav(voice_path)
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_completed_voice("S01_SH01", "06_audio/dialogue/S01_SH01.wav"))

    stage = apply_audio_offset(tmp_path, shot_path, track="voice", offset_ms=400)

    assert stage["version"] == 2
    assert len(stage["history"]) == 1
    assert stage["history"][0]["superseded_reason"] == "audio_offset"
    archived_path = tmp_path / stage["history"][0]["artifact"]["path"]
    assert archived_path.exists()
    new_path = tmp_path / stage["artifact"]["path"]
    assert new_path.exists()
    assert new_path == voice_path


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_apply_audio_offset_advances_track_with_negative_offset(tmp_path: Path):
    voice_path = tmp_path / "06_audio" / "dialogue" / "S01_SH01.wav"
    _make_tiny_wav(voice_path, duration=2.0)
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_completed_voice("S01_SH01", "06_audio/dialogue/S01_SH01.wav"))

    stage = apply_audio_offset(tmp_path, shot_path, track="voice", offset_ms=-300)

    assert stage["version"] == 2
    new_path = tmp_path / stage["artifact"]["path"]
    assert new_path.exists()
    assert new_path.stat().st_size > 0
