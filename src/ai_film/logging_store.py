from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REDACT_KEY_PATTERN = re.compile(
    r"(authorization|api[_-]?key|token|secret|signed[_-]?url)", re.IGNORECASE
)
_REDACTED = "[REDACTED]"


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _REDACTED if _REDACT_KEY_PATTERN.search(key) else redact(val)
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


def write_attempt_log(
    project_dir: Path,
    shot_id: str,
    stage: str,
    attempt: int,
    job: dict | None,
    request: dict,
    response: dict,
    outcome: str,
) -> Path:
    log_dir = project_dir / "99_logs" / shot_id
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{_timestamp()}_{stage}_attempt{attempt:02d}.json"
    log_path.write_text(
        json.dumps(
            {
                "attempt": attempt,
                "stage": stage,
                "job": job,
                "outcome": outcome,
                "request": redact(request),
                "response": redact(response),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return log_path


def write_approval_log(
    project_dir: Path,
    scope: str,
    target_ids: list[str],
    estimated_cost: float | None,
) -> Path:
    log_dir = project_dir / "99_logs" / "approvals"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{_timestamp()}_generation_approved.json"
    log_path.write_text(
        json.dumps(
            {
                "scope": scope,
                "target_ids": target_ids,
                "estimated_cost": estimated_cost,
                "approved_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return log_path
