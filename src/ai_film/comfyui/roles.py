"""Layer A: coarse, broad pipeline-role classification for every node,
via substring matching on the node's type string -- never a prefix
match (DownloadAndLoadLivePortraitModels contains "LivePortrait" but
doesn't start with it). A misclassified or unmatched role only affects
the human-facing description; it never gates a mutation's safety. See
docs/superpowers/specs/2026-09-11-comfyui-workflow-interop-design.md,
"Two-layer semantic model"."""

from __future__ import annotations

_EXACT_ROLES = {
    "Reroute": "structural",
    "Note": "comment",
}

# Order matters only in that no two patterns here are expected to both
# match the same real type; first match wins.
_SUBSTRING_ROLES = [
    ("MimicMotion", "body_motion_transfer"),
    ("LivePortrait", "facial_performance_transfer"),
    ("ADE_", "temporal_consistency"),
    ("AnimateDiff", "temporal_consistency"),
    ("ReActor", "identity_stabilization"),
    ("FaceSwap", "identity_stabilization"),
    ("RIFE", "frame_interpolation"),
    ("VFI", "frame_interpolation"),
    ("Interpolat", "frame_interpolation"),
    ("SAM", "segmentation"),
    ("GroundingDino", "segmentation"),
    ("Segment", "segmentation"),
    ("Mask", "segmentation"),
    ("ControlNet", "control_guidance"),
    ("VHS_LoadVideo", "video_io"),
    ("VHS_VideoCombine", "video_io"),
    ("CLIPTextEncode", "conditioning"),
    ("CheckpointLoaderSimple", "model_load"),
    ("KSampler", "sampler"),
    ("LoraLoader", "lora"),
    ("VAEEncode", "latent_encode"),
    ("VAEDecode", "latent_decode"),
    ("LoadImage", "reference_image"),
    ("SaveImage", "output"),
]


def infer_role(node_type: str) -> str | None:
    if node_type in _EXACT_ROLES:
        return _EXACT_ROLES[node_type]
    for pattern, role in _SUBSTRING_ROLES:
        if pattern in node_type:
            return role
    return None
