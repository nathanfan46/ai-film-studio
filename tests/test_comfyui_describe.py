# tests/test_comfyui_describe.py
from ai_film.comfyui.describe import describe_workflow, list_workflow_nodes


def _workflow():
    return {
        "nodes": [
            {"id": 1, "type": "CheckpointLoaderSimple",
             "outputs": [{"name": "MODEL", "type": "MODEL", "links": [1]}]},
            {"id": 2, "type": "KSampler",
             "inputs": [{"name": "model", "type": "MODEL", "link": 1}],
             "outputs": [{"name": "LATENT", "type": "LATENT", "links": []}]},
            {"id": 3, "type": "TotallyUnknownCustomNode", "widgets_values": []},
            {"id": 4, "type": "TotallyUnknownCustomNode", "widgets_values": []},
            {"id": 5, "type": "Note", "properties": {"text": ""}, "widgets_values": ["a generic author note"]},
        ],
        "links": [[1, 1, 0, 2, 0, "MODEL"]],
    }


def test_describe_groups_known_nodes_by_role():
    result = describe_workflow(_workflow())
    role_ids = {entry["id"] for entry in result["by_role"]["sampler"]}
    assert role_ids == {2}


def test_describe_compresses_unclassified_nodes_by_type_and_count():
    result = describe_workflow(_workflow())
    assert result["unclassified"] == {"TotallyUnknownCustomNode": 2}


def test_describe_surfaces_notes_verbatim():
    result = describe_workflow(_workflow())
    assert result["notes"] == [{"id": 5, "text": "a generic author note"}]


def test_list_workflow_nodes_includes_resolved_inputs():
    nodes = list_workflow_nodes(_workflow())
    ksampler = next(n for n in nodes if n["id"] == 2)
    assert ksampler["role"] == "sampler"
    assert ksampler["inputs"][0]["resolution"] == "direct"
    assert ksampler["inputs"][0]["source_node_id"] == 1


def test_list_workflow_nodes_filters_by_role():
    nodes = list_workflow_nodes(_workflow(), role="sampler")
    assert [n["id"] for n in nodes] == [2]


def test_list_workflow_nodes_never_compresses_duplicates():
    nodes = list_workflow_nodes(_workflow())
    unknown_ids = [n["id"] for n in nodes if n["type"] == "TotallyUnknownCustomNode"]
    assert unknown_ids == [3, 4]
