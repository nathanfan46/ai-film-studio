from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from ai_film.shot_store import load_shot

VALID_SCREEN_SIDES = ("left", "center", "right")
VALID_FACINGS = ("left", "right", "camera", "away")

_SHOT_ID_RE = re.compile(r"^(S\d+)_SH(\d+)$")


def scene_id_for_shot(shot_id: str) -> str | None:
    match = _SHOT_ID_RE.match(shot_id)
    return match.group(1) if match else None


def _shot_number(shot_id: str) -> int:
    match = _SHOT_ID_RE.match(shot_id)
    if match is None:
        raise ValueError(f"shot id {shot_id!r} doesn't match S<SS>_SH<NN>")
    return int(match.group(2))


def continuity_path(project_dir: Path, scene_id: str) -> Path:
    return project_dir / "02_scenes" / f"{scene_id}.continuity.json"


def load_continuity(project_dir: Path, scene_id: str) -> dict:
    path = continuity_path(project_dir, scene_id)
    if not path.exists():
        return {
            "scene_id": scene_id, "master_shot": None,
            "master_reference_image": None, "spatial": {}, "transitions": [],
        }
    return json.loads(path.read_text())


def save_continuity(project_dir: Path, scene_id: str, data: dict) -> None:
    path = continuity_path(project_dir, scene_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".continuity-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _validate_state(screen_side: str, facing: str) -> None:
    if screen_side not in VALID_SCREEN_SIDES:
        raise ValueError(f"screen_side must be one of {VALID_SCREEN_SIDES}, got {screen_side!r}")
    if facing not in VALID_FACINGS:
        raise ValueError(f"facing must be one of {VALID_FACINGS}, got {facing!r}")


def set_scene_continuity(
    project_dir: Path,
    scene_id: str,
    character: str,
    screen_side: str,
    facing: str,
    master_shot: str | None = None,
    force: bool = False,
) -> dict:
    _validate_state(screen_side, facing)
    if master_shot is not None and scene_id_for_shot(master_shot) != scene_id:
        raise ValueError(f"master_shot {master_shot!r} does not belong to scene {scene_id!r}")

    data = load_continuity(project_dir, scene_id)
    new_state = {"screen_side": screen_side, "facing": facing}
    existing_state = data["spatial"].get(character)
    if existing_state is not None and existing_state != new_state and not force:
        raise ValueError(
            f"{character!r} already has an established state {existing_state} in "
            f"scene {scene_id!r} — pass force=True to deliberately revise it"
        )
    data["spatial"][character] = new_state

    if master_shot is not None:
        existing_master = data.get("master_shot")
        if existing_master is not None and existing_master != master_shot and not force:
            raise ValueError(
                f"scene {scene_id!r} already has master_shot={existing_master!r} "
                f"— pass force=True to deliberately change it"
            )
        data["master_shot"] = master_shot

    save_continuity(project_dir, scene_id, data)
    return data


def add_continuity_transition(
    project_dir: Path,
    scene_id: str,
    after_shot: str,
    character: str,
    screen_side: str,
    facing: str,
    reason: str,
) -> dict:
    _validate_state(screen_side, facing)
    if scene_id_for_shot(after_shot) != scene_id:
        raise ValueError(f"after_shot {after_shot!r} does not belong to scene {scene_id!r}")

    data = load_continuity(project_dir, scene_id)
    new_state = {"screen_side": screen_side, "facing": facing}
    for transition in data["transitions"]:
        if transition["after_shot"] == after_shot:
            transition["changes"][character] = new_state
            transition["reason"] = reason
            save_continuity(project_dir, scene_id, data)
            return data
    data["transitions"].append({
        "after_shot": after_shot,
        "changes": {character: new_state},
        "reason": reason,
    })
    save_continuity(project_dir, scene_id, data)
    return data


def lock_continuity_master(project_dir: Path, scene_id: str, force: bool = False) -> dict:
    data = load_continuity(project_dir, scene_id)
    master_shot = data.get("master_shot")
    if not master_shot:
        raise ValueError(
            f"scene {scene_id!r} has no master_shot set — call set_scene_continuity first"
        )
    if data.get("master_reference_image") and not force:
        raise ValueError(
            f"scene {scene_id!r} already has a locked master reference "
            f"— pass force=True to deliberately re-lock it"
        )

    shot_path = project_dir / "03_shots" / f"{master_shot}.json"
    if not shot_path.exists():
        raise ValueError(f"master shot {master_shot!r} has no shot.json at {shot_path}")
    shot_data = load_shot(shot_path)
    artifact = shot_data.get("generation", {}).get("image", {}).get("artifact")
    if not artifact or not artifact.get("path"):
        raise ValueError(f"master shot {master_shot!r} has no locked image yet")

    source = project_dir / artifact["path"]
    dest = project_dir / "02_scenes" / f"{scene_id}_master_reference.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(source, dest)
    data["master_reference_image"] = str(dest.relative_to(project_dir))
    save_continuity(project_dir, scene_id, data)
    return data


def effective_spatial_state(continuity: dict, shot_id: str) -> dict[str, dict]:
    target_num = _shot_number(shot_id)
    state = {name: dict(values) for name, values in continuity.get("spatial", {}).items()}
    transitions = sorted(
        continuity.get("transitions", []), key=lambda t: _shot_number(t["after_shot"]),
    )
    for transition in transitions:
        if _shot_number(transition["after_shot"]) < target_num:
            for character, new_values in transition["changes"].items():
                state[character] = dict(new_values)
    return state
