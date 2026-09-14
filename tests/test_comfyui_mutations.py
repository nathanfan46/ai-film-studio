import pytest

from ai_film.comfyui.mutations import set_workflow_field, set_workflow_raw


def _ksampler_workflow():
    return {
        "nodes": [
            {"id": 2, "type": "CLIPTextEncode", "widgets_values": ["old prompt"]},
            {"id": 5, "type": "KSampler", "widgets_values": [42, "fixed", 20, 8.0, "euler", "normal", 1]},
            {"id": 99, "type": "TotallyUnknownCustomNode", "widgets_values": [1, 2, 3]},
        ],
        "links": [],
    }


def test_set_workflow_field_updates_array_addressed_value():
    workflow = _ksampler_workflow()
    summary = set_workflow_field(workflow, 2, "text", "a new prompt")
    node = next(n for n in workflow["nodes"] if n["id"] == 2)
    assert node["widgets_values"] == ["a new prompt"]
    assert "2" in summary and "text" in summary


def test_set_workflow_field_updates_a_middle_widget_position():
    workflow = _ksampler_workflow()
    set_workflow_field(workflow, 5, "steps", 30)
    node = next(n for n in workflow["nodes"] if n["id"] == 5)
    assert node["widgets_values"] == [42, "fixed", 30, 8.0, "euler", "normal", 1]


def test_set_workflow_field_rejects_unregistered_node():
    workflow = _ksampler_workflow()
    with pytest.raises(ValueError, match="not in the known-node registry"):
        set_workflow_field(workflow, 99, "anything", "x")


def test_set_workflow_field_raises_schema_mismatch_never_a_silent_wrong_write():
    workflow = _ksampler_workflow()
    node = next(n for n in workflow["nodes"] if n["id"] == 5)
    node["widgets_values"] = [42, "fixed", 20, 8.0]  # only 4 of KSampler's 7 declared widgets
    with pytest.raises(ValueError, match="schema mismatch"):
        set_workflow_field(workflow, 5, "sampler_name", "euler")
    assert node["widgets_values"] == [42, "fixed", 20, 8.0]  # untouched


def test_set_workflow_raw_writes_by_array_index():
    workflow = _ksampler_workflow()
    set_workflow_raw(workflow, 99, "changed", index=1)
    node = next(n for n in workflow["nodes"] if n["id"] == 99)
    assert node["widgets_values"] == [1, "changed", 3]


def test_set_workflow_raw_writes_by_dict_key():
    workflow = {"nodes": [{"id": 41, "type": "VHS_LoadVideo", "widgets_values": {"video": "old.mp4"}}], "links": []}
    set_workflow_raw(workflow, 41, "new.mp4", key="video")
    node = next(n for n in workflow["nodes"] if n["id"] == 41)
    assert node["widgets_values"]["video"] == "new.mp4"


def test_set_workflow_raw_requires_exactly_one_of_index_or_key():
    workflow = _ksampler_workflow()
    with pytest.raises(ValueError, match="exactly one"):
        set_workflow_raw(workflow, 99, "x")
    with pytest.raises(ValueError, match="exactly one"):
        set_workflow_raw(workflow, 99, "x", index=0, key="video")


def test_set_workflow_raw_rejects_out_of_range_index_never_a_silent_wrong_write():
    workflow = _ksampler_workflow()
    node = next(n for n in workflow["nodes"] if n["id"] == 99)
    with pytest.raises(ValueError, match="no widgets_values index"):
        set_workflow_raw(workflow, 99, "x", index=10)
    assert node["widgets_values"] == [1, 2, 3]  # untouched, no IndexError leaked


def test_set_workflow_raw_rejects_nonexistent_dict_key_never_a_phantom_write():
    workflow = {"nodes": [{"id": 41, "type": "VHS_LoadVideo", "widgets_values": {"video": "old.mp4"}}], "links": []}
    node = next(n for n in workflow["nodes"] if n["id"] == 41)
    with pytest.raises(ValueError, match="no widgets_values key"):
        set_workflow_raw(workflow, 41, "b.mp4", key="typo_key")
    assert node["widgets_values"] == {"video": "old.mp4"}  # untouched, no phantom key, no KeyError leaked


from ai_film.comfyui.mutations import remove_workflow_node, rewire_workflow_link


def _rewire_workflow():
    return {
        "last_link_id": 2,
        "nodes": [
            {"id": 1, "type": "CheckpointLoaderSimple",
             "outputs": [{"name": "MODEL", "type": "MODEL", "links": [1]}]},
            {"id": 2, "type": "CheckpointLoaderSimple",
             "outputs": [{"name": "MODEL", "type": "MODEL", "links": []}]},
            {"id": 3, "type": "KSampler",
             "inputs": [{"name": "model", "type": "MODEL", "link": 1}]},
        ],
        "links": [[1, 1, 0, 3, 0, "MODEL"]],
    }


