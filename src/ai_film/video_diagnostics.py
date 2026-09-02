from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ai_film.services.generation_service import project_relative_path
from ai_film.shot_store import load_shot

_SILENCE_NOISE_DB = "-30dB"
_SILENCE_MIN_DURATION = "0.1"


def _probe_duration(video_path: Path) -> float:
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
        ],
        capture_output=True, text=True,
    )
    return float(probe.stdout.strip())


def _has_audio_stream(video_path: Path) -> bool:
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a",
            "-show_entries", "stream=index", "-of", "csv=p=0", str(video_path),
        ],
        capture_output=True, text=True,
    )
    return bool(probe.stdout.strip())


def _silence_windows(video_path: Path) -> list[dict] | None:
    """Silence windows in the video's own audio track, or None if it has no
    audio stream at all (e.g. a video pre-lipsync)."""
    if not _has_audio_stream(video_path):
        return None
    result = subprocess.run(
        [
            "ffmpeg", "-i", str(video_path), "-af",
            f"silencedetect=noise={_SILENCE_NOISE_DB}:d={_SILENCE_MIN_DURATION}",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    windows = []
    start: float | None = None
    for line in result.stderr.splitlines():
        if "silence_start" in line:
            start = float(line.rsplit("silence_start:", 1)[1].strip())
        elif "silence_end" in line and start is not None:
            end = float(line.split("silence_end:", 1)[1].split("|", 1)[0].strip())
            windows.append({"start": start, "end": end})
            start = None
    return windows


def _extract_frames(video_path: Path, out_dir: Path, interval_seconds: float, duration: float) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("frame_*.png"):
        stale.unlink()
    frames = []
    t = 0.0
    while t < duration:
        frame_path = out_dir / f"frame_{t:.2f}s.png"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", str(video_path), "-frames:v", "1", str(frame_path)],
            check=True, capture_output=True,
        )
        frames.append({"t": round(t, 2), "path": frame_path})
        t += interval_seconds
    return frames


def extract_last_frame(video_path: Path, output_path: Path) -> None:
    """Extract a video's final frame to output_path via ffmpeg. Seeks to
    just before the very end, not exactly at it — seeking to or past a
    video's exact last timestamp routinely yields a black or corrupted
    frame with some encoders/containers."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("ffmpeg/ffprobe is not installed or not on PATH")
    duration = _probe_duration(video_path)
    timestamp = max(duration - 0.1, 0.0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", str(timestamp), "-i", str(video_path),
                "-frames:v", "1", str(output_path),
            ],
            check=True, capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors="replace") if exc.stderr else ""
        raise RuntimeError(f"ffmpeg failed extracting last frame: {stderr}") from exc


def diagnose_video(project_dir: Path, shot_path: Path, interval_seconds: float = 0.5) -> dict:
    """Extract frames from a shot's current video at a fixed interval and
    report the silence windows detected in its own audio track — a
    repeatable stand-in for the frame-by-frame-plus-silencedetect check
    used throughout this project's lipsync debugging to tell whether
    on-screen mouth movement actually lines up with real spoken audio,
    instead of eyeballing timestamps by hand each time.

    Frames land under 07_review/diagnostics/<shot_id>/ so a caller with a
    Read tool can inspect them directly by path; this function only
    extracts and reports, it does not judge what's in the frames."""
    if interval_seconds <= 0:
        raise ValueError(f"interval_seconds must be > 0, got {interval_seconds}")
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("ffmpeg/ffprobe is not installed or not on PATH")

    shot = load_shot(shot_path)
    shot_id = shot["id"]
    video_artifact = shot.get("generation", {}).get("video", {}).get("artifact")
    if not video_artifact or not video_artifact.get("path"):
        raise ValueError(f"shot {shot_id} has no video artifact to diagnose")

    video_path = project_dir / video_artifact["path"]
    duration = _probe_duration(video_path)
    out_dir = project_dir / "07_review" / "diagnostics" / shot_id
    frames = _extract_frames(video_path, out_dir, interval_seconds, duration)
    silence_windows = _silence_windows(video_path)

    return {
        "shot_id": shot_id,
        "video_path": project_relative_path(str(video_path), project_dir),
        "duration_seconds": duration,
        "frames": [
            {"t": f["t"], "path": project_relative_path(str(f["path"]), project_dir)} for f in frames
        ],
        "silence_windows": silence_windows,
    }
