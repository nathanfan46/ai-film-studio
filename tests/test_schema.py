from ai_film.schema import validate_shot


def _valid_shot() -> dict:
    return {
        "schema_version": "1.0",
        "id": "S01_SH01",
        "status": "draft",
        "duration_seconds": 5,
        "continuity": {"status": "pending", "checked_at": None, "issues": []},
        "generation": {
            "image": {"status": "pending", "attempts": 0},
            "video": {"status": "pending", "attempts": 0},
            "voice": {"status": "pending", "attempts": 0},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }


def test_valid_shot_has_no_errors():
    assert validate_shot(_valid_shot()) == []


def test_shot_with_environment_field_is_valid():
    shot = _valid_shot()
    shot["environment"] = {
        "name": "hospital_corridor",
        "reference": "assets/environments/hospital_corridor/reference.png",
    }
    assert validate_shot(shot) == []


def test_missing_required_field_is_reported():
    shot = _valid_shot()
    del shot["schema_version"]
    errors = validate_shot(shot)
    assert any("schema_version" in e for e in errors)


def test_invalid_status_enum_is_reported():
    shot = _valid_shot()
    shot["status"] = "not-a-real-status"
    errors = validate_shot(shot)
    assert len(errors) == 1


def test_invalid_generation_stage_status_is_reported():
    shot = _valid_shot()
    shot["generation"]["image"]["status"] = "bogus"
    errors = validate_shot(shot)
    assert len(errors) == 1


def test_missing_generation_stage_is_reported():
    shot = _valid_shot()
    del shot["generation"]["music"]
    errors = validate_shot(shot)
    assert any("music" in e for e in errors)


def test_generation_stage_rejects_non_integer_version():
    shot = _valid_shot()
    shot["generation"]["video"]["version"] = "two"
    errors = validate_shot(shot)
    assert len(errors) == 1


def test_generation_stage_accepts_version_and_history():
    shot = _valid_shot()
    shot["generation"]["video"] = {
        "status": "completed",
        "attempts": 1,
        "version": 2,
        "history": [
            {
                "version": 1,
                "provider": "fal",
                "model": "veo-3",
                "artifact": {
                    "path": "05_video/history/S01_SH01_v1.mp4",
                    "size_bytes": 100,
                    "sha256": None,
                    "duration_seconds": 5.0,
                },
                "superseded_at": "2026-08-27T09:00:00Z",
                "superseded_reason": "regenerate",
            }
        ],
    }
    assert validate_shot(shot) == []