def test_rewire_points_target_input_at_new_source():
    workflow = _rewire_workflow()
    rewire_workflow_link(workflow, target_node_id=3, target_input="model",
                          source_node_id=2, source_output="MODEL")
    target = next(n for n in workflow["nodes"] if n["id"] == 3)
    new_link_id = target["inputs"][0]["link"]
    assert new_link_id != 1
    new_link = next(l for l in workflow["links"] if l[0] == new_link_id)
    assert new_link[1:5] == [2, 0, 3, 0]
    old_source = next(n for n in workflow["nodes"] if n["id"] == 1)
    assert 1 not in old_source["outputs"][0]["links"]
    new_source = next(n for n in workflow["nodes"] if n["id"] == 2)
    assert new_link_id in new_source["outputs"][0]["links"]
    assert not any(l[0] == 1 for l in workflow["links"])  # old link removed


def _bypass_workflow():
    return {
        "last_link_id": 2,
        "nodes": [
            {"id": 1, "type": "CheckpointLoaderSimple",
             "outputs": [{"name": "MODEL", "type": "MODEL", "links": [1]}]},
            {"id": 2, "type": "LoraLoaderModelOnly",
             "inputs": [{"name": "model", "type": "MODEL", "link": 1}],
             "outputs": [{"name": "MODEL", "type": "MODEL", "links": [2]}],
             "widgets_values": ["x.safetensors", 1.0]},
            {"id": 3, "type": "KSampler",
             "inputs": [{"name": "model", "type": "MODEL", "link": 2}]},
        ],
        "links": [[1, 1, 0, 2, 0, "MODEL"], [2, 2, 0, 3, 0, "MODEL"]],
    }


def test_remove_with_bypass_reconnects_around_the_removed_node():
    workflow = _bypass_workflow()
    remove_workflow_node(workflow, 2, bypass=True)
    assert not any(n["id"] == 2 for n in workflow["nodes"])
    target = next(n for n in workflow["nodes"] if n["id"] == 3)
    new_link_id = target["inputs"][0]["link"]
    new_link = next(l for l in workflow["links"] if l[0] == new_link_id)
    assert new_link[1:5] == [1, 0, 3, 0]


def test_remove_without_bypass_leaves_downstream_input_disconnected():
    workflow = _bypass_workflow()
    remove_workflow_node(workflow, 2, bypass=False)
    assert not any(n["id"] == 2 for n in workflow["nodes"])
    target = next(n for n in workflow["nodes"] if n["id"] == 3)
    assert target["inputs"][0]["link"] is None


def test_remove_bypass_declines_when_no_unambiguous_pair_exists():
    workflow = _bypass_workflow()
    # give the node a second, differently-typed output so there's no
    # single unambiguous MODEL-in/MODEL-out pair anymore
    workflow["nodes"][1]["outputs"].append({"name": "EXTRA", "type": "EXTRA", "links": []})
    summary = remove_workflow_node(workflow, 2, bypass=True)
    target = next(n for n in workflow["nodes"] if n["id"] == 3)
    assert target["inputs"][0]["link"] is None  # behaved like bypass=False
    assert "did not bypass" in summary.lower()


def test_remove_bypass_works_on_a_completely_unclassified_node_type():
    """Bypass eligibility depends only on socket types matching, never on
    Layer A/B classification -- an unrecognized custom node type must
    bypass exactly as readily as a known one, since remove_workflow_node
    never looks at node["type"] to decide eligibility."""
    workflow = _bypass_workflow()
    workflow["nodes"][1]["type"] = "SomeNeverBeforeSeenCustomNode"
    remove_workflow_node(workflow, 2, bypass=True)
    target = next(n for n in workflow["nodes"] if n["id"] == 3)
    new_link = next(l for l in workflow["links"] if l[0] == target["inputs"][0]["link"])
    assert new_link[1:5] == [1, 0, 3, 0]


def test_remove_bypass_handles_dangling_input_link_without_crashing():
    # A malformed/hand-edited workflow can have an input "link" field that
    # doesn't correspond to any entry in workflow["links"]. The bypass path
    # must degrade to a plain removal instead of raising StopIteration.
    workflow = _bypass_workflow()
    node = next(n for n in workflow["nodes"] if n["id"] == 2)
    node["inputs"][0]["link"] = 999
    summary = remove_workflow_node(workflow, 2, bypass=True)
    assert not any(n["id"] == 2 for n in workflow["nodes"])
    target = next(n for n in workflow["nodes"] if n["id"] == 3)
    assert target["inputs"][0]["link"] is None
    assert "removed node 2" in summary


def test_remove_bypass_pair_exists_but_output_has_no_downstream_links():
    # The removed node has an unambiguous type-matching pair, but its
    # matched output has no downstream links to reconnect -- must not
    # crash and must not claim "no unambiguous pair" in the summary.
    workflow = _bypass_workflow()
    node = next(n for n in workflow["nodes"] if n["id"] == 2)
    node["outputs"][0]["links"] = []
    summary = remove_workflow_node(workflow, 2, bypass=True)
    assert not any(n["id"] == 2 for n in workflow["nodes"])
    assert "no unambiguous" not in summary.lower()
