# tests/test_video_fix.py
import shutil
import subprocess
from pathlib import Path

import pytest

from ai_film.shot_store import save_shot
from ai_film.video_fix import mux_audio_track, trim_video


def _make_tiny_video(path: Path, duration: float = 3.0, with_audio: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=64x64:rate=10"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=" + str(duration), "-shortest"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True, capture_output=True)


def _make_tiny_audio(path: Path, duration: float = 3.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=880:duration={duration}",
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


def _shot_with_video_and_track(
    shot_id: str, video_path: str, video_duration: float,
    track: str | None = None, track_path: str | None = None,
    video_extra: dict | None = None, dialogue: dict | None = None, characters: list | None = None,
) -> dict:
    shot = _shot_with_completed_video(shot_id, video_path, video_duration, **(video_extra or {}))
    if track is not None:
        model = "thinksound" if track == "sfx" else "speech-02-hd"
        shot["generation"][track] = {
            "status": "completed", "provider": "fal", "model": model, "attempts": 1,
            "artifact": {"path": track_path, "size_bytes": 10, "sha256": None, "duration_seconds": 2.0},
        }
    if dialogue is not None:
        shot["dialogue"] = dialogue
    if characters is not None:
        shot["characters"] = characters
    return shot


def test_mux_audio_track_rejects_unknown_track(tmp_path: Path):
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_video_and_track("S01_SH01", "05_video/S01_SH01.mp4", 4.0))
    with pytest.raises(ValueError):
        mux_audio_track(tmp_path, shot_path, "music")


def test_mux_audio_track_rejects_missing_video_artifact(tmp_path: Path):
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    shot = _shot_with_video_and_track(
        "S01_SH01", "05_video/S01_SH01.mp4", 4.0, track="sfx", track_path="06_audio/sfx/S01_SH01.wav",
    )
    shot["generation"]["video"] = {"status": "pending", "attempts": 0}
    save_shot(shot_path, shot)
    with pytest.raises(ValueError):
        mux_audio_track(tmp_path, shot_path, "sfx")


def test_mux_audio_track_rejects_missing_track_artifact(tmp_path: Path):
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_video_and_track("S01_SH01", "05_video/S01_SH01.mp4", 4.0))
    with pytest.raises(ValueError):
        mux_audio_track(tmp_path, shot_path, "sfx")


def test_mux_audio_track_raises_when_ffmpeg_missing(tmp_path: Path, monkeypatch):
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(
        shot_path,
        _shot_with_video_and_track(
            "S01_SH01", "05_video/S01_SH01.mp4", 4.0, track="sfx", track_path="06_audio/sfx/S01_SH01.wav",
        ),
    )
    monkeypatch.setattr("ai_film.video_fix.shutil.which", lambda name: None)
    with pytest.raises(RuntimeError):
        mux_audio_track(tmp_path, shot_path, "sfx")


def test_mux_audio_track_rejects_relock_without_force(tmp_path: Path):
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(
        shot_path,
        _shot_with_video_and_track(
            "S01_SH01", "05_video/S01_SH01.mp4", 4.0, track="sfx", track_path="06_audio/sfx/S01_SH01.wav",
            video_extra={"sfx_muxed": True},
        ),
    )
    with pytest.raises(ValueError):
        mux_audio_track(tmp_path, shot_path, "sfx")


