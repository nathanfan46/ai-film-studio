import json
from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app

runner = CliRunner()
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_import_then_describe_then_export(tmp_path):
    workflows_dir = tmp_path / "workflows"
    result = runner.invoke(app, [
        "import-workflow", str(FIXTURES_DIR / "comfyui_workflow_simple.json"),
        "--id", "my-flow", "--workflows-dir", str(workflows_dir),
    ])
    assert result.exit_code == 0, result.output
    assert (workflows_dir / "my-flow" / "workflow.json").exists()

    result = runner.invoke(app, ["describe-workflow", "--id", "my-flow", "--workflows-dir", str(workflows_dir)])
    assert result.exit_code == 0, result.output
    assert "sampler" in result.output

    out_path = tmp_path / "exported.json"
    result = runner.invoke(app, [
        "export-workflow", "--id", "my-flow", "--out", str(out_path), "--workflows-dir", str(workflows_dir),
    ])
    assert result.exit_code == 0, result.output
    assert json.loads(out_path.read_text())["last_node_id"] == 7


def test_list_workflow_nodes_filters_by_role(tmp_path):
    workflows_dir = tmp_path / "workflows"
    runner.invoke(app, ["import-workflow", str(FIXTURES_DIR / "comfyui_workflow_simple.json"),
                         "--id", "my-flow", "--workflows-dir", str(workflows_dir)])
    result = runner.invoke(app, ["list-workflow-nodes", "--id", "my-flow", "--role", "sampler",
                                  "--workflows-dir", str(workflows_dir)])
    assert result.exit_code == 0, result.output
    assert "KSampler" in result.output
    assert "CheckpointLoaderSimple" not in result.output


def test_set_workflow_field_coerces_numeric_value(tmp_path):
    workflows_dir = tmp_path / "workflows"
    runner.invoke(app, ["import-workflow", str(FIXTURES_DIR / "comfyui_workflow_simple.json"),
                         "--id", "my-flow", "--workflows-dir", str(workflows_dir)])
    result = runner.invoke(app, ["set-workflow-field", "--id", "my-flow", "--node", "5",
                                  "--field", "steps", "--value", "30", "--workflows-dir", str(workflows_dir)])
    assert result.exit_code == 0, result.output
    stored = json.loads((workflows_dir / "my-flow" / "workflow.json").read_text())
    node = next(n for n in stored["nodes"] if n["id"] == 5)
    assert node["widgets_values"][2] == 30  # int, not the string "30"


def test_remove_workflow_node_with_bypass_flag(tmp_path):
    workflows_dir = tmp_path / "workflows"
    runner.invoke(app, ["import-workflow", str(FIXTURES_DIR / "comfyui_workflow_simple.json"),
                         "--id", "my-flow", "--workflows-dir", str(workflows_dir)])
    result = runner.invoke(app, ["remove-workflow-node", "--id", "my-flow", "--node", "3",
                                  "--workflows-dir", str(workflows_dir)])
    assert result.exit_code == 0, result.output
    stored = json.loads((workflows_dir / "my-flow" / "workflow.json").read_text())
    assert not any(n["id"] == 3 for n in stored["nodes"])


def test_remove_workflow_node_bypass_flag_reconnects_around_node(tmp_path):
    # The brief's own bypass test never passes --bypass (it only exercises
    # the default bypass=False path), so the flag's wiring through to
    # remove_workflow_node_service(..., bypass=bypass) is never actually
    # proven by the CLI test suite. This test targets node 8 in the complex
    # fixture -- a Reroute node with exactly one MODEL input and one MODEL
    # output (an unambiguous type-matching pass-through pair) -- so passing
    # --bypass produces an observably different, correct result: node 8 is
    # gone AND node 9's input link now resolves straight back to node 1
    # (the original upstream source), instead of merely disappearing.
    workflows_dir = tmp_path / "workflows"
    runner.invoke(app, ["import-workflow", str(FIXTURES_DIR / "comfyui_workflow_complex.json"),
                         "--id", "my-flow", "--workflows-dir", str(workflows_dir)])
    result = runner.invoke(app, ["remove-workflow-node", "--id", "my-flow", "--node", "8",
                                  "--bypass", "--workflows-dir", str(workflows_dir)])
    assert result.exit_code == 0, result.output
    assert "bypassed" in result.output

    stored = json.loads((workflows_dir / "my-flow" / "workflow.json").read_text())
    assert not any(n["id"] == 8 for n in stored["nodes"])

    node_9 = next(n for n in stored["nodes"] if n["id"] == 9)
    new_link_id = node_9["inputs"][0]["link"]
    links_by_id = {link[0]: link for link in stored["links"]}
    new_link = links_by_id[new_link_id]
    # link shape: [id, origin_id, origin_slot, target_id, target_slot, type]
    assert new_link[1] == 1  # reconnected straight back to node 1, not node 8
    assert new_link[3] == 9
    assert new_link[5] == "MODEL"


def test_mutation_failure_exits_nonzero_with_clear_message(tmp_path):
    workflows_dir = tmp_path / "workflows"
    runner.invoke(app, ["import-workflow", str(FIXTURES_DIR / "comfyui_workflow_simple.json"),
                         "--id", "my-flow", "--workflows-dir", str(workflows_dir)])
    result = runner.invoke(app, ["set-workflow-field", "--id", "my-flow", "--node", "999",
                                  "--field", "text", "--value", "x", "--workflows-dir", str(workflows_dir)])
    assert result.exit_code == 1
    assert "999" in result.output


def test_import_rejects_v1_schema_with_clear_error(tmp_path):
    workflows_dir = tmp_path / "workflows"
    result = runner.invoke(app, ["import-workflow", str(FIXTURES_DIR / "comfyui_workflow_v1_schema.json"),
                                  "--id", "bad-flow", "--workflows-dir", str(workflows_dir)])
    assert result.exit_code == 1
    assert "v1.0" in result.output
