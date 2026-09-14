# src/ai_film/comfyui/describe.py
"""Two views of the same annotated graph for two different purposes:
describe_workflow compresses unclassified nodes by type+count so a
427-node workflow doesn't dump hundreds of lines -- the human-facing
summary. list_workflow_nodes never compresses -- the full-detail
substrate an agent reasons over to find exact targets, including
duplicate node families it must disambiguate by resolved neighbors, not
id. See the spec's "describe-workflow should probably not be too
compressed" discussion."""

from __future__ import annotations

from ai_film.comfyui.graph import resolve_input_source
from ai_film.comfyui.roles import infer_role


def describe_workflow(workflow: dict) -> dict:
    by_role: dict[str, list[dict]] = {}
    unclassified: dict[str, int] = {}
    notes: list[dict] = []

    for node in workflow["nodes"]:
        role = infer_role(node["type"])
        if node["type"] == "Note":
            values = node.get("widgets_values")
            text = values[0] if values else ""
            notes.append({"id": node["id"], "text": text})
            continue
        if role is None:
            unclassified[node["type"]] = unclassified.get(node["type"], 0) + 1
            continue
        by_role.setdefault(role, []).append({"id": node["id"], "type": node["type"]})

    return {"by_role": by_role, "unclassified": unclassified, "notes": notes}


def list_workflow_nodes(workflow: dict, role: str | None = None) -> list[dict]:
    entries = []
    for node in workflow["nodes"]:
        node_role = infer_role(node["type"])
        if role is not None and node_role != role:
            continue
        resolved_inputs = []
        for inp in node.get("inputs", []):
            if inp.get("link") is None:
                resolved_inputs.append({"name": inp["name"], "resolution": "none"})
                continue
            result = resolve_input_source(workflow, node["id"], inp["name"])
            resolved_inputs.append({
                "name": inp["name"],
                "resolution": result["resolution"],
                "source_node_id": result.get("node_id"),
                "source_output_name": result.get("output_name"),
            })
        entries.append({
            "id": node["id"],
            "type": node["type"],
            "role": node_role,
            "inputs": resolved_inputs,
            "outputs": [{"name": o["name"], "type": o["type"]} for o in node.get("outputs", [])],
        })
    return entries
