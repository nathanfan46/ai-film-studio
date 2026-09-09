import json
from pathlib import Path

import pytest

from ai_film.template_store import save_template


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


def _write_draft(path: Path, draft: dict) -> None:
    path.write_text(json.dumps(draft))


def test_save_template_rejects_missing_draft_file(tmp_path: Path):
    with pytest.raises(ValueError, match="not found"):
        save_template(tmp_path / "missing.json", "hero-orbit", tmp_path / "templates")


def test_save_template_rejects_invalid_draft(tmp_path: Path):
    draft_path = tmp_path / "draft.json"
    draft = _draft()
    del draft["shot_patterns"]
    _write_draft(draft_path, draft)

    with pytest.raises(ValueError, match="schema validation"):
        save_template(draft_path, "hero-orbit", tmp_path / "templates")


def test_save_template_writes_template_json_with_given_id(tmp_path: Path):
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, _draft())
    templates_dir = tmp_path / "templates"

    result = save_template(draft_path, "hero-orbit", templates_dir)

    assert result["id"] == "hero-orbit"
    written = json.loads((templates_dir / "hero-orbit" / "template.json").read_text())
    assert written["id"] == "hero-orbit"
    assert written["name"] == "Hero Orbit Reveal"


def test_save_template_copies_referenced_keyframes(tmp_path: Path):
    keyframe = tmp_path / "shot1_start.jpg"
    keyframe.write_bytes(b"FAKE-JPG")
    draft = _draft()
    draft["shot_patterns"][0]["reference_keyframe"] = "shot1_start.jpg"
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, draft)
    templates_dir = tmp_path / "templates"

    result = save_template(draft_path, "hero-orbit", templates_dir)

    copied = templates_dir / "hero-orbit" / "keyframes" / "shot1_start.jpg"
    assert copied.exists()
    assert result["shot_patterns"][0]["reference_keyframe"] == "keyframes/shot1_start.jpg"


def test_save_template_rejects_missing_referenced_keyframe(tmp_path: Path):
    draft = _draft()
    draft["shot_patterns"][0]["reference_keyframe"] = "does_not_exist.jpg"
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, draft)

    with pytest.raises(ValueError, match="reference_keyframe"):
        save_template(draft_path, "hero-orbit", tmp_path / "templates")


def test_save_template_refuses_existing_without_force(tmp_path: Path):
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, _draft())
    templates_dir = tmp_path / "templates"
    save_template(draft_path, "hero-orbit", templates_dir)

    with pytest.raises(RuntimeError, match="already exists"):
        save_template(draft_path, "hero-orbit", templates_dir)


def test_save_template_force_overwrites(tmp_path: Path):
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, _draft())
    templates_dir = tmp_path / "templates"
    save_template(draft_path, "hero-orbit", templates_dir)

    save_template(draft_path, "hero-orbit", templates_dir, force=True)  # must not raise
