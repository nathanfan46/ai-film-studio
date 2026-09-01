from __future__ import annotations


def _reference_legend(shot: dict) -> str:
    """Reference images are attached to the provider call as bare,
    unlabeled image_urls, in this exact order: environment (if any), then
    each character with a reference (see cli.py's
    _character_and_environment_references) — nothing in the prompt text
    ever said which image is which, or that a reference image encodes a
    fixed identity to preserve rather than a loose style cue. Confirmed as
    the cause of real character-appearance drift on a live project: even a
    single-character shot with exactly one reference image still drifted
    to a different face/hair/wardrobe than that reference, because the
    model had no signal to lock onto it."""
    environment = shot.get("environment") or {}
    labels = []
    if environment.get("reference"):
        labels.append(f'the location "{environment.get("name") or "this location"}"')
    labels += [c["name"] for c in shot.get("characters", []) if c.get("reference")]
    if not labels:
        return ""
    numbered = "; ".join(f"image {i + 1} = {label}" for i, label in enumerate(labels))
    return (
        f"Reference images attached in this order: {numbered}. Match each one's exact "
        "appearance — face, hair, skin tone, and wardrobe for characters; layout and "
        "materials for the location — do not redesign or reinterpret them."
    )


def _spatial_fragment(spatial: dict | None) -> str:
    if not spatial:
        return ""
    parts = [
        f"{character} is screen-{values['screen_side']}, facing {values['facing']}"
        for character, values in spatial.items()
    ]
    return (
        "; ".join(parts)
        + " — maintain these relative positions unless the shot's action "
        "explicitly changes them."
    )


def build_image_prompt(shot: dict, spatial: dict | None = None) -> str:
    parts = []
    legend = _reference_legend(shot)
    if legend:
        parts.append(legend)
    fragment = _spatial_fragment(spatial)
    if fragment:
        parts.append(fragment)
    parts.append(shot.get("action", ""))
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
