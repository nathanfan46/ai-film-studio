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
    dialogue = shot.get("dialogue", {})
    speaker = dialogue.get("speaker", "")
    on_screen_names = {c.get("name") for c in shot.get("characters", [])}
    if dialogue.get("text") and speaker in on_screen_names:
        # Only inject when the speaker is actually visible in the shot.
        # Video models capable of their own auto-generated lip-synced
        # speech (e.g. fal's h3-max) otherwise have to guess dialogue from
        # scene description alone and animate mouth movement to match their
        # own guess — giving them the real line at least lets that guess be
        # right. For off-screen dialogue (a phone call, narration) there is
        # no correct face to animate to these exact words, so leaving it
        # out here is deliberate, not a gap: injecting it would just hand
        # the model concrete wrong words to lip-sync whoever IS on screen
        # to, which is worse than an unguided guess.
        parts.append(f'{speaker} says: "{dialogue["text"]}"')
    return ", ".join(part for part in parts if part)
