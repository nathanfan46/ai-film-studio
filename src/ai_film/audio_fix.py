from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ai_film.services.generation_service import archive_stage_artifact, project_relative_path
from ai_film.shot_store import load_shot, save_shot

AUDIO_STAGES = ("voice", "sfx", "music")


def apply_audio_offset(project_dir: Path, shot_path: Path, track: str, offset_ms: float) -> dict:
    if track not in AUDIO_STAGES:
        raise ValueError(f"track must be one of {AUDIO_STAGES}, got {track!r}")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not installed or not on PATH")

    shot = load_shot(shot_path)
    shot_id = shot["id"]
    stage_data = shot["generation"].get(track, {"status": "pending"})
    if stage_data.get("status") != "completed" or not stage_data.get("artifact"):
        raise ValueError(f"shot {shot_id} has no completed {track!r} artifact to offset")

    current_duration = stage_data["artifact"].get("duration_seconds")
    if offset_ms < 0 and current_duration is not None and abs(offset_ms) / 1000 >= current_duration:
        raise ValueError(
            f"offset_ms={offset_ms} would trim past the track's duration "
            f"({current_duration}s) — nothing would be left"
        )

    archive = archive_stage_artifact(project_dir, stage_data, "audio_offset")
    if archive.archived_path is None:
        raise ValueError(
            f"artifact file for shot {shot_id}'s {track!r} stage is missing on disk"
        )
    new_path = project_dir / stage_data["artifact"]["path"]  # now vacated by the archive step

    if offset_ms >= 0:
        cmd = [
            "ffmpeg", "-y", "-i", str(archive.archived_path),
            "-af", f"adelay={offset_ms}:all=1", str(new_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-ss", str(abs(offset_ms) / 1000), "-i", str(archive.archived_path),
            str(new_path),
        ]

    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        if archive.restore:
            archive.restore()
        stderr = exc.stderr.decode(errors="replace") if exc.stderr else ""
        raise RuntimeError(f"ffmpeg failed applying audio offset: {stderr}") from exc

    artifact = {
        "path": project_relative_path(str(new_path), project_dir),
        "size_bytes": new_path.stat().st_size,
        "sha256": None,
        # NOTE: duration_seconds is pre-offset; adelay/trim changes actual file duration slightly, but not recomputed here (no current consumers)
        "duration_seconds": stage_data["artifact"].get("duration_seconds"),
    }
    shot["generation"][track] = {
        "provider": stage_data.get("provider"),
        "model": stage_data.get("model"),
        "status": "completed",
        "version": archive.version,
        "history": archive.history,
        "job": stage_data.get("job"),
        "inputs": stage_data.get("inputs", []),
        "artifact": artifact,
        "attempts": stage_data.get("attempts", 1),
    }
    save_shot(shot_path, shot)
    return shot["generation"][track]
