from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ai_film.services.generation_service import archive_stage_artifact, project_relative_path
from ai_film.shot_store import load_shot, save_shot


def trim_video(project_dir: Path, shot_path: Path, end_seconds: float) -> dict:
    """Cut a completed video artifact down to its first `end_seconds` via
    ffmpeg (no provider spend) — same archive/version/history bookkeeping as
    any other video regeneration, so render (and everything downstream)
    picks up the trimmed clip automatically.

    Exists for video models with a hard minimum-duration floor (e.g.
    hailuo-2.3's 6s) that leaves trailing dead air past a short dialogue
    line — dead air a model's own generated performance can fill with
    unscripted movement unrelated to the real audio. Trimming the tail off
    removes both the excess pacing and whatever the model did during it.
    """
    if end_seconds <= 0:
        raise ValueError(f"end_seconds must be > 0, got {end_seconds}")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not installed or not on PATH")

    shot = load_shot(shot_path)
    shot_id = shot["id"]
    stage_data = shot["generation"].get("video", {"status": "pending"})
    if stage_data.get("status") != "completed" or not stage_data.get("artifact"):
        raise ValueError(f"shot {shot_id} has no completed video artifact to trim")

    current_duration = stage_data["artifact"].get("duration_seconds")
    if current_duration is not None and end_seconds >= current_duration:
        raise ValueError(
            f"end_seconds={end_seconds} is not shorter than the current duration "
            f"({current_duration}s) — nothing would be trimmed"
        )

    archive = archive_stage_artifact(project_dir, stage_data, "trim")
    if archive.archived_path is None:
        raise ValueError(f"artifact file for shot {shot_id}'s video stage is missing on disk")
    new_path = project_dir / stage_data["artifact"]["path"]  # now vacated by the archive step

    cmd = ["ffmpeg", "-y", "-i", str(archive.archived_path), "-t", str(end_seconds), str(new_path)]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        if archive.restore:
            archive.restore()
        stderr = exc.stderr.decode(errors="replace") if exc.stderr else ""
        raise RuntimeError(f"ffmpeg failed trimming video: {stderr}") from exc

    artifact = {
        **stage_data["artifact"],
        "path": project_relative_path(str(new_path), project_dir),
        "size_bytes": new_path.stat().st_size,
        "sha256": None,
        "duration_seconds": end_seconds,
    }
    shot["generation"]["video"] = {
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
    return shot["generation"]["video"]
