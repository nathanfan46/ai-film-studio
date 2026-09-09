"""Read-only report of which shots were generated against a character or
environment reference image that has since changed. See
docs/superpowers/specs/2026-09-04-template-library-design.md's "Asset
staleness tracking" section. Never triggers regeneration itself."""

from __future__ import annotations

from pathlib import Path

from ai_film.services.generation_service import sha256_of_file
from ai_film.shot_store import list_shot_paths, load_shot


def check_stale(project_dir: Path) -> list[dict]:
    shots_dir = project_dir / "03_shots"
    if not shots_dir.exists():
        return []

    stale = []
    for shot_path in list_shot_paths(shots_dir):
        shot = load_shot(shot_path)
        image_stage = shot.get("generation", {}).get("image", {})
        if image_stage.get("status") != "completed":
            continue

        changed_assets = []
        for asset in image_stage.get("source_assets") or []:
            asset_path = project_dir / asset["path"]
            if not asset_path.exists():
                continue
            if sha256_of_file(asset_path) != asset["sha256"]:
                changed_assets.append(asset["path"])

        if changed_assets:
            stale.append({"shot_id": shot["id"], "changed_assets": changed_assets})

    return stale
