from pathlib import Path

import pytest

from ai_film.errors import ProviderError
from ai_film.models import (
    Capability,
    ImageGenerationRequest,
    JobStatus,
    MusicGenerationRequest,
    SfxGenerationRequest,
    VoiceGenerationRequest,
)
from ai_film.providers.mock.image import MockImageProvider
from ai_film.providers.mock.audio import MockAudioProvider


def test_mock_image_provider_completes_and_writes_artifact(tmp_path: Path):
    provider = MockImageProvider()
    output_path = tmp_path / "shot.png"
    request = ImageGenerationRequest(
        prompt="a girl in a corridor", model="nano-banana",
        reference_paths=[], output_path=str(output_path),
    )
    job = provider.submit(request)
    assert job.provider == "mock"
    status = provider.poll(job)
    assert status == JobStatus.COMPLETED
    result = provider.get_result(job)
    assert result.artifact_path == str(output_path)
    assert output_path.exists()
    assert result.size_bytes > 0


def test_mock_image_provider_simulates_submit_failures(tmp_path: Path):
    provider = MockImageProvider(fail_first_n_submits=2)
    request = ImageGenerationRequest(
        prompt="x", model="nano-banana", reference_paths=[],
        output_path=str(tmp_path / "shot.png"),
    )
    with pytest.raises(ProviderError):
        provider.submit(request)
    with pytest.raises(ProviderError):
        provider.submit(request)
    job = provider.submit(request)  # third call succeeds
    assert provider.poll(job) == JobStatus.COMPLETED


def test_mock_audio_provider_submit_voice(tmp_path: Path):
    provider = MockAudioProvider()
    request = VoiceGenerationRequest(
        text="你終於來了。", model="csm-1b", speaker="girl",
        output_path=str(tmp_path / "dialogue.wav"),
    )
    job = provider.submit_voice(request)
    assert job.capability == Capability.VOICE
    assert provider.poll(job) == JobStatus.COMPLETED
    result = provider.get_result(job)
    assert Path(result.artifact_path).exists()


def test_mock_audio_provider_submit_sfx(tmp_path: Path):
    provider = MockAudioProvider()
    request = SfxGenerationRequest(
        prompt="door slam", model="sfx-v1",
        output_path=str(tmp_path / "sfx.wav"),
    )
    job = provider.submit_sfx(request)
    assert job.capability == Capability.SFX
    assert provider.poll(job) == JobStatus.COMPLETED
    result = provider.get_result(job)
    assert Path(result.artifact_path).exists()


def test_mock_audio_provider_submit_music(tmp_path: Path):
    provider = MockAudioProvider()
    request = MusicGenerationRequest(
        prompt="upbeat electronic", model="music-v1", duration_seconds=30.0,
        output_path=str(tmp_path / "music.wav"),
    )
    job = provider.submit_music(request)
    assert job.capability == Capability.MUSIC
    assert provider.poll(job) == JobStatus.COMPLETED
    result = provider.get_result(job)
    assert Path(result.artifact_path).exists()
