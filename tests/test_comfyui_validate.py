from ai_film.comfyui.validate import validate_workflow


def _minimal_valid_workflow():
    return {
        "nodes": [
            {"id": 1, "type": "CheckpointLoaderSimple",
             "outputs": [{"name": "MODEL", "type": "MODEL", "links": [1]}],
             "widgets_values": ["x.safetensors"]},
            {"id": 2, "type": "KSampler",
             "inputs": [{"name": "model", "type": "MODEL", "link": 1}],
             "widgets_values": [1, "fixed", 20, 8.0, "euler", "normal", 1]},
        ],
        "links": [[1, 1, 0, 2, 0, "MODEL"]],
    }


def test_valid_workflow_has_no_errors():
    result = validate_workflow(_minimal_valid_workflow())
    assert result["errors"] == []


def test_dangling_link_reference_is_an_error():
    workflow = _minimal_valid_workflow()
    workflow["nodes"][1]["inputs"][0]["link"] = 999  # no link 999 exists
    result = validate_workflow(workflow)
    assert any("999" in e for e in result["errors"])


def test_type_mismatched_link_is_an_error():
    workflow = _minimal_valid_workflow()
    workflow["nodes"][1]["inputs"][0]["type"] = "CONDITIONING"  # was MODEL
    result = validate_workflow(workflow)
    assert any("type" in e.lower() for e in result["errors"])


def test_wildcard_type_is_never_a_mismatch():
    workflow = _minimal_valid_workflow()
    workflow["nodes"][1]["inputs"][0]["type"] = "*"
    result = validate_workflow(workflow)
    assert result["errors"] == []


def test_negative_ksampler_steps_is_a_level_2_error():
    workflow = _minimal_valid_workflow()
    workflow["nodes"][1]["widgets_values"][2] = -5  # steps
    result = validate_workflow(workflow)
    assert any("steps" in e for e in result["errors"])


def test_unclassified_node_is_a_warning_not_an_error():
    workflow = _minimal_valid_workflow()
    workflow["nodes"].append({"id": 3, "type": "TotallyUnknownCustomNode", "widgets_values": []})
    result = validate_workflow(workflow)
    assert result["errors"] == []
    assert any("3" in w for w in result["warnings"])


def test_opaque_passthrough_link_is_a_warning():
    workflow = _minimal_valid_workflow()
    workflow["nodes"].append({
        "id": 3, "type": "Pipe from any [Crystools]",
        "outputs": [{"name": "any_1", "type": "*", "links": [2]}],
    })
    workflow["nodes"][1]["inputs"][0]["link"] = 2
    workflow["links"].append([2, 3, 0, 2, 0, "*"])
    result = validate_workflow(workflow)
    assert result["errors"] == []
    assert any("opaque" in w.lower() for w in result["warnings"])
