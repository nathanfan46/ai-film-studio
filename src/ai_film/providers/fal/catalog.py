from __future__ import annotations

from ai_film.models import Capability, ModelInfo

_MODELS = [
    ModelInfo("fal", "nano-banana", Capability.IMAGE, "Nano Banana 2 (fast)"),
    ModelInfo("fal", "nano-banana-pro", Capability.IMAGE, "Nano Banana Pro (high fidelity)"),
    ModelInfo("fal", "veo-3", Capability.VIDEO, "Veo 3 (Google DeepMind)"),
    ModelInfo("fal", "seedance-1-0-pro", Capability.VIDEO, "Seedance 1.0 Pro"),
    ModelInfo(
        "fal", "seedance-2.5", Capability.VIDEO,
        "Seedance 2.5 (ByteDance, up to 30s native duration, 480p/720p/1080p,"
        " $0.13-0.47/s) - fewer cuts needed for one continuous beat",
    ),
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
    ModelInfo(
        "fal", "speech-02-hd", Capability.VOICE,
        "MiniMax Speech-02 HD (named voice presets, emotion/speed/pitch control,"
        " $0.10/1000 chars)",
    ),
    ModelInfo("fal", "thinksound", Capability.SFX, "ThinkSound (video-to-audio)"),
    ModelInfo(
        "fal", "cassetteai-music", Capability.MUSIC,
        "CassetteAI Music Generator (instrumental, 10-180s)",
    ),
    ModelInfo(
        "fal", "kling-lipsync", Capability.LIPSYNC,
        "Kling Lipsync (audio-driven mouth sync on an existing video, $0.014/5s)",
    ),
    ModelInfo(
        "fal", "kling-motion-control", Capability.MOTION_TRANSFER,
        "Kling v2.6 Motion Control (driving-video + reference-image -> character"
        " performs that motion, $0.07/s)",
    ),
]


class FalProviderCatalog:
    def models(self, capability: Capability) -> list[ModelInfo]:
        return [m for m in _MODELS if m.capability == capability]
