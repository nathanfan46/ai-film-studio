# src/ai_film/candidate_store.py
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

_TARGET_KINDS = ("character", "env", "shot")


def target_dir(project_dir: Path, target: str) -> Path:
    parts = target.split(":")
    kind = parts[0]
    if kind == "character":
        if len(parts) != 2:
            raise ValueError(f"malformed character target {target!r}")
        return project_dir / "assets" / "characters" / parts[1]
    if kind == "env":
        if len(parts) != 2:
            raise ValueError(f"malformed env target {target!r}")
        return project_dir / "assets" / "environments" / parts[1]
    if kind == "shot":
        if len(parts) != 3:
            raise ValueError(f"malformed shot target {target!r}, expected shot:<id>:<stage>")
        shot_id, stage = parts[1], parts[2]
        if stage != "image":
            raise ValueError(
                f"unsupported shot target stage {stage!r} in {target!r}; "
                f"only 'image' is supported in this release"
            )
        return project_dir / "04_storyboard" / "candidates" / shot_id
    raise ValueError(f"unknown target kind {kind!r} in {target!r}; expected one of {_TARGET_KINDS}")


def scope_for_target(target: str) -> str:
    kind = target.split(":", 1)[0]
    return "bibles" if kind in ("character", "env") else "storyboard"


def _candidates_json_path(project_dir: Path, target: str) -> Path:
    return target_dir(project_dir, target) / "candidates.json"


def load_candidate_set(project_dir: Path, target: str) -> dict:
    path = _candidates_json_path(project_dir, target)
    if not path.exists():
        return {"target": target, "candidates": [], "selected": None}
    return json.loads(path.read_text())


def save_candidate_set(project_dir: Path, target: str, candidate_set: dict) -> None:
    directory = target_dir(project_dir, target)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "candidates.json"
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".candidates-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(candidate_set, handle, indent=2, ensure_ascii=False)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def next_candidate_id(candidate_set: dict) -> str:
    existing = [int(c["id"]) for c in candidate_set["candidates"]]
    return f"{(max(existing) + 1) if existing else 1:03d}"


def add_candidates(project_dir: Path, target: str, entries: list[dict]) -> dict:
    candidate_set = load_candidate_set(project_dir, target)
    candidate_set["candidates"].extend(entries)
    save_candidate_set(project_dir, target, candidate_set)
    return candidate_set


def get_candidate(candidate_set: dict, candidate_id: str) -> dict:
    for candidate in candidate_set["candidates"]:
        if candidate["id"] == candidate_id:
            return candidate
    raise ValueError(f"no candidate with id {candidate_id!r}")
