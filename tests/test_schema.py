from ai_film.schema import validate_shot, TEMPLATE_SCHEMA, validate_template


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


def test_shot_schema_accepts_optional_format_field():
    shot = _valid_shot()
    shot["format"] = {"resolution": "1280x720", "fps": 24}
    assert validate_shot(shot) == []


def test_shot_schema_accepts_missing_format_field():
    shot = _valid_shot()
    assert "format" not in shot
    assert validate_shot(shot) == []


def test_shot_schema_accepts_optional_driving_video_field():
    shot = _valid_shot()
    shot["driving_video"] = {"path": "05_video/reference_clips/dance.mp4"}
    assert validate_shot(shot) == []


def test_shot_schema_accepts_missing_driving_video_field():
    shot = _valid_shot()
    assert "driving_video" not in shot
    assert validate_shot(shot) == []


def _valid_template() -> dict:
    return {
        "schema_version": "1.0",
        "id": "hero-orbit",
        "name": "Hero Orbit Reveal",
        "created_at": "2026-09-04T12:00:00Z",
        "source_note": "Reference clip of a hero introduction",
        "shot_patterns": [
            {
                "order": 0,
                "pattern_name": "establish",
                "camera": "wide shot, static",
                "subject_motion": "N/A",
                "framing": "wide, subject not yet visible",
                "suggested_duration_seconds": 3,
                "reference_keyframe": None,
            }
        ],
    }


def test_validate_template_accepts_valid_template():
    assert validate_template(_valid_template()) == []


def test_validate_template_rejects_missing_shot_patterns():
    template = _valid_template()
    del template["shot_patterns"]
    errors = validate_template(template)
    assert any("shot_patterns" in e for e in errors)


def test_validate_template_rejects_shot_pattern_missing_camera():
    template = _valid_template()
    del template["shot_patterns"][0]["camera"]
    errors = validate_template(template)
    assert any("camera" in e for e in errors)


def test_validate_template_allows_null_reference_keyframe_and_duration():
    template = _valid_template()
    template["shot_patterns"][0]["reference_keyframe"] = None
    template["shot_patterns"][0]["suggested_duration_seconds"] = None
    assert validate_template(template) == []
