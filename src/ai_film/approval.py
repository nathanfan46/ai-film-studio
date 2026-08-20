from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ai_film.logging_store import write_approval_log

VALID_SCOPES = ("bibles", "storyboard")


def _config_path(project_dir: Path) -> Path:
    return project_dir / "config.json"


def load_config(project_dir: Path) -> dict:
    return json.loads(_config_path(project_dir).read_text())


def save_config(project_dir: Path, config: dict) -> None:
    _config_path(project_dir).write_text(json.dumps(config, indent=2, ensure_ascii=False))


def approve_generation(
    project_dir: Path,
    scope: str,
    target_ids: list[str],
    estimated_cost: float | None = None,
    revision: int | None = None,
) -> dict:
    if scope not in VALID_SCOPES:
        raise ValueError(f"unknown approval scope: {scope!r}, must be one of {VALID_SCOPES}")

    config = load_config(project_dir)
    scope_record: dict = {
        "type": "bibles" if scope == "bibles" else "storyboard_revision",
        "target_ids": list(target_ids),
    }
    if scope == "storyboard":
        scope_record["revision"] = revision or 1

    config.setdefault("generation_approval", {})[scope] = {
        "approved": True,
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "scope": scope_record,
        "estimated_cost": estimated_cost,
    }
    write_approval_log(project_dir, scope, list(target_ids), estimated_cost)
    save_config(project_dir, config)
    return config["generation_approval"][scope]


def is_approved(project_dir: Path, scope: str, target_id: str) -> bool:
    config = load_config(project_dir)
    record = config.get("generation_approval", {}).get(scope)
    if not record or not record.get("approved"):
        return False
    return target_id in record.get("scope", {}).get("target_ids", [])
