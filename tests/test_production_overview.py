import json
from pathlib import Path

from ai_film.production_overview import build_production_overview
from ai_film.shot_store import save_shot


def _init_config(project_dir: Path, title: str = "Test Film") -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "config.json").write_text(json.dumps({"title": title}))


def _base_shot(shot_id: str, **overrides) -> dict:
    shot = {
        "schema_version": "1.0", "id": shot_id, "status": "draft", "duration_seconds": 3,
        "continuity": {"status": "pending", "checked_at": None, "issues": []},
        "generation": {
            "image": {"status": "pending", "attempts": 0},
            "video": {"status": "pending", "attempts": 0},
            "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }
    shot.update(overrides)
    return shot


def test_build_production_overview_with_no_shots_renders_empty_state(tmp_path: Path):
    _init_config(tmp_path)

    out_path = build_production_overview(tmp_path)

    assert out_path == tmp_path / "07_review" / "overview.html"
    html_text = out_path.read_text()
    assert "Test Film" in html_text
    assert "0 shots" in html_text


def test_build_production_overview_groups_shots_by_scene_in_order(tmp_path: Path):
    _init_config(tmp_path)
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _base_shot("S01_SH01"))
    save_shot(tmp_path / "03_shots" / "S01_SH02.json", _base_shot("S01_SH02"))
    save_shot(tmp_path / "03_shots" / "S02_SH01.json", _base_shot("S02_SH01"))

    out_path = build_production_overview(tmp_path)

    html_text = out_path.read_text()
    assert html_text.index("S01") < html_text.index("S01_SH01") < html_text.index("S01_SH02")
    assert html_text.index("S01_SH02") < html_text.index("S02_SH01")


def test_build_production_overview_shows_thumbnail_when_artifact_file_exists_regardless_of_status(
    tmp_path: Path,
):
    """Per explicit design decision: file-on-disk is the display signal, not
    the image stage's status field — a shot generated outside the normal
    flow (or with stale metadata) still gets its thumbnail shown."""
    _init_config(tmp_path)
    image_path = tmp_path / "04_storyboard" / "S01_SH01.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"PNG-BYTES")
    shot = _base_shot(
        "S01_SH01",
        generation={
            "image": {
                "status": "failed",  # deliberately NOT "completed"
                "artifact": {"path": "04_storyboard/S01_SH01.png", "size_bytes": 9, "sha256": None},
            },
            "video": {"status": "pending", "attempts": 0},
            "voice": {"status": "not_required"}, "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    )
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", shot)

    out_path = build_production_overview(tmp_path)

    html_text = out_path.read_text()
    assert 'src="../04_storyboard/S01_SH01.png"' in html_text
    assert "No storyboard" not in html_text


def test_build_production_overview_falls_back_to_placeholder_when_artifact_file_missing(
    tmp_path: Path,
):
    _init_config(tmp_path)
    shot = _base_shot(
        "S01_SH01",
        generation={
            "image": {
                "status": "completed",
                "artifact": {"path": "04_storyboard/S01_SH01.png", "size_bytes": 9, "sha256": None},
            },
            "video": {"status": "pending", "attempts": 0},
            "voice": {"status": "not_required"}, "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    )
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", shot)
    # deliberately never write the actual PNG file

    out_path = build_production_overview(tmp_path)

    html_text = out_path.read_text()
    assert "No storyboard" in html_text


def test_build_production_overview_falls_back_to_placeholder_when_no_artifact_at_all(
    tmp_path: Path,
):
    _init_config(tmp_path)
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _base_shot("S01_SH01"))

    out_path = build_production_overview(tmp_path)

    assert "No storyboard" in out_path.read_text()


def test_build_production_overview_links_to_existing_review_page(tmp_path: Path):
    _init_config(tmp_path)
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _base_shot("S01_SH01"))
    review_dir = tmp_path / "07_review"
    review_dir.mkdir(parents=True)
    (review_dir / "S01_SH01.html").write_text("<html></html>")

    out_path = build_production_overview(tmp_path)

    html_text = out_path.read_text()
    assert 'href="S01_SH01.html"' in html_text
    assert "Review unavailable" not in html_text


def test_build_production_overview_never_emits_a_broken_review_link(tmp_path: Path):
    _init_config(tmp_path)
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _base_shot("S01_SH01"))
    # no 07_review/S01_SH01.html on disk

    out_path = build_production_overview(tmp_path)

    html_text = out_path.read_text()
    assert 'href="S01_SH01.html"' not in html_text
    assert "Review unavailable" in html_text


def test_build_production_overview_flags_stale_shots(tmp_path: Path):
    from ai_film.services.generation_service import sha256_of_file

    _init_config(tmp_path)
    reference = tmp_path / "assets" / "characters" / "mara" / "reference.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"MARA-V1")
    shot = _base_shot(
        "S01_SH01",
        characters=[{"name": "Mara", "reference": "assets/characters/mara/reference.png"}],
        generation={
            "image": {
                "status": "completed",
                "artifact": {"path": "04_storyboard/S01_SH01.png", "size_bytes": 1, "sha256": None},
                "source_assets": [{
                    "path": "assets/characters/mara/reference.png",
                    "sha256": sha256_of_file(reference),
                }],
            },
            "video": {"status": "pending", "attempts": 0},
            "voice": {"status": "not_required"}, "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    )
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", shot)
    reference.write_bytes(b"MARA-V2-CHANGED")

    out_path = build_production_overview(tmp_path)

    html_text = out_path.read_text()
    assert "STALE" in html_text


def test_build_production_overview_omits_stale_badge_for_fresh_shots(tmp_path: Path):
    _init_config(tmp_path)
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _base_shot("S01_SH01"))

    out_path = build_production_overview(tmp_path)

    assert "STALE" not in out_path.read_text()


def test_build_production_overview_shows_camera_characters_and_environment(tmp_path: Path):
    shot = _base_shot(
        "S01_SH01",
        camera={"shot": "close-up"},
        characters=[{"name": "Mara"}, {"name": "Doctor"}],
        environment={"name": "Office"},
    )
    _init_config(tmp_path)
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", shot)

    html_text = build_production_overview(tmp_path).read_text()

    assert "CLOSE-UP" in html_text.upper()
    assert "Mara" in html_text
    assert "Doctor" in html_text
    assert "Office" in html_text


def test_build_production_overview_handles_shot_with_no_camera_characters_or_environment(
    tmp_path: Path,
):
    _init_config(tmp_path)
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _base_shot("S01_SH01"))

    # must not raise
    html_text = build_production_overview(tmp_path).read_text()
    assert "S01_SH01" in html_text


def test_build_production_overview_shows_stage_statuses(tmp_path: Path):
    shot = _base_shot(
        "S01_SH01",
        generation={
            "image": {"status": "completed", "artifact": {"path": "x.png", "size_bytes": 1, "sha256": None}},
            "video": {"status": "failed", "attempts": 1},
            "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    )
    _init_config(tmp_path)
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", shot)

    html_text = build_production_overview(tmp_path).read_text()

    assert "IMAGE" in html_text
    assert "VIDEO" in html_text

    # escapes project title and character/environment names against HTML injection
def test_build_production_overview_escapes_html_special_characters(tmp_path: Path):
    _init_config(tmp_path, title="A & B <Film>")
    shot = _base_shot("S01_SH01", characters=[{"name": "<script>alert(1)</script>"}])
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", shot)

    html_text = build_production_overview(tmp_path).read_text()

    assert "<script>alert(1)</script>" not in html_text
    assert "&lt;script&gt;" in html_text
