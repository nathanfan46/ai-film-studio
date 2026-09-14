from pathlib import Path

import pytest

from ai_film.comfyui.parser import load_workflow_json

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_loads_legacy_workflow_shape():
    workflow = load_workflow_json(FIXTURES_DIR / "comfyui_workflow_simple.json")
    assert workflow["last_node_id"] == 7
    assert len(workflow["nodes"]) == 7
    assert len(workflow["links"]) == 9


def test_rejects_api_prompt_format():
    with pytest.raises(ValueError, match="API/prompt format"):
        load_workflow_json(FIXTURES_DIR / "comfyui_workflow_api_format.json")


def test_rejects_v1_schema_shape():
    with pytest.raises(ValueError, match="v1.0"):
        load_workflow_json(FIXTURES_DIR / "comfyui_workflow_v1_schema.json")


def test_rejects_missing_file():
    with pytest.raises(ValueError, match="not found"):
        load_workflow_json(FIXTURES_DIR / "does_not_exist.json")


def test_rejects_invalid_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_workflow_json(bad)
