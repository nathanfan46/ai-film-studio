"""Resolve a Layer B semantic field name to its actual position on one
specific node instance -- never a fixed index, because a widget can be
"converted to an input socket" in the ComfyUI UI, which removes it from
widgets_values and shifts every later widget's position. See the
spec's "The widget-addressing problem"."""

from __future__ import annotations

from ai_film.comfyui.known_nodes import KNOWN_NODE_REGISTRY


def resolve_field_position(node: dict, field: str) -> dict:
    entry = KNOWN_NODE_REGISTRY.get(node["type"])
    if entry is None or field not in entry["widgets"]:
        return {"mode": "not_registered"}

    converted_inputs = {
        inp["widget"]["name"]
        for inp in node.get("inputs", [])
        if inp.get("widget") and inp.get("link") is not None
    }
    if field in converted_inputs:
        return {"mode": "converted_input", "input_name": field}

    remaining_widgets = [w for w in entry["widgets"] if w not in converted_inputs]
    position = remaining_widgets.index(field)
    widgets_values = node.get("widgets_values", [])

    if entry["addressing"] == "array":
        if len(remaining_widgets) != len(widgets_values):
            return {
                "mode": "schema_mismatch",
                "expected": remaining_widgets,
                "actual_widget_count": len(widgets_values),
            }
        return {"mode": "widget", "addressing": "array", "index": position}

    # dict-addressed
    if not all(w in widgets_values for w in remaining_widgets):
        return {
            "mode": "schema_mismatch",
            "expected": remaining_widgets,
            "actual_widget_count": len(widgets_values),
        }
    return {"mode": "widget", "addressing": "dict", "key": field}