def test_mux_audio_track_voice_rejects_on_screen_speaker(tmp_path: Path):
    """The invariant this exists to enforce: raw voice-muxing an on-screen
    speaker's line would play the audio with no mouth movement to match
    it — that combination must use generate-lipsync instead."""
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(
        shot_path,
        _shot_with_video_and_track(
            "S01_SH01", "05_video/S01_SH01.mp4", 4.0,
            track="voice", track_path="06_audio/dialogue/S01_SH01.wav",
            dialogue={"text": "hello", "speaker": "Mara"},
            characters=[{"name": "Mara", "reference": "assets/characters/Mara/reference.png"}],
        ),
    )
    with pytest.raises(ValueError):
        mux_audio_track(tmp_path, shot_path, "voice")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_mux_audio_track_voice_allows_off_screen_speaker(tmp_path: Path):
    """The scenario this whole command was generalized for: a shot shows
    only a listener, but the dialogue's speaker (a caller) is off screen
    — the line still needs to be audible, with no lip-sync to anyone."""
    video_path = tmp_path / "05_video" / "S01_SH01.mp4"
    _make_tiny_video(video_path, duration=4.0, with_audio=False)
    voice_path = tmp_path / "06_audio" / "dialogue" / "S01_SH01.wav"
    _make_tiny_audio(voice_path, duration=2.0)
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(
        shot_path,
        _shot_with_video_and_track(
            "S01_SH01", "05_video/S01_SH01.mp4", 4.0,
            track="voice", track_path="06_audio/dialogue/S01_SH01.wav",
            dialogue={"text": "hello", "speaker": "Caller"},
            characters=[{"name": "Mara", "reference": "assets/characters/Mara/reference.png"}],
        ),
    )

    stage = mux_audio_track(tmp_path, shot_path, "voice")

    assert stage["artifact"]["voice_muxed"] is True
    assert stage["history"][0]["superseded_reason"] == "voice_mux"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_mux_audio_track_layers_onto_existing_audio(tmp_path: Path):
    video_path = tmp_path / "05_video" / "S01_SH01.mp4"
    _make_tiny_video(video_path, duration=4.0, with_audio=True)
    sfx_path = tmp_path / "06_audio" / "sfx" / "S01_SH01.wav"
    _make_tiny_audio(sfx_path, duration=2.0)
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(
        shot_path,
        _shot_with_video_and_track(
            "S01_SH01", "05_video/S01_SH01.mp4", 4.0, track="sfx", track_path="06_audio/sfx/S01_SH01.wav",
        ),
    )

    stage = mux_audio_track(tmp_path, shot_path, "sfx")

    assert stage["version"] == 2
    assert stage["history"][0]["superseded_reason"] == "sfx_mux"
    assert stage["artifact"]["sfx_muxed"] is True
    new_path = tmp_path / stage["artifact"]["path"]
    assert new_path.exists()
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "default=noprint_wrappers=1", str(new_path)],
        capture_output=True, text=True,
    )
    assert probe.stdout.count("codec_type=audio") == 1  # mixed to one track, not stacked
    assert probe.stdout.count("codec_type=video") == 1
    duration_probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(new_path)],
        capture_output=True, text=True,
    )
    assert abs(float(duration_probe.stdout.strip()) - 4.0) < 0.5  # video duration stays authoritative


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_mux_audio_track_attaches_track_when_video_silent(tmp_path: Path):
    video_path = tmp_path / "05_video" / "S01_SH01.mp4"
    _make_tiny_video(video_path, duration=4.0, with_audio=False)
    sfx_path = tmp_path / "06_audio" / "sfx" / "S01_SH01.wav"
    _make_tiny_audio(sfx_path, duration=2.0)
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(
        shot_path,
        _shot_with_video_and_track(
            "S01_SH01", "05_video/S01_SH01.mp4", 4.0, track="sfx", track_path="06_audio/sfx/S01_SH01.wav",
        ),
    )

    stage = mux_audio_track(tmp_path, shot_path, "sfx")

    new_path = tmp_path / stage["artifact"]["path"]
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "default=noprint_wrappers=1", str(new_path)],
        capture_output=True, text=True,
    )
    assert "codec_type=audio" in probe.stdout


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_mux_audio_track_force_relocks(tmp_path: Path):
    video_path = tmp_path / "05_video" / "S01_SH01.mp4"
    _make_tiny_video(video_path, duration=4.0, with_audio=True)
    sfx_path = tmp_path / "06_audio" / "sfx" / "S01_SH01.wav"
    _make_tiny_audio(sfx_path, duration=2.0)
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(
        shot_path,
        _shot_with_video_and_track(
            "S01_SH01", "05_video/S01_SH01.mp4", 4.0, track="sfx", track_path="06_audio/sfx/S01_SH01.wav",
        ),
    )
    mux_audio_track(tmp_path, shot_path, "sfx")

    stage = mux_audio_track(tmp_path, shot_path, "sfx", force=True)

    assert stage["version"] == 3
    assert stage["artifact"]["sfx_muxed"] is True
