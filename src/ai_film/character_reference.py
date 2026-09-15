"""Filesystem-truth resolver for a character's reference image, across the
optional turnaround (multi-angle) set. See
docs/superpowers/specs/2026-09-14-character-turnaround-design.md."""

from __future__ import annotations

from pathlib import Path

from ai_film.services.generation_service import project_relative_path


def validate_angle_segment(angle: str) -> None:
    if not angle or "/" in angle or "\\" in angle or ".." in angle:
        raise ValueError(f"invalid orientation/angle segment: {angle!r}")


def resolve_character_reference(
    project_dir: Path, character_name: str, orientation: str | None
) -> str:
    if orientation:
        validate_angle_segment(orientation)
        candidate = (
            project_dir
            / "assets"
            / "characters"
            / character_name
            / "turnaround"
            / orientation
            / "reference.png"
        )
        if candidate.exists():
            return project_relative_path(str(candidate), project_dir)
    return f"assets/characters/{character_name}/reference.png"
