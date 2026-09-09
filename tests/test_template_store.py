import json
import zipfile
from pathlib import Path

import pytest

from ai_film.template_store import save_template, list_templates, show_template, export_template, import_template


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


def test_list_templates_empty_directory_returns_empty_list(tmp_path: Path):
    assert list_templates(tmp_path / "templates") == []


def test_list_templates_returns_id_name_and_pattern_count(tmp_path: Path):
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, _draft())
    templates_dir = tmp_path / "templates"
    save_template(draft_path, "hero-orbit", templates_dir)

    results = list_templates(templates_dir)

    assert results == [{"id": "hero-orbit", "name": "Hero Orbit Reveal", "shot_pattern_count": 1}]


def test_list_templates_skips_directories_without_template_json(tmp_path: Path):
    templates_dir = tmp_path / "templates"
    (templates_dir / "not-a-template").mkdir(parents=True)

    assert list_templates(templates_dir) == []


def test_show_template_returns_full_content(tmp_path: Path):
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, _draft())
    templates_dir = tmp_path / "templates"
    save_template(draft_path, "hero-orbit", templates_dir)

    result = show_template("hero-orbit", templates_dir)

    assert result["id"] == "hero-orbit"
    assert result["name"] == "Hero Orbit Reveal"


def test_show_template_rejects_unknown_id(tmp_path: Path):
    with pytest.raises(ValueError, match="not found"):
        show_template("does-not-exist", tmp_path / "templates")


def test_export_template_rejects_unknown_id(tmp_path: Path):
    with pytest.raises(ValueError, match="not found"):
        export_template("does-not-exist", tmp_path / "templates", tmp_path / "out.zip")


def test_export_then_import_round_trips_template_json_and_keyframes(tmp_path: Path):
    keyframe = tmp_path / "shot1_start.jpg"
    keyframe.write_bytes(b"FAKE-JPG")
    draft = _draft()
    draft["shot_patterns"][0]["reference_keyframe"] = "shot1_start.jpg"
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, draft)
    templates_dir = tmp_path / "templates"
    save_template(draft_path, "hero-orbit", templates_dir)

    archive_path = tmp_path / "hero-orbit.zip"
    export_template("hero-orbit", templates_dir, archive_path)
    assert archive_path.exists()
    with zipfile.ZipFile(archive_path) as zf:
        assert "template.json" in zf.namelist()
        assert "keyframes/shot1_start.jpg" in zf.namelist()

    new_templates_dir = tmp_path / "imported-templates"
    result = import_template(archive_path, new_templates_dir)

    assert result["id"] == "hero-orbit"
    imported = json.loads((new_templates_dir / "hero-orbit" / "template.json").read_text())
    assert imported["name"] == "Hero Orbit Reveal"
    assert (new_templates_dir / "hero-orbit" / "keyframes" / "shot1_start.jpg").exists()


def test_import_template_honors_id_override(tmp_path: Path):
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, _draft())
    templates_dir = tmp_path / "templates"
    save_template(draft_path, "hero-orbit", templates_dir)
    archive_path = tmp_path / "hero-orbit.zip"
    export_template("hero-orbit", templates_dir, archive_path)

    new_templates_dir = tmp_path / "imported-templates"
    result = import_template(archive_path, new_templates_dir, template_id="hero-orbit-v2")

    assert result["id"] == "hero-orbit-v2"
    assert (new_templates_dir / "hero-orbit-v2" / "template.json").exists()


def test_import_template_rejects_archive_without_template_json(tmp_path: Path):
    archive_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("not-a-template.txt", "hello")

    with pytest.raises(ValueError, match="template.json"):
        import_template(archive_path, tmp_path / "templates")


def test_import_template_rejects_invalid_template_json(tmp_path: Path):
    archive_path = tmp_path / "bad.zip"
    invalid = _draft()
    del invalid["shot_patterns"]
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("template.json", json.dumps(invalid))

    with pytest.raises(ValueError, match="schema validation"):
        import_template(archive_path, tmp_path / "templates")


