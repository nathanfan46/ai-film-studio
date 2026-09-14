"""Pure, in-memory graph-surgery primitives -- each mutates the given
workflow dict in place and returns a one-line summary, or raises
ValueError with a specific, actionable message. Callers (workflow_service
in Task 12) are responsible for validate-then-write-only-on-success and
logging; these functions never touch disk. See the spec's "Three
mutation primitives" and "Atomicity and the mutation log"."""

from __future__ import annotations

from ai_film.comfyui.graph import find_node
from ai_film.comfyui.widget_addressing import resolve_field_position


def set_workflow_field(workflow: dict, node_id: int, field: str, value) -> str:
    node = find_node(workflow, node_id)
    if node is None:
        raise ValueError(f"no node with id {node_id}")
    position = resolve_field_position(node, field)

    if position["mode"] == "not_registered":
        raise ValueError(
            f"node {node_id} ({node['type']}) is not in the known-node registry, "
            f"or has no field {field!r} -- use set_workflow_raw instead"
        )
    if position["mode"] == "converted_input":
        raise ValueError(
            f"field {field!r} on node {node_id} has been converted to an input "
            f"socket named {position['input_name']!r} -- use rewire_workflow_link instead"
        )
    if position["mode"] == "schema_mismatch":
        raise ValueError(
            f"schema mismatch: node {node_id} ({node['type']})'s widget layout "
            f"doesn't match the known-node registry for field {field!r} (expected "
            f"{position['expected']}, found {position['actual_widget_count']} widgets) "
            f"-- use set_workflow_raw instead"
        )

    if position["addressing"] == "array":
        node["widgets_values"][position["index"]] = value
    else:
        node["widgets_values"][position["key"]] = value
    return f"set node {node_id} ({node['type']}).{field} = {value!r}"


def set_workflow_raw(workflow: dict, node_id: int, value, index: int | None = None, key: str | None = None) -> str:
    if (index is None) == (key is None):
        raise ValueError("set_workflow_raw requires exactly one of index or key")
    node = find_node(workflow, node_id)
    if node is None:
        raise ValueError(f"no node with id {node_id}")

    widgets = node.get("widgets_values")
    if index is not None:
        if not isinstance(widgets, list) or not (0 <= index < len(widgets)):
            found = len(widgets) if isinstance(widgets, list) else "a non-array widgets_values"
            raise ValueError(f"node {node_id} ({node['type']}) has no widgets_values index {index} (found {found})")
        widgets[index] = value
        return f"set node {node_id} ({node['type']}).widgets_values[{index}] = {value!r} (raw)"

    if not isinstance(widgets, dict) or key not in widgets:
        found = list(widgets) if isinstance(widgets, dict) else "a non-dict widgets_values"
        raise ValueError(f"node {node_id} ({node['type']}) has no widgets_values key {key!r} (found keys: {found})")
    widgets[key] = value
    return f"set node {node_id} ({node['type']}).widgets_values[{key!r}] = {value!r} (raw)"


def _next_link_id(workflow: dict) -> int:
    existing = [link[0] for link in workflow["links"]]
    next_id = max([workflow.get("last_link_id", 0)] + existing) + 1
    workflow["last_link_id"] = next_id
    return next_id


def _remove_link(workflow: dict, link_id: int) -> None:
    link = next((l for l in workflow["links"] if l[0] == link_id), None)
    if link is None:
        return
    _, origin_id, origin_slot, target_id, target_slot, _ = link
    workflow["links"] = [l for l in workflow["links"] if l[0] != link_id]
    origin_node = find_node(workflow, origin_id)
    if origin_node is not None:
        origin_node["outputs"][origin_slot]["links"] = [
            lid for lid in origin_node["outputs"][origin_slot].get("links") or [] if lid != link_id
        ]
    target_node = find_node(workflow, target_id)
    if target_node is not None and target_slot < len(target_node.get("inputs", [])):
        if target_node["inputs"][target_slot].get("link") == link_id:
            target_node["inputs"][target_slot]["link"] = None


def _add_link(workflow: dict, source_node_id: int, source_slot: int, target_node_id: int, target_slot: int, link_type: str) -> int:
    link_id = _next_link_id(workflow)
    workflow["links"].append([link_id, source_node_id, source_slot, target_node_id, target_slot, link_type])
    source_node = find_node(workflow, source_node_id)
    source_node["outputs"][source_slot].setdefault("links", [])
    if source_node["outputs"][source_slot]["links"] is None:
        source_node["outputs"][source_slot]["links"] = []
    source_node["outputs"][source_slot]["links"].append(link_id)
    target_node = find_node(workflow, target_node_id)
    target_node["inputs"][target_slot]["link"] = link_id
    return link_id


