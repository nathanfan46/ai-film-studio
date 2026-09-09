from pathlib import Path

from ai_film.asset_staleness import check_stale
from ai_film.shot_store import save_shot


def _shot_with_source_assets(shot_id: str, source_assets: list[dict]) -> dict:
    return {
        "schema_version": "1.0", "id": shot_id, "status": "draft", "duration_seconds": 4,
        "continuity": {"status": "passed", "checked_at": None, "issues": []},
        "generation": {
            "image": {"status": "completed", "source_assets": source_assets},
            "video": {"status": "not_required"},
            "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }


def test_check_stale_reports_nothing_when_no_shots_exist(tmp_path: Path):
    (tmp_path / "03_shots").mkdir(parents=True)
    assert check_stale(tmp_path) == []


def test_check_stale_reports_clean_when_hash_still_matches(tmp_path: Path):
    reference = tmp_path / "assets" / "characters" / "mara" / "reference.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"MARA-V1")
    from ai_film.services.generation_service import sha256_of_file

    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_source_assets("S01_SH01", [
        {"path": "assets/characters/mara/reference.png", "sha256": sha256_of_file(reference)}
    ]))

    assert check_stale(tmp_path) == []


def test_check_stale_reports_shot_whose_asset_hash_changed(tmp_path: Path):
    reference = tmp_path / "assets" / "characters" / "mara" / "reference.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"MARA-V1")
    from ai_film.services.generation_service import sha256_of_file

    old_hash = sha256_of_file(reference)
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_source_assets("S01_SH01", [
        {"path": "assets/characters/mara/reference.png", "sha256": old_hash}
    ]))

    reference.write_bytes(b"MARA-V2-CHANGED")

    result = check_stale(tmp_path)

    assert result == [
        {"shot_id": "S01_SH01", "changed_assets": ["assets/characters/mara/reference.png"]}
    ]


def test_check_stale_skips_shots_without_completed_image_generation(tmp_path: Path):
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    shot = _shot_with_source_assets("S01_SH01", [{"path": "does/not/matter.png", "sha256": "abc"}])
    shot["generation"]["image"]["status"] = "pending"
    save_shot(shot_path, shot)

    assert check_stale(tmp_path) == []


def test_check_stale_skips_asset_paths_that_no_longer_exist(tmp_path: Path):
    """A deleted reference file is skipped, not reported — there's nothing
    to compare its hash against, and this plan doesn't ask for a distinct
    "missing" state."""
    shot_path = tmp_path / "03_shots" / "S01_SH01.json"
    save_shot(shot_path, _shot_with_source_assets("S01_SH01", [
        {"path": "assets/characters/deleted/reference.png", "sha256": "abc123"}
    ]))

    assert check_stale(tmp_path) == []


def test_check_stale_reports_multiple_shots_independently(tmp_path: Path):
    reference = tmp_path / "assets" / "characters" / "mara" / "reference.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"MARA-V1")
    from ai_film.services.generation_service import sha256_of_file

    old_hash = sha256_of_file(reference)
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot_with_source_assets(
        "S01_SH01", [{"path": "assets/characters/mara/reference.png", "sha256": old_hash}]
    ))
    save_shot(tmp_path / "03_shots" / "S01_SH02.json", _shot_with_source_assets(
        "S01_SH02", [{"path": "assets/characters/mara/reference.png", "sha256": old_hash}]
    ))

    reference.write_bytes(b"MARA-V2-CHANGED")

    result = check_stale(tmp_path)

    assert {r["shot_id"] for r in result} == {"S01_SH01", "S01_SH02"}
