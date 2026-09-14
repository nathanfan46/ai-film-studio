import json
from pathlib import Path

import pytest

from ai_film.services.workflow_service import (
    export_workflow,
    import_workflow,
    load_stored_workflow,
    remove_workflow_node_service,
    rewire_workflow_link_service,
    set_workflow_field_service,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def test_import_workflow_rejects_invalid_id(tmp_path):
    with pytest.raises(ValueError, match="invalid workflow id"):
        import_workflow(tmp_path, FIXTURES_DIR / "comfyui_workflow_simple.json", "../evil")


def test_import_workflow_stores_at_workflows_id_workflow_json(tmp_path):
    import_workflow(tmp_path, FIXTURES_DIR / "comfyui_workflow_simple.json", "my-flow")
    stored = tmp_path / "my-flow" / "workflow.json"
    assert stored.exists()
    assert json.loads(stored.read_text())["last_node_id"] == 7


def test_import_workflow_rejects_api_format(tmp_path):
    with pytest.raises(ValueError):
        import_workflow(tmp_path, FIXTURES_DIR / "comfyui_workflow_api_format.json", "bad-flow")
    assert not (tmp_path / "bad-flow").exists()


def test_export_workflow_writes_to_the_given_path(tmp_path):
    import_workflow(tmp_path, FIXTURES_DIR / "comfyui_workflow_simple.json", "my-flow")
    out_path = tmp_path / "exported.json"
    export_workflow(tmp_path, "my-flow", out_path)
    assert json.loads(out_path.read_text())["last_node_id"] == 7


def test_successful_mutation_writes_and_logs_applied(tmp_path):
    import_workflow(tmp_path, FIXTURES_DIR / "comfyui_workflow_simple.json", "my-flow")
    result = set_workflow_field_service(tmp_path, "my-flow", 2, "text", "a new prompt")
    assert "2" in result["summary"]
    stored = load_stored_workflow(tmp_path, "my-flow")
    node = next(n for n in stored["nodes"] if n["id"] == 2)
    assert node["widgets_values"] == ["a new prompt"]
    logs = list((tmp_path / "my-flow" / "99_logs" / "mutations").glob("*_set-workflow-field_attempt01.json"))
    assert len(logs) == 1
    assert json.loads(logs[0].read_text())["outcome"] == "applied"


def test_failed_mutation_leaves_stored_file_untouched_and_logs_rejected(tmp_path):
    import_workflow(tmp_path, FIXTURES_DIR / "comfyui_workflow_simple.json", "my-flow")
    before = (tmp_path / "my-flow" / "workflow.json").read_text()
    with pytest.raises(ValueError):
        set_workflow_field_service(tmp_path, "my-flow", 999, "text", "x")  # node 999 doesn't exist
    after = (tmp_path / "my-flow" / "workflow.json").read_text()
    assert before == after
    logs = list((tmp_path / "my-flow" / "99_logs" / "mutations").glob("*_set-workflow-field_attempt01.json"))
    assert json.loads(logs[0].read_text())["outcome"] == "rejected"


def test_remove_node_service_end_to_end(tmp_path):
    import_workflow(tmp_path, FIXTURES_DIR / "comfyui_workflow_simple.json", "my-flow")
    remove_workflow_node_service(tmp_path, "my-flow", 3, bypass=False)  # negative CLIPTextEncode
    stored = load_stored_workflow(tmp_path, "my-flow")
    assert not any(n["id"] == 3 for n in stored["nodes"])


# --- Fix 1: workflow-id traversal guard on load_stored_workflow ---


def test_load_stored_workflow_rejects_traversal_id(tmp_path):
    # Regression: load_stored_workflow used to build a path from a raw
    # workflow_id with no validation at all -- only import_workflow
    # validated. Every other function (export_workflow, the mutation
    # services) calls load_stored_workflow first, so this single guard
    # covers all of them transitively. Assert it never even touches a path
    # outside workflows_dir: no directories should be created as a
    # side effect of the attempt.
    with pytest.raises(ValueError, match="invalid workflow id"):
        load_stored_workflow(tmp_path, "../evil")
    assert list(tmp_path.iterdir()) == []


def test_export_workflow_rejects_traversal_id_via_load_stored_workflow(tmp_path):
    with pytest.raises(ValueError, match="invalid workflow id"):
        export_workflow(tmp_path, "../evil", tmp_path / "out.json")


# --- Fix 5: a mutation that succeeds in-memory but is rejected by
# validate_workflow afterward must also leave the stored file untouched
# (the `if result["errors"]:` branch in _apply_mutation -- structurally
# different from a mutation that fails inside the pure function itself). ---


def test_mutation_rejected_by_post_validation_leaves_stored_file_untouched(tmp_path):
    import_workflow(tmp_path, FIXTURES_DIR / "comfyui_workflow_simple.json", "my-flow")
    before = (tmp_path / "my-flow" / "workflow.json").read_text()

    # node 5 (KSampler)'s "positive" input is CONDITIONING-typed; rewiring it
    # to node 1 (CheckpointLoaderSimple)'s MODEL output succeeds at the pure
    # rewire_workflow_link level (both nodes/sockets exist), but produces a
    # type-mismatched link that validate_workflow's Level 1 check rejects.
    with pytest.raises(ValueError, match="mutation would break the workflow"):
        rewire_workflow_link_service(
            tmp_path, "my-flow",
            target_node_id=5, target_input="positive",
            source_node_id=1, source_output="MODEL",
        )

    after = (tmp_path / "my-flow" / "workflow.json").read_text()
    assert before == after

    logs = list((tmp_path / "my-flow" / "99_logs" / "mutations").glob("*_rewire-workflow-link_attempt01.json"))
    assert len(logs) == 1
    logged = json.loads(logs[0].read_text())
    assert logged["outcome"] == "rejected"
    assert any("incompatible types" in e or "type" in e.lower() for e in logged["response"]["errors"])


# --- Fix 7: import_workflow must not silently overwrite an existing id ---


def test_import_workflow_rejects_overwrite_without_force(tmp_path):
    import_workflow(tmp_path, FIXTURES_DIR / "comfyui_workflow_simple.json", "my-flow")
    stored_path = tmp_path / "my-flow" / "workflow.json"
    before = stored_path.read_text()

    with pytest.raises(ValueError, match="--force"):
        import_workflow(tmp_path, FIXTURES_DIR / "comfyui_workflow_simple.json", "my-flow")

    assert stored_path.read_text() == before


def test_import_workflow_overwrites_with_force(tmp_path):
    import_workflow(tmp_path, FIXTURES_DIR / "comfyui_workflow_simple.json", "my-flow")
    result = import_workflow(
        tmp_path, FIXTURES_DIR / "comfyui_workflow_simple.json", "my-flow", force=True
    )
    assert result["id"] == "my-flow"
    stored = load_stored_workflow(tmp_path, "my-flow")
    assert stored["last_node_id"] == 7


# --- Fix 8: export_workflow creates missing parent directories ---


def test_export_workflow_creates_missing_parent_directory(tmp_path):
    import_workflow(tmp_path, FIXTURES_DIR / "comfyui_workflow_simple.json", "my-flow")
    out_path = tmp_path / "does" / "not" / "exist" / "exported.json"
    assert not out_path.parent.exists()
    export_workflow(tmp_path, "my-flow", out_path)
    assert out_path.exists()
    assert json.loads(out_path.read_text())["last_node_id"] == 7
