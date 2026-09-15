from pathlib import Path

import pytest

from ai_film.character_reference import resolve_character_reference, validate_angle_segment


def test_validate_angle_segment_accepts_plain_name():
    validate_angle_segment("three_quarter")  # must not raise


@pytest.mark.parametrize("bad", ["", "../etc", "a/b", "a\\b", ".."])
def test_validate_angle_segment_rejects_unsafe_values(bad):
    with pytest.raises(ValueError):
        validate_angle_segment(bad)


def test_resolve_character_reference_falls_back_when_orientation_is_none(tmp_path: Path):
    assert resolve_character_reference(tmp_path, "mara", None) == "assets/characters/mara/reference.png"


def test_resolve_character_reference_falls_back_when_angle_not_locked(tmp_path: Path):
    assert resolve_character_reference(tmp_path, "mara", "back") == "assets/characters/mara/reference.png"


def test_resolve_character_reference_returns_turnaround_path_when_locked(tmp_path: Path):
    angle_dir = tmp_path / "assets" / "characters" / "mara" / "turnaround" / "back"
    angle_dir.mkdir(parents=True)
    (angle_dir / "reference.png").write_bytes(b"BACK-REF")

    result = resolve_character_reference(tmp_path, "mara", "back")

    assert result == "assets/characters/mara/turnaround/back/reference.png"


def test_resolve_character_reference_rejects_unsafe_orientation(tmp_path: Path):
    with pytest.raises(ValueError):
        resolve_character_reference(tmp_path, "mara", "../etc")
