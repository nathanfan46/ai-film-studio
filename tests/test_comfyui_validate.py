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


def test_link_with_nonexistent_origin_node_is_reported_cleanly_not_a_crash():
    workflow = {
        "nodes": [{"id": 2, "type": "KSampler",
                   "inputs": [{"name": "model", "type": "MODEL", "link": 1}],
                   "widgets_values": [1, "fixed", 20, 8.0, "euler", "normal", 1]}],
        "links": [[1, 999, 0, 2, 0, "MODEL"]],  # origin_id 999 does not exist
    }
    result = validate_workflow(workflow)
    assert any("999" in e for e in result["errors"])


def test_out_of_range_origin_slot_is_reported_cleanly_not_a_crash():
    # validate_workflow's Level 1 pass already detects an out-of-range
    # origin_slot as an error -- but it used to then unconditionally run
    # the opaque-passthrough warnings pass afterward, which called
    # resolve_input_source on the same malformed link and crashed with an
    # unguarded IndexError before the already-computed Level 1 error could
    # ever reach the caller.
    workflow = {
        "nodes": [
            {"id": 1, "type": "SomeNode", "outputs": [{"name": "MODEL", "type": "MODEL", "links": [1]}]},
            {"id": 2, "type": "KSampler",
             "inputs": [{"name": "model", "type": "MODEL", "link": 1}],
             "widgets_values": [1, "fixed", 20, 8.0, "euler", "normal", 1]},
        ],
        "links": [[1, 1, 5, 2, 0, "MODEL"]],  # origin_slot 5, but node 1 only has slot 0
    }
    result = validate_workflow(workflow)
    assert any("out of range" in e for e in result["errors"])


def test_reroute_with_empty_inputs_in_validate_is_reported_cleanly_not_a_crash():
    workflow = {
        "nodes": [
            {"id": 1, "type": "Reroute", "inputs": [],
             "outputs": [{"name": "", "type": "MODEL", "links": [1]}]},
            {"id": 2, "type": "KSampler",
             "inputs": [{"name": "model", "type": "MODEL", "link": 1}],
             "widgets_values": [1, "fixed", 20, 8.0, "euler", "normal", 1]},
        ],
        "links": [[1, 1, 0, 2, 0, "MODEL"]],
    }
    result = validate_workflow(workflow)  # must not raise
    assert result["errors"] == []


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