def rewire_workflow_link(workflow: dict, target_node_id: int, target_input: str, source_node_id: int, source_output: str) -> str:
    target_node = find_node(workflow, target_node_id)
    if target_node is None:
        raise ValueError(f"no node with id {target_node_id}")
    source_node = find_node(workflow, source_node_id)
    if source_node is None:
        raise ValueError(f"no node with id {source_node_id}")

    target_slot = next((i for i, inp in enumerate(target_node.get("inputs", [])) if inp["name"] == target_input), None)
    if target_slot is None:
        raise ValueError(f"node {target_node_id} ({target_node['type']}) has no input named {target_input!r}")
    source_slot = next((i for i, out in enumerate(source_node.get("outputs", [])) if out["name"] == source_output), None)
    if source_slot is None:
        raise ValueError(f"node {source_node_id} ({source_node['type']}) has no output named {source_output!r}")

    old_link_id = target_node["inputs"][target_slot].get("link")
    if old_link_id is not None:
        _remove_link(workflow, old_link_id)

    link_type = source_node["outputs"][source_slot]["type"]
    _add_link(workflow, source_node_id, source_slot, target_node_id, target_slot, link_type)
    return (
        f"rewired node {target_node_id} ({target_node['type']}).{target_input} <- "
        f"node {source_node_id} ({source_node['type']}).{source_output}"
    )


def _find_bypass_pair(node: dict) -> tuple[int, int] | None:
    # "Exactly one input/output pair sharing the same socket type" means
    # exactly one input AND exactly one output overall -- not just exactly
    # one *matching-type* combination. Counting only matching combinations
    # would let an unrelated extra socket of a different type slip through
    # as "still unambiguous", which defeats the point of requiring the node
    # to have a single, unambiguous through-path before auto-reconnecting.
    inputs = node.get("inputs", [])
    outputs = node.get("outputs", [])
    if len(inputs) != 1 or len(outputs) != 1:
        return None
    inp, out = inputs[0], outputs[0]
    if inp.get("link") is not None and inp["type"] == out["type"]:
        return (0, 0)
    return None


def remove_workflow_node(workflow: dict, node_id: int, bypass: bool = False) -> str:
    node = find_node(workflow, node_id)
    if node is None:
        raise ValueError(f"no node with id {node_id}")

    pair = _find_bypass_pair(node) if bypass else None
    bypass_source = None
    bypass_targets: list[tuple[int, int]] = []
    link_type = None
    if pair is not None:
        in_slot, out_slot = pair
        in_link_id = node["inputs"][in_slot]["link"]
        # The input's "link" field is only a claimed reference -- a malformed
        # or hand-edited workflow can point it at a link id that isn't
        # actually in workflow["links"]. Look it up defensively rather than
        # assuming it resolves, so a dangling reference degrades to "can't
        # safely bypass" instead of an unhandled StopIteration.
        in_link = next((l for l in workflow["links"] if l[0] == in_link_id), None)
        if in_link is not None:
            bypass_source = (in_link[1], in_link[2])
            link_type = node["outputs"][out_slot]["type"]
            for out_link_id in node["outputs"][out_slot].get("links") or []:
                out_link = next((l for l in workflow["links"] if l[0] == out_link_id), None)
                if out_link is not None:
                    bypass_targets.append((out_link[3], out_link[4]))

    touching_link_ids = {
        l[0] for l in workflow["links"] if l[1] == node_id or l[3] == node_id
    }
    for link_id in touching_link_ids:
        _remove_link(workflow, link_id)
    workflow["nodes"] = [n for n in workflow["nodes"] if n["id"] != node_id]

    if bypass_source is not None and bypass_targets:
        for target_id, target_slot in bypass_targets:
            _add_link(workflow, bypass_source[0], bypass_source[1], target_id, target_slot, link_type)
        return f"removed node {node_id} ({node['type']}) and bypassed {len(bypass_targets)} connection(s) around it"

    if bypass and pair is not None:
        # There was an unambiguous type-matching pair, but nothing was
        # actually reconnected -- either the matched input's link was
        # dangling, or the matched output had no downstream links to begin
        # with. Say so specifically rather than reusing the "no unambiguous
        # pair" message, which would misdescribe why nothing was bypassed.
        suffix = " (nothing to bypass -- matched pair had no live connections to reconnect)"
    elif bypass:
        suffix = " (did not bypass -- no unambiguous type-matching input/output pair)"
    else:
        suffix = ""
    return f"removed node {node_id} ({node['type']}){suffix}"
