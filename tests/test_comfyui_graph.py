# tests/test_comfyui_graph.py
from ai_film.comfyui.graph import find_node, resolve_input_source


def _workflow_with_reroute_chain():
    return {
        "nodes": [
            {"id": 1, "type": "CheckpointLoaderSimple",
             "outputs": [{"name": "MODEL", "type": "MODEL", "links": [1]}]},
            {"id": 2, "type": "Reroute",
             "inputs": [{"name": "", "type": "*", "link": 1}],
             "outputs": [{"name": "", "type": "MODEL", "links": [2]}]},
            {"id": 3, "type": "Reroute",
             "inputs": [{"name": "", "type": "*", "link": 2}],
             "outputs": [{"name": "", "type": "MODEL", "links": [3]}]},
            {"id": 4, "type": "KSampler",
             "inputs": [{"name": "model", "type": "MODEL", "link": 3}]},
        ],
        "links": [
            [1, 1, 0, 2, 0, "MODEL"],
            [2, 2, 0, 3, 0, "MODEL"],
            [3, 3, 0, 4, 0, "MODEL"],
        ],
    }


def test_find_node_by_id():
    workflow = _workflow_with_reroute_chain()
    assert find_node(workflow, 1)["type"] == "CheckpointLoaderSimple"
    assert find_node(workflow, 999) is None


def test_resolves_through_a_reroute_chain_as_direct():
    workflow = _workflow_with_reroute_chain()
    result = resolve_input_source(workflow, 4, "model")
    assert result == {"resolution": "direct", "node_id": 1, "output_name": "MODEL"}


def test_unlinked_input_returns_none_resolution():
    workflow = {"nodes": [{"id": 1, "type": "KSampler", "inputs": [{"name": "model", "type": "MODEL", "link": None}]}], "links": []}
    assert resolve_input_source(workflow, 1, "model") == {"resolution": "none"}


def test_wildcard_typed_output_stops_at_opaque_passthrough():
    workflow = {
        "nodes": [
            {"id": 1, "type": "CheckpointLoaderSimple",
             "outputs": [{"name": "MODEL", "type": "MODEL", "links": [1]}]},
            {"id": 2, "type": "Pipe from any [Crystools]",
             "inputs": [{"name": "any_1", "type": "*", "link": 1}],
             "outputs": [{"name": "any_1", "type": "*", "links": [2]}]},
            {"id": 3, "type": "KSampler",
             "inputs": [{"name": "model", "type": "MODEL", "link": 2}]},
        ],
        "links": [[1, 1, 0, 2, 0, "MODEL"], [2, 2, 0, 3, 0, "*"]],
    }
    result = resolve_input_source(workflow, 3, "model")
    assert result == {"resolution": "opaque_passthrough", "node_id": 2, "output_name": "any_1"}
