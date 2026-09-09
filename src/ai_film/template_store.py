"""Local file operations for the template library — save/list/show/
export/import. No provider abstraction, no generation calls; this module
only validates and moves files. See
docs/superpowers/specs/2026-09-04-template-library-design.md."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ai_film.schema import validate_template


def save_template(
    from_path: Path, template_id: str, templates_dir: Path, force: bool = False
) -> dict:
    if not from_path.exists():
        raise ValueError(f"draft file not found: {from_path}")

    draft = json.loads(from_path.read_text())
    errors = validate_template(draft)
    if errors:
        raise ValueError(f"draft failed schema validation: {'; '.join(errors)}")

    target_dir = templates_dir / template_id
    if target_dir.exists():
        if not force:
            raise RuntimeError(f"{target_dir} already exists — pass --force to overwrite")
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True)

    keyframes_dir = target_dir / "keyframes"
    for pattern in draft.get("shot_patterns", []):
        keyframe = pattern.get("reference_keyframe")
        if not keyframe:
            continue
        source_keyframe = from_path.parent / keyframe
        if not source_keyframe.exists():
            raise ValueError(f"reference_keyframe not found: {source_keyframe}")
        keyframes_dir.mkdir(parents=True, exist_ok=True)
        dest = keyframes_dir / Path(keyframe).name
        shutil.copyfile(source_keyframe, dest)
        pattern["reference_keyframe"] = f"keyframes/{dest.name}"

    draft["id"] = template_id
    (target_dir / "template.json").write_text(json.dumps(draft, indent=2, ensure_ascii=False))
    return draft


def list_templates(templates_dir: Path) -> list[dict]:
    if not templates_dir.exists():
        return []
    results = []
    for template_dir in sorted(p for p in templates_dir.iterdir() if p.is_dir()):
        template_path = template_dir / "template.json"
        if not template_path.exists():
            continue
        data = json.loads(template_path.read_text())
        results.append({
            "id": data.get("id", template_dir.name),
            "name": data.get("name", ""),
            "shot_pattern_count": len(data.get("shot_patterns", [])),
        })
    return results


def show_template(template_id: str, templates_dir: Path) -> dict:
    template_path = templates_dir / template_id / "template.json"
    if not template_path.exists():
        raise ValueError(f"template not found: {template_id}")
    return json.loads(template_path.read_text())
