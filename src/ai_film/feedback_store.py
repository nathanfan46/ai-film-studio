# src/ai_film/feedback_store.py
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

VALID_TARGETS = ("video", "voice", "sfx", "music", "sync")


def feedback_path(project_dir: Path, shot_id: str) -> Path:
    return project_dir / "03_shots" / f"{shot_id}.feedback.json"


def load_feedback(project_dir: Path, shot_id: str) -> dict:
    path = feedback_path(project_dir, shot_id)
    if not path.exists():
        return {"shot_id": shot_id, "entries": []}
    return json.loads(path.read_text())


def save_feedback(project_dir: Path, shot_id: str, data: dict) -> None:
    path = feedback_path(project_dir, shot_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".feedback-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _next_feedback_id(data: dict) -> str:
    return f"FB-{len(data['entries']) + 1:03d}"


def add_feedback_entry(
    project_dir: Path,
    shot_id: str,
    target: str,
    note: str,
    at: float | None = None,
    range_start: float | None = None,
    range_end: float | None = None,
) -> dict:
    if target not in VALID_TARGETS:
        raise ValueError(f"target must be one of {VALID_TARGETS}, got {target!r}")
    if at is not None and (range_start is not None or range_end is not None):
        raise ValueError("give either `at` or a range, not both")
    if (range_start is None) != (range_end is None):
        raise ValueError("range_start and range_end must be given together")
    for value in (at, range_start, range_end):
        if value is not None and value < 0:
            raise ValueError(f"timestamps must be non-negative, got {value}")

    data = load_feedback(project_dir, shot_id)
    entry = {
        "id": _next_feedback_id(data),
        "target": target,
        "at": at,
        "range": {"start": range_start, "end": range_end} if range_start is not None else None,
        "note": note,
        "status": "open",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "resolved_at": None,
        "resolution": None,
    }
    data["entries"].append(entry)
    save_feedback(project_dir, shot_id, data)
    return entry


def resolve_feedback_entry(
    project_dir: Path, shot_id: str, feedback_id: str, resolution: str | None = None,
) -> dict:
    data = load_feedback(project_dir, shot_id)
    for entry in data["entries"]:
        if entry["id"] == feedback_id:
            entry["status"] = "resolved"
            entry["resolved_at"] = datetime.now(timezone.utc).isoformat()
            entry["resolution"] = resolution
            save_feedback(project_dir, shot_id, data)
            return entry
    raise ValueError(f"no feedback entry with id {feedback_id!r} for shot {shot_id!r}")
