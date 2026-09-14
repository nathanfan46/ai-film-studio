# src/ai_film/comfyui/graph.py
"""Link resolution over a raw ComfyUI workflow dict: find a node by id,
and resolve what actually feeds a given input, transparently skipping
core Reroute hops (a fixed, trivial 1-in/1-out official node -- safe to
hard-code) and stopping at, but tagging, any node whose relevant output
socket is wildcard-typed ("*") -- a structural signal for an
unresolvable generic bundler (e.g. Crystools' "Pipe from any" nodes),
not a name/keyword match. See the spec's "Stage narration is the
agent's job, not Python's"."""

from __future__ import annotations

_MAX_REROUTE_HOPS = 50


def find_node(workflow: dict, node_id: int) -> dict | None:
    for node in workflow["nodes"]:
        if node["id"] == node_id:
            return node
    return None


def _find_link(workflow: dict, link_id: int) -> list | None:
    for link in workflow["links"]:
        if link[0] == link_id:
            return link
    return None


def resolve_input_source(workflow: dict, node_id: int, input_name: str) -> dict:
    node = find_node(workflow, node_id)
    if node is None:
        return {"resolution": "none"}
    matching_inputs = [i for i in node.get("inputs", []) if i["name"] == input_name]
    input_entry = matching_inputs[0] if matching_inputs else None
    if input_entry is None or input_entry.get("link") is None:
        return {"resolution": "none"}

    link_id = input_entry["link"]
    for _ in range(_MAX_REROUTE_HOPS):
        link = _find_link(workflow, link_id)
        if link is None:
            return {"resolution": "none"}
        origin_id, origin_slot = link[1], link[2]
        origin_node = find_node(workflow, origin_id)
        if origin_node is None:
            return {"resolution": "none"}
        output = origin_node["outputs"][origin_slot]

        if origin_node["type"] == "Reroute":
            reroute_input = origin_node["inputs"][0]
            if reroute_input.get("link") is None:
                return {"resolution": "none"}
            link_id = reroute_input["link"]
            continue

        if output.get("type") == "*":
            return {
                "resolution": "opaque_passthrough",
                "node_id": origin_id,
                "output_name": output["name"],
            }
        return {"resolution": "direct", "node_id": origin_id, "output_name": output["name"]}

    return {"resolution": "opaque_passthrough", "node_id": origin_id, "output_name": output["name"]}
