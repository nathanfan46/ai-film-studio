import json
from pathlib import Path

import pytest

from ai_film.services.workflow_service import (
    export_workflow,
    import_workflow,
    load_stored_workflow,
    remove_workflow_node_service,
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
