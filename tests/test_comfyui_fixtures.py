import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("filename", [
    "comfyui_workflow_simple.json",
    "comfyui_workflow_complex.json",
    "comfyui_workflow_api_format.json",
    "comfyui_workflow_v1_schema.json",
])
def test_fixture_is_valid_json(filename):
    data = json.loads((FIXTURES_DIR / filename).read_text())
    assert isinstance(data, dict)


def test_complex_fixture_has_expected_hard_cases():
    data = json.loads((FIXTURES_DIR / "comfyui_workflow_complex.json").read_text())
    types = [n["type"] for n in data["nodes"]]
    assert types.count("LivePortraitCropper") == 2
    assert types.count("Reroute") >= 2
    assert "TotallyUnknownCustomNode" in types
    assert "Note" in types
    dict_widget_nodes = [n for n in data["nodes"] if isinstance(n.get("widgets_values"), dict)]
    assert len(dict_widget_nodes) >= 1
