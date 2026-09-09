import json
from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app

runner = CliRunner()


def _draft(**overrides) -> dict:
    draft = {
        "schema_version": "1.0",
        "id": "placeholder",
        "name": "Hero Orbit Reveal",
        "created_at": "2026-09-04T12:00:00Z",
        "source_note": "test",
        "shot_patterns": [
            {
                "order": 0,
                "pattern_name": "establish",
                "camera": "wide shot, static",
                "subject_motion": "N/A",
                "framing": "wide",
                "suggested_duration_seconds": 3,
                "reference_keyframe": None,
            }
        ],
    }
    draft.update(overrides)
    return draft


def test_save_template_cmd_rejects_missing_draft(tmp_path: Path):
    result = runner.invoke(
        app,
        [
            "save-template", "--from", str(tmp_path / "missing.json"), "--id", "hero-orbit",
            "--templates-dir", str(tmp_path / "templates"),
        ],
    )
    assert result.exit_code == 1
    assert "not found" in result.output


def test_save_template_cmd_then_list_and_show(tmp_path: Path):
    draft_path = tmp_path / "draft.json"
    draft_path.write_text(json.dumps(_draft()))
    templates_dir = tmp_path / "templates"

    save_result = runner.invoke(
        app,
        [
            "save-template", "--from", str(draft_path), "--id", "hero-orbit",
            "--templates-dir", str(templates_dir),
        ],
    )
    assert save_result.exit_code == 0
    assert "hero-orbit" in save_result.output

    list_result = runner.invoke(app, ["list-templates", "--templates-dir", str(templates_dir)])
    assert list_result.exit_code == 0
    assert "hero-orbit" in list_result.output
    assert "Hero Orbit Reveal" in list_result.output

    show_result = runner.invoke(
        app, ["show-template", "--id", "hero-orbit", "--templates-dir", str(templates_dir)]
    )
    assert show_result.exit_code == 0
    assert '"id": "hero-orbit"' in show_result.output


def test_list_templates_cmd_empty_prints_no_templates_message(tmp_path: Path):
    result = runner.invoke(app, ["list-templates", "--templates-dir", str(tmp_path / "templates")])
    assert result.exit_code == 0
    assert "no templates" in result.output.lower()


def test_show_template_cmd_rejects_unknown_id(tmp_path: Path):
    result = runner.invoke(
        app, ["show-template", "--id", "does-not-exist", "--templates-dir", str(tmp_path / "templates")]
    )
    assert result.exit_code == 1
    assert "not found" in result.output
