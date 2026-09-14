import json
from pathlib import Path

from ai_film.comfyui.describe import describe_workflow, list_workflow_nodes
from ai_film.comfyui.validate import validate_workflow
from ai_film.services.workflow_service import export_workflow, import_workflow, set_workflow_field_service

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _nodes_by_id(workflow: dict) -> dict:
    return {n["id"]: n for n in workflow["nodes"]}


def test_noop_roundtrip_is_structurally_identical_simple(tmp_path):
    workflows_dir = tmp_path / "workflows"
    import_workflow(workflows_dir, FIXTURES_DIR / "comfyui_workflow_simple.json", "flow")
    original = json.loads((FIXTURES_DIR / "comfyui_workflow_simple.json").read_text())
    out_path = tmp_path / "out.json"
    export_workflow(workflows_dir, "flow", out_path)
    exported = json.loads(out_path.read_text())
    assert _nodes_by_id(exported) == _nodes_by_id(original)
    assert sorted(exported["links"]) == sorted(original["links"])


def test_noop_roundtrip_is_structurally_identical_complex(tmp_path):
    workflows_dir = tmp_path / "workflows"
    import_workflow(workflows_dir, FIXTURES_DIR / "comfyui_workflow_complex.json", "flow")
    original = json.loads((FIXTURES_DIR / "comfyui_workflow_complex.json").read_text())
    out_path = tmp_path / "out.json"
    export_workflow(workflows_dir, "flow", out_path)
    exported = json.loads(out_path.read_text())
    assert _nodes_by_id(exported) == _nodes_by_id(original)


def test_modified_export_leaves_unrelated_nodes_untouched(tmp_path):
    workflows_dir = tmp_path / "workflows"
    import_workflow(workflows_dir, FIXTURES_DIR / "comfyui_workflow_complex.json", "flow")
    original = json.loads((FIXTURES_DIR / "comfyui_workflow_complex.json").read_text())
    set_workflow_field_service(workflows_dir, "flow", 2, "text", "a changed prompt")
    out_path = tmp_path / "out.json"
    export_workflow(workflows_dir, "flow", out_path)
    exported = json.loads(out_path.read_text())
    for node_id, node in _nodes_by_id(original).items():
        if node_id == 2:
            continue
        assert _nodes_by_id(exported)[node_id] == node, f"node {node_id} changed unexpectedly"
    assert _nodes_by_id(exported)[2]["widgets_values"] == ["a changed prompt"]


def test_complex_fixture_validates_clean(tmp_path):
    workflow = json.loads((FIXTURES_DIR / "comfyui_workflow_complex.json").read_text())
    result = validate_workflow(workflow)
    assert result["errors"] == []


def test_complex_fixture_describe_groups_livportrait_family_as_one_stage(tmp_path):
    workflow = json.loads((FIXTURES_DIR / "comfyui_workflow_complex.json").read_text())
    description = describe_workflow(workflow)
    facial = description["by_role"].get("facial_performance_transfer", [])
    assert len(facial) == 6  # two chains x 3 nodes each


def test_complex_fixture_disambiguates_duplicate_families_by_neighbors(tmp_path):
    workflow = json.loads((FIXTURES_DIR / "comfyui_workflow_complex.json").read_text())
    nodes = list_workflow_nodes(workflow, role="facial_performance_transfer")
    croppers = [n for n in nodes if n["type"] == "LivePortraitCropper"]
    assert len(croppers) == 2
    sources = {inp["source_node_id"] for n in croppers for inp in n["inputs"] if inp["resolution"] == "direct"}
    assert len(sources) == 2, "the two LivePortraitCropper chains must resolve to two distinct upstream sources"


def test_complex_fixture_tags_reroute_chain_as_direct_and_bundler_as_opaque(tmp_path):
    workflow = json.loads((FIXTURES_DIR / "comfyui_workflow_complex.json").read_text())
    nodes = list_workflow_nodes(workflow)
    resolutions = {inp["resolution"] for n in nodes for inp in n["inputs"]}
    assert "direct" in resolutions
    assert "opaque_passthrough" in resolutions
