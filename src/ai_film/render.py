from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

from ai_film.shot_store import list_shot_paths, load_shot

_ASPECT_RATIO_TOLERANCE = 0.02
_DEFAULT_RESOLUTION = "1280x720"
_DEFAULT_FPS = 24


def build_manifest(project_dir: Path) -> dict:
    shots_dir = project_dir / "03_shots"
    shot_paths = list_shot_paths(shots_dir)
    shots = []
    for path in shot_paths:
        shot = load_shot(path)
        video = shot["generation"]["video"]
        artifact = video.get("artifact") or {}
        shots.append({
            "id": shot["id"],
            "video": artifact.get("path"),
            "duration": shot.get("duration_seconds"),
            "requested_format": artifact.get("requested_format"),
            "actual_format": artifact.get("actual_format"),
        })
    return {"shots": shots, "audio": [], "captions": []}


def preflight(manifest: dict, project_dir: Path) -> list[str]:
    errors: list[str] = []
    shots_dir = project_dir / "03_shots"
    resolved_root = project_dir.resolve()

    if not manifest["shots"]:
        return ["manifest contains no shots to render"]

    for entry in manifest["shots"]:
        shot_id = entry["id"]
        shot_path = shots_dir / f"{shot_id}.json"
        if not shot_path.exists():
            errors.append(f"{shot_id}: shot.json not found at {shot_path}")
            continue

        shot = load_shot(shot_path)
        if shot["generation"].get("video", {}).get("status") == "failed":
            errors.append(f"{shot_id}: video generation status is 'failed'")

        video_path_str = entry.get("video")
        if not video_path_str:
            errors.append(f"{shot_id}: no video artifact path in manifest")
            continue

        video_path = (project_dir / video_path_str).resolve()
        try:
            video_path.relative_to(resolved_root)
        except ValueError:
            errors.append(f"{shot_id}: artifact path escapes project directory: {video_path}")
            continue

        if not video_path.exists():
            errors.append(f"{shot_id}: artifact file does not exist: {video_path}")
        elif video_path.stat().st_size == 0:
            errors.append(f"{shot_id}: artifact file is empty: {video_path}")

    return errors


def _probe_duration(video_path: Path) -> float:
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
        ],
        capture_output=True, text=True,
    )
    return float(probe.stdout.strip())


def _probe_resolution(video_path: Path) -> tuple[int, int]:
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(video_path),
        ],
        capture_output=True, text=True,
    )
    width_str, height_str = probe.stdout.strip().split("x")
    return int(width_str), int(height_str)


def _probe_fps(video_path: Path) -> Fraction:
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate", "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True, text=True,
    )
    return Fraction(probe.stdout.strip())


def _has_audio_stream(video_path: Path) -> bool:
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a",
            "-show_entries", "stream=index", "-of", "csv=p=0", str(video_path),
        ],
        capture_output=True, text=True,
    )
    return bool(probe.stdout.strip())


def _parse_resolution(resolution: str) -> tuple[int, int]:
    width_str, height_str = resolution.lower().split("x")
    return int(width_str), int(height_str)


def _render_target(project_dir: Path) -> tuple[int, int, int]:
    """The final output format (see the design spec's Terminology
    section) — render's own unconditional normalization target, read
    directly from config.json's render section every call, independent of
    any individual shot's own target format."""
    config = json.loads((project_dir / "config.json").read_text())
    render_config = config.get("render", {})
    width, height = _parse_resolution(render_config.get("resolution", _DEFAULT_RESOLUTION))
    fps = render_config.get("fps", _DEFAULT_FPS)
    return width, height, fps


def preflight_warnings(manifest: dict, project_dir: Path) -> list[str]:
    """Non-fatal diagnostics: one line per shot showing its declared
    (generation-time target, if the artifact recorded one) vs. actual
    (ffprobe'd) format and audio presence, plus a final summary line for
    the render's own unconditional target. Never blocks — render()'s own
    normalization already handles every case shown here correctly. Only
    inspects shots whose artifact file already exists; preflight() (the
    blocking check) already reports a missing file."""
    if shutil.which("ffprobe") is None:
        return []
    lines: list[str] = []
    for entry in manifest["shots"]:
        video_path_str = entry.get("video")
        if not video_path_str:
            continue
        video_path = project_dir / video_path_str
        if not video_path.exists() or video_path.stat().st_size == 0:
            continue
        requested = entry.get("requested_format")
        actual = entry.get("actual_format")
        if requested and actual:
            declared = f"{requested['width']}x{requested['height']}@{requested.get('fps', '?')}"
            actual_str = f"{actual['width']}x{actual['height']}@{actual.get('fps', '?')}"
        else:
            width, height = _probe_resolution(video_path)
            fps = _probe_fps(video_path)
            declared = "unknown (generated before this shot recorded a format)"
            actual_str = f"{width}x{height}@{float(fps):.3f}"
        audio = "yes" if _has_audio_stream(video_path) else "no (padded with silence)"
        lines.append(f"{entry['id']}: declared {declared}, actual {actual_str}, audio: {audio}")
    if lines:
        width, height, fps = _render_target(project_dir)
        lines.append(f"final output format: {width}x{height}@{fps} (from config.json render.resolution/fps)")
    return lines


class RenderPreflightError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def render(project_dir: Path, manifest: dict, output_name: str = "reel_001.mp4") -> Path:
    errors = preflight(manifest, project_dir)
    if errors:
        raise RenderPreflightError(errors)

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("ffmpeg/ffprobe is not installed or not on PATH")

    width, height, fps = _render_target(project_dir)
    video_paths = [(project_dir / entry["video"]).resolve() for entry in manifest["shots"]]

    filter_parts = []
    video_labels = []
    audio_labels = []
    for i, video_path in enumerate(video_paths):
        filter_parts.append(
            f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[v{i}]"
        )
        video_labels.append(f"[v{i}]")
        if _has_audio_stream(video_path):
            filter_parts.append(
                f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo[a{i}]"
            )
        else:
            duration = _probe_duration(video_path)
            filter_parts.append(
                f"anullsrc=channel_layout=stereo:sample_rate=48000:duration={duration}[a{i}]"
            )
        audio_labels.append(f"[a{i}]")

    n = len(video_paths)
    concat_inputs = "".join(v + a for v, a in zip(video_labels, audio_labels))
    filter_parts.append(f"{concat_inputs}concat=n={n}:v=1:a=1[vout][aout]")
    filter_complex = ";".join(filter_parts)

    output_path = project_dir / "final" / output_name
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["ffmpeg", "-y"]
    for video_path in video_paths:
        cmd += ["-i", str(video_path)]
    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-c:a", "aac",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path
