from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_film.models import (
    Capability,
    ImageGenerationRequest,
    JobStatus,
    MusicGenerationRequest,
    SfxGenerationRequest,
    VoiceGenerationRequest,
)
from ai_film.providers.fal.audio import FalAudioProvider
from ai_film.providers.fal.catalog import FalProviderCatalog
from ai_film.providers.fal.image import FalImageProvider


def test_catalog_lists_image_models_only_for_image_capability():
    catalog = FalProviderCatalog()
    models = catalog.models(Capability.IMAGE)
    assert models, "expected at least one image model"
    assert all(m.capability == Capability.IMAGE and m.provider == "fal" for m in models)


@patch("ai_film.providers.fal.client.requests")
def test_fal_image_provider_full_lifecycle(mock_requests, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")

    submit_response = MagicMock(status_code=200)
    submit_response.json.return_value = {
        "request_id": "req-1",
        "status_url": "https://queue.fal.run/status/req-1",
        "response_url": "https://queue.fal.run/result/req-1",
    }
    status_response = MagicMock(status_code=200)
    status_response.json.return_value = {"status": "COMPLETED"}
    result_response = MagicMock(status_code=200)
    result_response.json.return_value = {"images": [{"url": "https://cdn.fal.run/out.png"}]}
    download_response = MagicMock(status_code=200, content=b"PNG-BYTES")

    mock_requests.post.return_value = submit_response
    mock_requests.get.side_effect = [status_response, result_response, download_response]

    provider = FalImageProvider()
    output_path = tmp_path / "SH01.png"
    request = ImageGenerationRequest(
        prompt="a girl in a corridor", model="nano-banana",
        reference_paths=[], output_path=str(output_path),
    )
    job = provider.submit(request)
    assert job.provider == "fal"
    assert job.id == "req-1"
    assert provider.poll(job) == JobStatus.COMPLETED
    result = provider.get_result(job)
    assert result.artifact_path == str(output_path)
    assert output_path.read_bytes() == b"PNG-BYTES"


@patch("ai_film.providers.fal.client.requests")
def test_fal_audio_provider_tags_each_submit_with_its_own_capability(
    mock_requests, tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("FAL_KEY", "test-key")

    def submit_response(request_id: str) -> MagicMock:
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "request_id": request_id,
            "status_url": f"https://queue.fal.run/status/{request_id}",
            "response_url": f"https://queue.fal.run/result/{request_id}",
        }
        return response

    mock_requests.post.side_effect = [
        submit_response("req-voice"),
        submit_response("req-sfx"),
        submit_response("req-music"),
    ]

    provider = FalAudioProvider()

    voice_job = provider.submit_voice(
        VoiceGenerationRequest(
            text="hello", model="csm-1b", output_path=str(tmp_path / "voice.wav")
        )
    )
    sfx_job = provider.submit_sfx(
        SfxGenerationRequest(
            prompt="door creak", model="thinksound", output_path=str(tmp_path / "sfx.wav")
        )
    )
    music_job = provider.submit_music(
        MusicGenerationRequest(
            prompt="tense strings", model="csm-1b", output_path=str(tmp_path / "music.wav")
        )
    )

    assert voice_job.capability == Capability.VOICE
    assert sfx_job.capability == Capability.SFX
    assert music_job.capability == Capability.MUSIC
    assert {voice_job.capability, sfx_job.capability, music_job.capability} == {
        Capability.VOICE,
        Capability.SFX,
        Capability.MUSIC,
    }


@patch("ai_film.providers.fal.client.requests")
def test_fal_client_raises_provider_error_without_fal_key(mock_requests, tmp_path, monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    from ai_film.errors import ProviderError

    provider = FalImageProvider()
    request = ImageGenerationRequest(
        prompt="x", model="nano-banana", reference_paths=[],
        output_path=str(tmp_path / "x.png"),
    )
    try:
        provider.submit(request)
        assert False, "expected ProviderError"
    except ProviderError as exc:
        assert "FAL_KEY" in str(exc)
