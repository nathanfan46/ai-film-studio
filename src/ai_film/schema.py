from __future__ import annotations

import jsonschema

GENERATION_STAGE_SCHEMA = {
    "type": "object",
    "required": ["status"],
    "properties": {
        "status": {
            "enum": [
                "pending", "queued", "running", "completed", "failed", "not_required",
            ]
        },
        "provider": {"type": ["string", "null"]},
        "model": {"type": ["string", "null"]},
        "version": {"type": "integer"},
        "history": {"type": "array"},
        "job": {"type": ["object", "null"]},
        "inputs": {"type": "array"},
        "artifact": {"type": ["object", "null"]},
        "attempts": {"type": "integer"},
        "created_at": {"type": ["string", "null"]},
        "started_at": {"type": ["string", "null"]},
        "completed_at": {"type": ["string", "null"]},
    },
}

SHOT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["schema_version", "id", "status", "duration_seconds", "generation"],
    "properties": {
        "schema_version": {"type": "string"},
        "id": {"type": "string"},
        "status": {"enum": ["draft", "ready", "generating", "completed", "failed"]},
        "duration_seconds": {"type": "number"},
        "characters": {"type": "array"},
        "inputs": {"type": "object"},
        "camera": {"type": "object"},
        "action": {"type": "string"},
        "dialogue": {"type": "object"},
        "visual": {"type": "object"},
        "continuity": {
            "type": "object",
            "required": ["status"],
            "properties": {
                "status": {"enum": ["pending", "passed", "warning", "failed"]},
                "checked_at": {"type": ["string", "null"]},
                "issues": {"type": "array"},
            },
        },
        "generation": {
            "type": "object",
            "required": ["image", "video", "voice", "sfx", "music"],
            "properties": {
                "image": GENERATION_STAGE_SCHEMA,
                "video": GENERATION_STAGE_SCHEMA,
                "voice": GENERATION_STAGE_SCHEMA,
                "sfx": GENERATION_STAGE_SCHEMA,
                "music": GENERATION_STAGE_SCHEMA,
            },
        },
    },
}


def validate_shot(data: dict) -> list[str]:
    validator = jsonschema.Draft7Validator(SHOT_SCHEMA)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    return [
        f"{'.'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors
    ]
