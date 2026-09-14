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
