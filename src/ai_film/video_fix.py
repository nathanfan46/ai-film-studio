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


MUXABLE_TRACKS = ("voice", "sfx")


def mux_audio_track(project_dir: Path, shot_path: Path, track: str, force: bool = False) -> dict:
    """Mix a shot's current `track` artifact (voice or sfx) into its
    current video artifact via ffmpeg (no provider spend), superseding the
    video with the muxed result — same archive/version/history bookkeeping
    as any other video regeneration. Layers the track onto whatever audio
    the video already has via ffmpeg's `amix`, never replacing it; the
    video's own duration stays authoritative — the track is padded/trimmed
    to match, never the reverse. Requires an already-generated,
    already-reviewed artifact for `track` — never generates one itself, so
    `generate-{voice,sfx}` and this stay separate, deliberate steps: a
    track gets its timing corrected on its own (via `apply-audio-offset`)
    before it's locked into the video.

    `track="sfx"` is for shot-bound event SFX (a phone ringing, a door
    slam) — sound that belongs to exactly this shot and never needs to
    move relative to other shots. Scene ambience and film-level music are
    not this: they span multiple shots and can only be placed once the
    picture is assembled, so they stay a final-render-time concern, never
    a per-shot mux — never reuse this for either.

    `track="voice"` is for dialogue that must be audible in the shot but
    was never lip-synced to anyone on screen — an off-screen speaker (a
    phone call the on-screen character is listening to, a narrator): the
    line has a completed voice artifact, but `generate-lipsync` was never
    (and should never be) run, since there's no on-screen mouth to match
    it to. If the dialogue's speaker IS one of this shot's on-screen
    `characters`, this raises instead of muxing — that combination needs
    `generate-lipsync`, which also syncs mouth movement; muxing raw voice
    onto an on-screen speaker would play the line with no mouth movement
    to match it, an obviously wrong result `--force` should only override
    with real intent to skip lip-sync deliberately.

    Mux input is always the CURRENT `generation.video.artifact`, which may
    already be lipsynced — deliberately different from what SFX
    *generation* itself reads (the pre-lipsync base video, so ThinkSound's
    own unverified audio-mixing behavior never has to touch real dialogue
    audio). Generation needs the silent/base picture to analyze; muxing
    needs whatever the shot's actual current video is, since that's what
    has to become the shot's real final output.
    """
    if track not in MUXABLE_TRACKS:
        raise ValueError(f"track must be one of {MUXABLE_TRACKS}, got {track!r}")
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("ffmpeg/ffprobe is not installed or not on PATH")

    shot = load_shot(shot_path)
    shot_id = shot["id"]
    video_stage = shot["generation"].get("video", {"status": "pending"})
    if video_stage.get("status") != "completed" or not video_stage.get("artifact"):
        raise ValueError(f"shot {shot_id} has no completed video artifact to mux {track} into")
    track_stage = shot["generation"].get(track, {"status": "pending"})
    track_artifact = track_stage.get("artifact")
    if track_stage.get("status") != "completed" or not track_artifact or not track_artifact.get("path"):
        raise ValueError(
            f"shot {shot_id} has no completed {track} artifact to mux — run generate-{track} first"
        )
    if track == "voice":
        speaker = shot.get("dialogue", {}).get("speaker")
        on_screen = {c.get("name") for c in shot.get("characters", [])}
        if speaker and speaker in on_screen and not force:
            raise ValueError(
                f"shot {shot_id}'s dialogue speaker {speaker!r} is on screen in this shot — "
                f"use generate-lipsync instead, which also syncs mouth movement to the voice; "
                f"pass force=True only if you deliberately want the raw voice muxed in without it"
            )
    muxed_flag = f"{track}_muxed"
    if video_stage["artifact"].get(muxed_flag) and not force:
        raise ValueError(
            f"shot {shot_id}'s current video already has {track} muxed in "
            f"— pass force=True to deliberately re-mux"
        )

    archive = archive_stage_artifact(project_dir, video_stage, f"{track}_mux")
    if archive.archived_path is None:
        raise ValueError(f"artifact file for shot {shot_id}'s video stage is missing on disk")
    new_path = project_dir / video_stage["artifact"]["path"]  # now vacated by the archive step
    track_path = project_dir / track_artifact["path"]
    video_duration = video_stage["artifact"].get("duration_seconds") or _probe_duration(
        archive.archived_path
    )

    if _has_audio_stream(archive.archived_path):
        cmd = [
            "ffmpeg", "-y", "-i", str(archive.archived_path), "-i", str(track_path),
            "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=0[aout]",
            "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-t", str(video_duration),
            str(new_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-i", str(archive.archived_path), "-i", str(track_path),
            "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-shortest", "-t", str(video_duration),
            str(new_path),
        ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        if archive.restore:
            archive.restore()
        stderr = exc.stderr.decode(errors="replace") if exc.stderr else ""
        raise RuntimeError(f"ffmpeg failed muxing {track} into video: {stderr}") from exc

    artifact = {
        **video_stage["artifact"],
        "path": project_relative_path(str(new_path), project_dir),
        "size_bytes": new_path.stat().st_size,
        "sha256": None,
        muxed_flag: True,
    }
    shot["generation"]["video"] = {
        "provider": video_stage.get("provider"),
        "model": video_stage.get("model"),
        "status": "completed",
        "version": archive.version,
        "history": archive.history,
        "job": video_stage.get("job"),
        "inputs": video_stage.get("inputs", []),
        "artifact": artifact,
        "attempts": video_stage.get("attempts", 1),
    }
    save_shot(shot_path, shot)
    return shot["generation"]["video"]