def test_import_template_refuses_existing_without_force(tmp_path: Path):
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, _draft())
    templates_dir = tmp_path / "templates"
    save_template(draft_path, "hero-orbit", templates_dir)
    archive_path = tmp_path / "hero-orbit.zip"
    export_template("hero-orbit", templates_dir, archive_path)

    with pytest.raises(RuntimeError, match="already exists"):
        import_template(archive_path, templates_dir)


# --- Finding 1: path traversal via a malicious/careless template id ---


def test_save_template_rejects_path_traversal_template_id(tmp_path: Path):
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, _draft())
    templates_dir = tmp_path / "templates"
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("do not delete me")

    with pytest.raises(ValueError, match="invalid template id"):
        save_template(draft_path, "../victim", templates_dir, force=True)

    assert (victim / "keep.txt").exists()
    assert not templates_dir.exists()


def test_import_template_rejects_path_traversal_id_from_archive(tmp_path: Path):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("do not delete me")

    templates_dir = tmp_path / "templates"
    malicious = _draft(id="../victim")
    archive_path = tmp_path / "malicious.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("template.json", json.dumps(malicious))

    with pytest.raises(ValueError):
        import_template(archive_path, templates_dir, force=True)

    assert (victim / "keep.txt").exists()
    assert not templates_dir.exists()


def test_import_template_rejects_path_traversal_id_override(tmp_path: Path):
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, _draft())
    templates_dir = tmp_path / "templates"
    save_template(draft_path, "hero-orbit", templates_dir)
    archive_path = tmp_path / "hero-orbit.zip"
    export_template("hero-orbit", templates_dir, archive_path)

    with pytest.raises(ValueError, match="invalid template id"):
        import_template(archive_path, templates_dir, template_id="../victim2")


# --- Finding 5: --force must not destroy the old template before keyframes
# referenced by the new draft are confirmed to exist ---


def test_save_template_force_with_missing_keyframe_preserves_existing_template(tmp_path: Path):
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, _draft())
    templates_dir = tmp_path / "templates"
    save_template(draft_path, "hero-orbit", templates_dir)
    original_content = (templates_dir / "hero-orbit" / "template.json").read_text()

    bad_draft = _draft()
    bad_draft["shot_patterns"][0]["reference_keyframe"] = "does_not_exist.jpg"
    bad_draft_path = tmp_path / "bad_draft.json"
    _write_draft(bad_draft_path, bad_draft)

    with pytest.raises(ValueError, match="reference_keyframe"):
        save_template(bad_draft_path, "hero-orbit", templates_dir, force=True)

    assert (templates_dir / "hero-orbit" / "template.json").exists()
    assert (templates_dir / "hero-orbit" / "template.json").read_text() == original_content


# --- Finding 6: corrupt archives and corrupt JSON produce clean errors
# instead of crashing ---


def test_import_template_rejects_non_zip_archive(tmp_path: Path):
    bad_archive = tmp_path / "not-a-zip.zip"
    bad_archive.write_bytes(b"this is not a zip file")

    with pytest.raises(ValueError, match="not a valid zip archive"):
        import_template(bad_archive, tmp_path / "templates")


def test_list_templates_skips_corrupt_json_but_keeps_valid_entries(tmp_path: Path):
    draft_path = tmp_path / "draft.json"
    _write_draft(draft_path, _draft())
    templates_dir = tmp_path / "templates"
    save_template(draft_path, "hero-orbit", templates_dir)

    corrupt_dir = templates_dir / "corrupt-template"
    corrupt_dir.mkdir()
    (corrupt_dir / "template.json").write_text("{not valid json")

    results = list_templates(templates_dir)

    assert results == [{"id": "hero-orbit", "name": "Hero Orbit Reveal", "shot_pattern_count": 1}]
