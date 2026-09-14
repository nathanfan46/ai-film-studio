"""Load a ComfyUI workflow JSON file, rejecting the two shapes this
project doesn't target: the API/prompt format (no nodes[]/links[], used
only for /prompt execution) and the newer v1.0 schema variant
(object-shaped links, "state" instead of last_node_id/last_link_id).
See docs/superpowers/specs/2026-09-11-comfyui-workflow-interop-design.md."""

from __future__ import annotations

import json
from pathlib import Path


def load_workflow_json(path: Path) -> dict:
    if not path.exists():
        raise ValueError(f"workflow file not found: {path}")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc

    if "nodes" not in data or "links" not in data:
        raise ValueError(
            f"{path} looks like the ComfyUI API/prompt format (node-id-keyed "
            "class_type/inputs), not the workflow format this tool needs. "
            "In ComfyUI, use Save (not Save API Format) to get a workflow file."
        )
    if "state" in data or "version" in data and data["version"] == 1:
        raise ValueError(
            f"{path} uses the newer ComfyUI v1.0 workflow schema (object-shaped "
            "links, 'state' instead of last_node_id/last_link_id), which this "
            "tool doesn't support yet."
        )
    return data
