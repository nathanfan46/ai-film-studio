import pytest

from ai_film.models import Capability
from ai_film.providers.mock.image import MockImageProvider
from ai_film.providers.registry import resolve_provider


def test_resolve_provider_returns_mock_image_provider():
    provider = resolve_provider(Capability.IMAGE, "mock")
    assert isinstance(provider, MockImageProvider)


def test_resolve_provider_rejects_unknown_provider_name():
    with pytest.raises(ValueError):
        resolve_provider(Capability.IMAGE, "not-a-provider")


def test_resolve_provider_returns_fal_motion_transfer_provider():
    from ai_film.providers.fal.motion_transfer import FalMotionTransferProvider
    from ai_film.providers.registry import resolve_provider

    provider = resolve_provider(Capability.MOTION_TRANSFER, "fal")
    assert isinstance(provider, FalMotionTransferProvider)


def test_resolve_provider_returns_mock_motion_transfer_provider():
    from ai_film.providers.mock.motion_transfer import MockMotionTransferProvider
    from ai_film.providers.registry import resolve_provider

    provider = resolve_provider(Capability.MOTION_TRANSFER, "mock")
    assert isinstance(provider, MockMotionTransferProvider)
