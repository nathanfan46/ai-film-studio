"""Local file operations for the template library — save/list/show/
export/import. No provider abstraction, no generation calls; this module
only validates and moves files. See
docs/superpowers/specs/2026-09-04-template-library-design.md."""

from __future__ import annotations

import json
import re
import shutil
import zipfile
from pathlib import Path

from ai_film.schema import validate_template

_TEMPLATE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _validate_template_id(template_id: str) -> None:
    if (
        "/" in template_id
        or "\\" in template_id
        or ".." in template_id
        or not _TEMPLATE_ID_PATTERN.match(template_id)
    ):
        raise ValueError(f"invalid template id: {template_id!r}")


def save_template(
    from_path: Path, template_id: str, templates_dir: Path, force: bool = False
) -> dict:
    _validate_template_id(template_id)

    if not from_path.exists():
        raise ValueError(f"draft file not found: {from_path}")

    draft = json.loads(from_path.read_text())
    errors = validate_template(draft)
    if errors:
        raise ValueError(f"draft failed schema validation: {'; '.join(errors)}")

    target_dir = templates_dir / template_id
    if target_dir.exists() and not force:
        raise RuntimeError(f"{target_dir} already exists — pass --force to overwrite")

    # Validate every referenced keyframe exists before touching the target
    # directory, so a --force save with a bad keyframe path doesn't destroy
    # the previously-saved template.
    for pattern in draft.get("shot_patterns", []):
        keyframe = pattern.get("reference_keyframe")
        if not keyframe:
            continue
        source_keyframe = from_path.parent / keyframe
        if not source_keyframe.exists():
            raise ValueError(f"reference_keyframe not found: {source_keyframe}")

    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True)

    keyframes_dir = target_dir / "keyframes"
    for pattern in draft.get("shot_patterns", []):
        keyframe = pattern.get("reference_keyframe")
        if not keyframe:
            continue
        source_keyframe = from_path.parent / keyframe
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
        try:
            data = json.loads(template_path.read_text())
        except json.JSONDecodeError:
            continue
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


def export_template(template_id: str, templates_dir: Path, output_path: Path) -> None:
    source_dir = templates_dir / template_id
    if not source_dir.exists():
        raise ValueError(f"template not found: {template_id}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(source_dir.rglob("*")):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(source_dir))


def import_template(
    from_path: Path,
    templates_dir: Path,
    template_id: str | None = None,
    force: bool = False,
) -> dict:
    if not from_path.exists():
        raise ValueError(f"archive not found: {from_path}")

    try:
        zf_context = zipfile.ZipFile(from_path)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"{from_path} is not a valid zip archive") from exc

    with zf_context as zf:
        names = zf.namelist()
        if "template.json" not in names:
            raise ValueError(f"{from_path} does not contain a template.json")
        draft = json.loads(zf.read("template.json"))
        errors = validate_template(draft)
        if errors:
            raise ValueError(f"imported template.json failed schema validation: {'; '.join(errors)}")

        resolved_id = template_id or draft.get("id")
        if not resolved_id:
            raise ValueError("no --id given and template.json has no id field")
        _validate_template_id(resolved_id)

        target_dir = templates_dir / resolved_id
        if target_dir.exists():
            if not force:
                raise RuntimeError(f"{target_dir} already exists — pass --force to overwrite")
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True)
        zf.extractall(target_dir)

    draft["id"] = resolved_id
    (target_dir / "template.json").write_text(json.dumps(draft, indent=2, ensure_ascii=False))
    return draft
