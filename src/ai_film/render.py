from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ai_film.shot_store import load_shot


def build_manifest(project_dir: Path) -> dict:
    shots_dir = project_dir / "03_shots"
    shot_paths = sorted(shots_dir.glob("*.json"))
    shots = []
    for path in shot_paths:
        shot = load_shot(path)
        video = shot["generation"]["video"]
        artifact = video.get("artifact")
        shots.append({
            "id": shot["id"],
            "video": artifact["path"] if artifact else None,
            "duration": shot.get("duration_seconds"),
        })
    return {"shots": shots, "audio": [], "captions": []}


def preflight(manifest: dict, project_dir: Path) -> list[str]:
    errors: list[str] = []
    shots_dir = project_dir / "03_shots"
    resolved_root = project_dir.resolve()

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


class RenderPreflightError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def render(project_dir: Path, manifest: dict, output_name: str = "reel_001.mp4") -> Path:
    errors = preflight(manifest, project_dir)
    if errors:
        raise RenderPreflightError(errors)

    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not installed or not on PATH")

    concat_list_path = project_dir / "99_logs" / "_render_concat_list.txt"
    concat_list_path.parent.mkdir(parents=True, exist_ok=True)
    with concat_list_path.open("w") as handle:
        for entry in manifest["shots"]:
            video_path = (project_dir / entry["video"]).resolve()
            handle.write(f"file '{video_path.as_posix()}'\n")

    output_path = project_dir / "final" / output_name
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_list_path), "-c", "copy", str(output_path),
        ],
        check=True, capture_output=True,
    )
    return output_path
