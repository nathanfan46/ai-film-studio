from __future__ import annotations

import json
from pathlib import Path

from ai_film.schema import validate_shot

REQUIRED_STAGES = ("image", "video", "voice", "sfx", "music")
_ACTIVE = ("queued", "running")
_DONE = ("completed", "not_required")


def list_shot_paths(shots_dir: Path) -> list[Path]:
    """List all shot.json files in a directory, excluding feedback.json files."""
    return sorted(p for p in shots_dir.glob("*.json") if not p.name.endswith(".feedback.json"))


def compute_status(shot: dict) -> str:
    continuity_status = shot.get("continuity", {}).get("status", "pending")
    generation = shot.get("generation", {})
    stage_statuses = [
        generation.get(stage, {}).get("status", "pending") for stage in REQUIRED_STAGES
    ]

    if any(status == "failed" for status in stage_statuses):
        return "failed"
    if any(status in _ACTIVE for status in stage_statuses):
        return "generating"
    if all(status in _DONE for status in stage_statuses):
        return "completed"
    if continuity_status == "passed":
        return "ready"
    return "draft"


def load_shot(path: Path) -> dict:
    return json.loads(path.read_text())


def save_shot(path: Path, shot: dict) -> None:
    shot = dict(shot)
    shot["status"] = compute_status(shot)
    errors = validate_shot(shot)
    if errors:
        raise ValueError(f"invalid shot.json for {shot.get('id')}: {errors}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(shot, indent=2, ensure_ascii=False))


def previous_shot_id(shot_id: str) -> str | None:
    """The immediately preceding shot id in the same scene, or None if
    shot_id is already a scene's first shot."""
    scene, num_str = shot_id.split("_SH")
    num = int(num_str)
    return f"{scene}_SH{num - 1:02d}" if num > 1 else None


def previous_shot_image_reference(project_dir: Path, shot_id: str) -> str | None:
    """The immediately preceding shot's locked storyboard image, same scene,
    if one exists and is already completed — None for a scene's first shot,
    or if the preceding shot has no locked image yet."""
    prev_id = previous_shot_id(shot_id)
    if prev_id is None:
        return None
    prev_path = project_dir / "03_shots" / f"{prev_id}.json"
    if not prev_path.exists():
        return None
    prev_shot = load_shot(prev_path)
    artifact = prev_shot.get("generation", {}).get("image", {}).get("artifact")
    return artifact["path"] if artifact else None
