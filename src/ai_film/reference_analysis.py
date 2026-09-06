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


_SCENE_THRESHOLD = 0.35


def _detect_scene_cuts(video_path: Path, threshold: float = _SCENE_THRESHOLD) -> list[float]:
    """Scene-cut timestamps (seconds), NOT including 0.0. ffmpeg's
    showinfo filter logs one line per frame selected by the scene
    expression; each such line's pts_time is a cut boundary. Verified
    empirically against a real synthetic 2-scene clip during spec
    design — showinfo does log pts_time correctly for this filter."""
    result = subprocess.run(
        [
            "ffmpeg", "-i", str(video_path),
            "-vf", f"select='gt(scene,{threshold})',showinfo",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    cuts = []
    for line in result.stderr.splitlines():
        if "pts_time:" not in line:
            continue
        cuts.append(float(line.split("pts_time:", 1)[1].split()[0]))
    return sorted(set(cuts))


def _scenes_from_cuts(cut_timestamps: list[float], total_duration: float) -> list[tuple[float, float]]:
    boundaries = sorted(set(cut_timestamps) | {0.0, total_duration})
    return [
        (boundaries[i], boundaries[i + 1])
        for i in range(len(boundaries) - 1)
        if boundaries[i + 1] > boundaries[i]
    ]


_LOW_MAX = 0.02
_MEDIUM_MAX = 0.08


def _scene_scores(video_path: Path) -> list[tuple[float, float]]:
    """Every frame's raw ffmpeg scene-change score (0.0-1.0), before any
    cut threshold is applied. showinfo does NOT print this value —
    verified empirically during spec design against real ffmpeg 9.0.1
    output — it must come from the metadata filter's print mode, which
    emits a two-line block per frame: "frame:N pts:P pts_time:T" then
    "lavfi.scene_score=X" on the next line."""
    result = subprocess.run(
        [
            "ffmpeg", "-i", str(video_path),
            "-vf", "select='gte(scene,0)',metadata=print:key=lavfi.scene_score",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    scores: list[tuple[float, float]] = []
    pending_time: float | None = None
    for line in result.stderr.splitlines():
        if "pts_time:" in line:
            pending_time = float(line.split("pts_time:", 1)[1].split()[0])
        elif "lavfi.scene_score=" in line and pending_time is not None:
            scores.append((pending_time, float(line.split("lavfi.scene_score=", 1)[1].strip())))
            pending_time = None
    return scores


def _mean_interior_score(scene_start: float, scene_end: float, scores: list[tuple[float, float]]) -> float:
    """Mean score of frames strictly between a scene's boundaries — the
    boundary frames themselves are cut frames (score spikes near 1.0 by
    definition) and must not pull a static scene's average up."""
    interior = [score for t, score in scores if scene_start < t < scene_end]
    if not interior:
        return 0.0
    return sum(interior) / len(interior)


def _visual_change_level(mean_score: float) -> str:
    # Heuristic starting points, not measured constants — expect these
    # to be revisited once real reference footage has been run through
    # this, per the design spec.
    if mean_score < _LOW_MAX:
        return "low"
    if mean_score <= _MEDIUM_MAX:
        return "medium"
    return "high"


def _extract_keyframe(video_path: Path, timestamp: float, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(timestamp), "-i", str(video_path), "-frames:v", "1", str(output_path)],
        check=True, capture_output=True,
    )


_KEYFRAME_START_OFFSET = 0.1
_KEYFRAME_MIDPOINT_MIN_DURATION = 3.0


def _copy_source(source_path: Path, ref_dir: Path) -> Path:
    ext = source_path.suffix or ".mp4"
    dest_path = ref_dir / f"source{ext}"
    shutil.copyfile(source_path, dest_path)
    return dest_path


def analyze_reference_video(project_dir: Path, source_path: Path, force: bool = False) -> dict:
    """Copy source_path into assets/reference-video/, run scene detection
    + motion-signal bucketing + keyframe extraction, write and return
    video_analysis_brief.json's contents. Pure local ffmpeg — never
    touches a fal.ai endpoint. See the design spec's "Ownership
    invariant" for the approved/force refusal rules below."""
    if not source_path.exists():
        raise ValueError(f"source video not found: {source_path}")

    ref_dir = project_dir / "assets" / "reference-video"
    brief_path = ref_dir / "video_analysis_brief.json"
    if brief_path.exists():
        existing = json.loads(brief_path.read_text())
        if existing.get("approved"):
            raise RuntimeError(
                f"{brief_path} is already approved — move or rename it "
                "before re-running analysis"
            )
        if not force:
            raise RuntimeError(f"{brief_path} already exists — pass --force to overwrite")

    _require_ffmpeg()

    ref_dir.mkdir(parents=True, exist_ok=True)
    keyframes_dir = ref_dir / "keyframes"
    keyframes_dir.mkdir(parents=True, exist_ok=True)

    dest_path = _copy_source(source_path, ref_dir)
    duration = _probe_duration(dest_path)
    stream_info = _probe_stream_info(dest_path)

    cut_timestamps = _detect_scene_cuts(dest_path)
    scene_bounds = _scenes_from_cuts(cut_timestamps, duration)
    scores = _scene_scores(dest_path)

    scenes = []
    for index, (start, end) in enumerate(scene_bounds):
        level = _visual_change_level(_mean_interior_score(start, end, scores))

        keyframe_paths = []
        start_kf = keyframes_dir / f"scene{index:02d}_start.jpg"
        _extract_keyframe(dest_path, start + _KEYFRAME_START_OFFSET, start_kf)
        keyframe_paths.append(project_relative_path(str(start_kf), project_dir))
        if end - start > _KEYFRAME_MIDPOINT_MIN_DURATION:
            mid_kf = keyframes_dir / f"scene{index:02d}_mid.jpg"
            _extract_keyframe(dest_path, start + (end - start) / 2, mid_kf)
            keyframe_paths.append(project_relative_path(str(mid_kf), project_dir))

        scenes.append({
            "scene_index": index,
            "start_seconds": round(start, 2),
            "end_seconds": round(end, 2),
            "keyframes": keyframe_paths,
            "visual_change_level": level,
            "description": None,
            "subject": None,
            "subject_motion": None,
            "camera": None,
            "motion_transfer_candidate": None,
        })

    brief = {
        "schema_version": "1.0",
        "approved": False,
        "source": {
            "original_filename": source_path.name,
            "path": project_relative_path(str(dest_path), project_dir),
            "duration_seconds": round(duration, 2),
            "resolution": stream_info["resolution"],
            "fps": stream_info["fps"],
        },
        "scenes": scenes,
        "analysis_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scene_threshold": _SCENE_THRESHOLD,
            "change_level_thresholds": {"low_max": _LOW_MAX, "medium_max": _MEDIUM_MAX},
        },
    }
    brief_path.write_text(json.dumps(brief, indent=2, ensure_ascii=False))
    return brief
