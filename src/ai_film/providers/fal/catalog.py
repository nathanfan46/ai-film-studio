from __future__ import annotations

from ai_film.models import Capability, ModelInfo

_MODELS = [
    ModelInfo("fal", "nano-banana", Capability.IMAGE, "Nano Banana 2 (fast)"),
    ModelInfo("fal", "nano-banana-pro", Capability.IMAGE, "Nano Banana Pro (high fidelity)"),
    ModelInfo("fal", "veo-3", Capability.VIDEO, "Veo 3 (Google DeepMind)"),
    ModelInfo("fal", "seedance-1-0-pro", Capability.VIDEO, "Seedance 1.0 Pro"),
    ModelInfo("fal", "kling-v3-pro", Capability.VIDEO, "Kling Video v3 Pro"),
    ModelInfo(
        "fal", "hailuo-2.3", Capability.VIDEO,
        "MiniMax Hailuo 2.3 Standard (image-to-video, 768p, $0.28/6s or $0.56/10s)",
    ),
    ModelInfo(
        "fal", "hailuo-2.3-fast", Capability.VIDEO,
        "MiniMax Hailuo 2.3 Fast (image-to-video, fixed duration, cheaper/faster tier)",
    ),
    ModelInfo(
        "fal", "h3-max", Capability.VIDEO,
        "MiniMax H3 Max (image-to-video, 480p/768p, promo pricing until 2026-09-01"
        " then $0.05-0.08/s)",
    ),
    ModelInfo("fal", "csm-1b", Capability.VOICE, "CSM-1B conversational speech"),
    ModelInfo("fal", "thinksound", Capability.SFX, "ThinkSound (video-to-audio)"),
    ModelInfo("fal", "csm-1b", Capability.MUSIC, "CSM-1B (placeholder music backend)"),
]


class FalProviderCatalog:
    def models(self, capability: Capability) -> list[ModelInfo]:
        return [m for m in _MODELS if m.capability == capability]
