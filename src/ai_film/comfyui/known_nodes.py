"""Layer B: the small, hand-curated set of ComfyUI node types this
tool can read/write specific semantic fields on. Deliberately not
grown ahead of real usage -- see the spec's "Two-layer semantic model"
and its non-goal against pre-emptively expanding this table. Each
entry's "widgets" list is that type's declared widget order (verified
against real ComfyUI behavior, never guessed), consumed by
widget_addressing.py to resolve a semantic field name to its actual
widgets_values position or dict key on one specific node instance."""

from __future__ import annotations

KNOWN_NODE_REGISTRY: dict[str, dict] = {
    "CheckpointLoaderSimple": {
        "addressing": "array",
        "widgets": ["ckpt_name"],
    },
    "CLIPTextEncode": {
        "addressing": "array",
        "widgets": ["text"],
    },
    "KSampler": {
        "addressing": "array",
        "widgets": [
            "seed", "control_after_generate", "steps", "cfg",
            "sampler_name", "scheduler", "denoise",
        ],
    },
    "KSamplerAdvanced": {
        "addressing": "array",
        "widgets": [
            "add_noise", "noise_seed", "control_after_generate", "steps", "cfg",
            "sampler_name", "scheduler", "start_at_step", "end_at_step",
            "return_with_leftover_noise",
        ],
    },
    "VAEEncode": {
        "addressing": "array",
        "widgets": [],
    },
    "VAEDecode": {
        "addressing": "array",
        "widgets": [],
    },
    "EmptyLatentImage": {
        "addressing": "array",
        "widgets": ["width", "height", "batch_size"],
    },
    "LoadImage": {
        "addressing": "array",
        "widgets": ["image", "upload"],
    },
    "SaveImage": {
        "addressing": "array",
        "widgets": ["filename_prefix"],
    },
    "LoraLoader": {
        "addressing": "array",
        "widgets": ["lora_name", "strength_model", "strength_clip"],
    },
    "LoraLoaderModelOnly": {
        "addressing": "array",
        "widgets": ["lora_name", "strength_model"],
    },
}
