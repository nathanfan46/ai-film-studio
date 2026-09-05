"""Local, zero-cost reference-video analysis: scene cuts, a coarse
per-scene motion signal, and keyframes, extracted entirely via ffmpeg —
see docs/superpowers/specs/2026-09-04-reference-video-analysis-design.md.
No provider abstraction here; this never calls a fal.ai endpoint."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ai_film.services.generation_service import project_relative_path

_FFMPEG_MISSING_MSG = "ffmpeg/ffprobe is not installed or not on PATH"


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError(_FFMPEG_MISSING_MSG)


def _probe_duration(video_path: Path) -> float:
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
        ],
        capture_output=True, text=True,
    )
    return float(probe.stdout.strip())


def _probe_stream_info(video_path: Path) -> dict:
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate",
            "-of", "json", str(video_path),
        ],
        capture_output=True, text=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    num, den = stream["r_frame_rate"].split("/")
    fps = round(float(num) / float(den), 3) if float(den) else 0.0
    return {"resolution": f"{stream['width']}x{stream['height']}", "fps": fps}
