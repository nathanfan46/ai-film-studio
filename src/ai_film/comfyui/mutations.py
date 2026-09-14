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
