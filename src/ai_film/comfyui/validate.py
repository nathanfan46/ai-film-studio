"""validate_workflow: Level 1 (graph integrity) and Level 2 (known-node
field shape) as errors that block export/mutation; unclassified nodes
and opaque-passthrough connections as warnings that never block. Level
3 (are required custom nodes/models actually installed) is explicitly
out of scope for V1. See the spec's "validate_workflow" section."""

from __future__ import annotations

from ai_film.comfyui.roles import infer_role
from ai_film.comfyui.widget_addressing import resolve_field_position

_LEVEL_2_CHECKS = {
    ("KSampler", "steps"): lambda v: isinstance(v, int) and v > 0,
    ("KSampler", "cfg"): lambda v: isinstance(v, (int, float)) and v > 0,
    ("CLIPTextEncode", "text"): lambda v: isinstance(v, str),
}


def _nodes_by_id(workflow: dict) -> dict[int, dict]:
    return {n["id"]: n for n in workflow["nodes"]}


def _links_by_id(workflow: dict) -> dict[int, list]:
    return {link[0]: link for link in workflow["links"]}


def _validate_level_1(workflow: dict, errors: list[str]) -> None:
    nodes = _nodes_by_id(workflow)
    links = _links_by_id(workflow)

    for node in workflow["nodes"]:
        for slot_index, inp in enumerate(node.get("inputs", [])):
            link_id = inp.get("link")
            if link_id is None:
                continue
            link = links.get(link_id)
            if link is None:
                errors.append(f"node {node['id']} input {inp['name']!r} references missing link {link_id}")
                continue
            _, origin_id, origin_slot, target_id, target_slot, _ = link
            if target_id != node["id"] or target_slot != slot_index:
                errors.append(f"link {link_id} target does not match node {node['id']} input {slot_index}")
                continue
            origin_node = nodes.get(origin_id)
            if origin_node is None:
                errors.append(f"link {link_id} origin node {origin_id} does not exist")
                continue
            origin_outputs = origin_node.get("outputs", [])
            if origin_slot >= len(origin_outputs):
                errors.append(f"link {link_id} origin slot {origin_slot} out of range on node {origin_id}")
                continue
            origin_type = origin_outputs[origin_slot].get("type")
            target_type = inp.get("type")
            if origin_type not in ("*", target_type) and target_type != "*":
                errors.append(
                    f"link {link_id} connects incompatible types "
                    f"{origin_type!r} -> {target_type!r}"
                )


def _validate_level_2(workflow: dict, errors: list[str], warnings: list[str]) -> None:
    for node in workflow["nodes"]:
        for (node_type, field), check in _LEVEL_2_CHECKS.items():
            if node["type"] != node_type:
                continue
            position = resolve_field_position(node, field)
            if position["mode"] == "schema_mismatch":
                warnings.append(f"node {node['id']} ({node_type}) widget schema doesn't match registry for {field!r}")
                continue
            if position["mode"] != "widget":
                continue
            values = node.get("widgets_values", [])
            value = values[position["index"]] if position["addressing"] == "array" else values[position["key"]]
            if not check(value):
                errors.append(f"node {node['id']} ({node_type}) field {field!r} has an invalid value: {value!r}")


def _validate_classification_warnings(workflow: dict, warnings: list[str]) -> None:
    from ai_film.comfyui.known_nodes import KNOWN_NODE_REGISTRY

    for node in workflow["nodes"]:
        if node["type"] in KNOWN_NODE_REGISTRY:
            continue
        if infer_role(node["type"]) is None:
            warnings.append(f"node {node['id']} ({node['type']}) has no inferred role")


def _validate_opaque_passthrough_warnings(workflow: dict, warnings: list[str]) -> None:
    from ai_film.comfyui.graph import resolve_input_source

    for node in workflow["nodes"]:
        for inp in node.get("inputs", []):
            if inp.get("link") is None:
                continue
            result = resolve_input_source(workflow, node["id"], inp["name"])
            if result["resolution"] == "opaque_passthrough":
                warnings.append(
                    f"node {node['id']} input {inp['name']!r} resolves through an "
                    f"opaque passthrough at node {result['node_id']}"
                )


def validate_workflow(workflow: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    _validate_level_1(workflow, errors)
    _validate_level_2(workflow, errors, warnings)
    _validate_classification_warnings(workflow, warnings)
    _validate_opaque_passthrough_warnings(workflow, warnings)
    return {"errors": errors, "warnings": warnings}
