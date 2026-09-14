import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_film.cli import _coerce_cli_value, app

runner = CliRunner()
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("raw", ["NaN", "nan", "inf", "Inf", "-inf", "Infinity", "+INFINITY"])
def test_coerce_cli_value_rejects_non_finite_float_tokens(raw):
    # Python's float() happily parses "NaN"/"inf"/"Infinity" (case-insensitive,
    # with optional sign) into non-finite floats. _coerce_cli_value exists to
    # protect typed ComfyUI widget values from silent corruption, so a literal
    # string value like "NaN" (e.g. someone's seed label, or a text field that
    # happens to contain that word) must round-trip as the string "NaN", not
    # silently become float('nan') -- which also produces non-standard
    # `NaN`/`Infinity` tokens when re-serialized to JSON via json.dumps.
    assert _coerce_cli_value(raw) == raw


def test_coerce_cli_value_still_converts_valid_numeric_and_bool_cases():
    assert _coerce_cli_value("30") == 30
    assert isinstance(_coerce_cli_value("30"), int)
    assert _coerce_cli_value("3.14") == 3.14
    assert isinstance(_coerce_cli_value("3.14"), float)
    assert _coerce_cli_value("true") is True
    assert _coerce_cli_value("false") is False
    assert _coerce_cli_value("512.safetensors") == "512.safetensors"


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


def test_list_workflow_nodes_prints_output_socket_names(tmp_path):
    # Fix 3: rewire-workflow-link --source-output requires knowing a node's
    # actual output socket names, but list-workflow-nodes never printed
    # them. Node 1 (CheckpointLoaderSimple) has outputs MODEL/CLIP/VAE --
    # assert a non-generic-sounding one (CLIP) actually shows up in stdout.
    workflows_dir = tmp_path / "workflows"
    runner.invoke(app, ["import-workflow", str(FIXTURES_DIR / "comfyui_workflow_simple.json"),
                         "--id", "my-flow", "--workflows-dir", str(workflows_dir)])
    result = runner.invoke(app, ["list-workflow-nodes", "--id", "my-flow", "--workflows-dir", str(workflows_dir)])
    assert result.exit_code == 0, result.output
    assert "CLIP" in result.output
    assert "VAE" in result.output


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


def test_set_workflow_field_raw_string_keeps_numeric_looking_value_as_string(tmp_path):
    # Fix 4: without --raw-string, a numeric-looking string like "2024"
    # (e.g. a filename_prefix) would silently be coerced to an int. Reuse
    # node 7's SaveImage "filename_prefix" widget (index 0) via set-workflow
    # -raw is a different primitive -- exercise set-workflow-field directly
    # on the CLIPTextEncode "text" field instead, which stays a string
    # field: assert the raw string "2024" is written, not the int 2024.
    workflows_dir = tmp_path / "workflows"
    runner.invoke(app, ["import-workflow", str(FIXTURES_DIR / "comfyui_workflow_simple.json"),
                         "--id", "my-flow", "--workflows-dir", str(workflows_dir)])
    result = runner.invoke(app, ["set-workflow-field", "--id", "my-flow", "--node", "2",
                                  "--field", "text", "--value", "2024", "--raw-string",
                                  "--workflows-dir", str(workflows_dir)])
    assert result.exit_code == 0, result.output
    stored = json.loads((workflows_dir / "my-flow" / "workflow.json").read_text())
    node = next(n for n in stored["nodes"] if n["id"] == 2)
    assert node["widgets_values"][0] == "2024"
    assert isinstance(node["widgets_values"][0], str)


def test_set_workflow_field_without_raw_string_still_coerces_numeric_value(tmp_path):
    # Regression guard: --raw-string must not change existing coercion
    # behavior (Task 12) when the flag isn't passed.
    workflows_dir = tmp_path / "workflows"
    runner.invoke(app, ["import-workflow", str(FIXTURES_DIR / "comfyui_workflow_simple.json"),
                         "--id", "my-flow", "--workflows-dir", str(workflows_dir)])
    result = runner.invoke(app, ["set-workflow-field", "--id", "my-flow", "--node", "5",
                                  "--field", "steps", "--value", "30", "--workflows-dir", str(workflows_dir)])
    assert result.exit_code == 0, result.output
    stored = json.loads((workflows_dir / "my-flow" / "workflow.json").read_text())
    node = next(n for n in stored["nodes"] if n["id"] == 5)
    assert node["widgets_values"][2] == 30
    assert isinstance(node["widgets_values"][2], int)


def test_set_workflow_raw_raw_string_keeps_numeric_looking_value_as_string(tmp_path):
    workflows_dir = tmp_path / "workflows"
    runner.invoke(app, ["import-workflow", str(FIXTURES_DIR / "comfyui_workflow_simple.json"),
                         "--id", "my-flow", "--workflows-dir", str(workflows_dir)])
    result = runner.invoke(app, ["set-workflow-raw", "--id", "my-flow", "--node", "7",
                                  "--index", "0", "--value", "1984", "--raw-string",
                                  "--workflows-dir", str(workflows_dir)])
    assert result.exit_code == 0, result.output
    stored = json.loads((workflows_dir / "my-flow" / "workflow.json").read_text())
    node = next(n for n in stored["nodes"] if n["id"] == 7)
    assert node["widgets_values"][0] == "1984"
    assert isinstance(node["widgets_values"][0], str)


def test_import_workflow_rejects_overwrite_without_force(tmp_path):
    workflows_dir = tmp_path / "workflows"
    runner.invoke(app, ["import-workflow", str(FIXTURES_DIR / "comfyui_workflow_simple.json"),
                         "--id", "my-flow", "--workflows-dir", str(workflows_dir)])
    stored_path = workflows_dir / "my-flow" / "workflow.json"
    before = stored_path.read_text()

    result = runner.invoke(app, ["import-workflow", str(FIXTURES_DIR / "comfyui_workflow_simple.json"),
                                  "--id", "my-flow", "--workflows-dir", str(workflows_dir)])
    assert result.exit_code == 1
    assert "--force" in result.output
    assert stored_path.read_text() == before


def test_import_workflow_overwrites_with_force_flag(tmp_path):
    workflows_dir = tmp_path / "workflows"
    runner.invoke(app, ["import-workflow", str(FIXTURES_DIR / "comfyui_workflow_simple.json"),
                         "--id", "my-flow", "--workflows-dir", str(workflows_dir)])
    result = runner.invoke(app, ["import-workflow", str(FIXTURES_DIR / "comfyui_workflow_simple.json"),
                                  "--id", "my-flow", "--force", "--workflows-dir", str(workflows_dir)])
    assert result.exit_code == 0, result.output


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
