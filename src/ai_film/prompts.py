from __future__ import annotations


def build_image_prompt(shot: dict) -> str:
    parts = [shot.get("action", "")]
    visual = shot.get("visual", {})
    if visual.get("style"):
        parts.append(f"style: {visual['style']}")
    if visual.get("lighting"):
        parts.append(f"lighting: {visual['lighting']}")
    camera = shot.get("camera", {})
    if camera.get("shot"):
        parts.append(f"{camera['shot']} shot")
    return ", ".join(part for part in parts if part)


def build_video_prompt(shot: dict) -> str:
    parts = [build_image_prompt(shot)]
    camera = shot.get("camera", {})
    if camera.get("movement"):
        parts.append(f"camera movement: {camera['movement']}")
    return ", ".join(part for part in parts if part)
