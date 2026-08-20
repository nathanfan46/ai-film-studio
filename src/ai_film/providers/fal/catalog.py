from __future__ import annotations

from ai_film.models import Capability, ModelInfo

_MODELS = [
    ModelInfo("fal", "nano-banana", Capability.IMAGE, "Nano Banana 2 (fast)"),
    ModelInfo("fal", "nano-banana-pro", Capability.IMAGE, "Nano Banana Pro (high fidelity)"),
    ModelInfo("fal", "veo-3", Capability.VIDEO, "Veo 3 (Google DeepMind)"),
    ModelInfo("fal", "seedance-1-0-pro", Capability.VIDEO, "Seedance 1.0 Pro"),
    ModelInfo("fal", "kling-v3-pro", Capability.VIDEO, "Kling Video v3 Pro"),
    ModelInfo("fal", "csm-1b", Capability.VOICE, "CSM-1B conversational speech"),
    ModelInfo("fal", "thinksound", Capability.SFX, "ThinkSound (video-to-audio)"),
    ModelInfo("fal", "csm-1b", Capability.MUSIC, "CSM-1B (placeholder music backend)"),
]


class FalProviderCatalog:
    def models(self, capability: Capability) -> list[ModelInfo]:
        return [m for m in _MODELS if m.capability == capability]
