from __future__ import annotations

from ai_film.models import Capability
from ai_film.providers.fal.audio import FalAudioProvider
from ai_film.providers.fal.image import FalImageProvider
from ai_film.providers.fal.video import FalVideoProvider
from ai_film.providers.mock.audio import MockAudioProvider
from ai_film.providers.mock.image import MockImageProvider
from ai_film.providers.mock.video import MockVideoProvider

_REGISTRIES = {
    Capability.IMAGE: {"fal": FalImageProvider, "mock": MockImageProvider},
    Capability.VIDEO: {"fal": FalVideoProvider, "mock": MockVideoProvider},
    Capability.VOICE: {"fal": FalAudioProvider, "mock": MockAudioProvider},
    Capability.SFX: {"fal": FalAudioProvider, "mock": MockAudioProvider},
    Capability.MUSIC: {"fal": FalAudioProvider, "mock": MockAudioProvider},
}


def resolve_provider(capability: Capability, provider_name: str):
    registry = _REGISTRIES[capability]
    if provider_name not in registry:
        raise ValueError(
            f"unknown provider {provider_name!r} for {capability.value}; "
            f"available: {sorted(registry)}"
        )
    return registry[provider_name]()
