from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_film.models import (
    Capability,
    ImageEditRequest,
    ImageGenerationRequest,
    JobStatus,
    LipsyncGenerationRequest,
    MusicGenerationRequest,
    SfxGenerationRequest,
    VideoGenerationRequest,
    VoiceGenerationRequest,
)
from ai_film.providers.fal.audio import FalAudioProvider, _speaker_id
from ai_film.providers.fal.catalog import FalProviderCatalog
from ai_film.providers.fal.image import FalImageProvider
from ai_film.providers.fal.lipsync import FalLipsyncProvider
from ai_film.providers.fal.video import FalVideoProvider


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


def test_speaker_id_gives_distinct_ids_for_distinct_character_names():
    """Regression guard: an earlier version of _speaker_id used `% 5`, which
    collided for these exact two names (Doctor and Eli Voss both hashed to
    1) — two different characters silently getting the same voice, on a
    real story that hit this in production."""
    ids = {name: _speaker_id(name) for name in ("Doctor", "Eli Voss", "Mara Voss")}
    assert len(set(ids.values())) == 3


def test_speaker_id_is_deterministic_for_the_same_name():
    assert _speaker_id("Mara Voss") == _speaker_id("Mara Voss")


def test_speaker_id_empty_speaker_returns_zero():
    assert _speaker_id("") == 0


@patch("ai_film.providers.fal.client.requests")
def test_submit_voice_sends_scene_structure_with_derived_speaker_id(
    mock_requests, tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("FAL_KEY", "test-key")
    submit_response = MagicMock(status_code=200)
    submit_response.json.return_value = {
        "request_id": "req-voice", "status_url": "https://queue.fal.run/status/req-voice",
        "response_url": "https://queue.fal.run/result/req-voice",
    }
    mock_requests.post.return_value = submit_response

    provider = FalAudioProvider()
    provider.submit_voice(
        VoiceGenerationRequest(
            text="hello", model="csm-1b", speaker="Mara Voss",
            output_path=str(tmp_path / "voice.wav"),
        )
    )

    sent_input = mock_requests.post.call_args.kwargs["json"]
    assert sent_input == {"scene": [{"speaker_id": _speaker_id("Mara Voss"), "text": "hello"}]}


@patch("ai_film.providers.fal.client.requests")
def test_submit_voice_honors_explicit_speaker_id_override(mock_requests, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    submit_response = MagicMock(status_code=200)
    submit_response.json.return_value = {
        "request_id": "req-voice", "status_url": "https://queue.fal.run/status/req-voice",
        "response_url": "https://queue.fal.run/result/req-voice",
    }
    mock_requests.post.return_value = submit_response

    provider = FalAudioProvider()
    provider.submit_voice(
        VoiceGenerationRequest(
            text="hello", model="csm-1b", speaker="Mara Voss", speaker_id=42,
            output_path=str(tmp_path / "voice.wav"),
        )
    )

    sent_input = mock_requests.post.call_args.kwargs["json"]
    assert sent_input == {"scene": [{"speaker_id": 42, "text": "hello"}]}


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


def test_fal_image_provider_supports_edit_returns_true():
    provider = FalImageProvider()
    assert provider.supports_edit() is True


@patch("ai_film.providers.fal.client.requests")
def test_fal_image_provider_get_results_downloads_all_images(mock_requests, tmp_path: Path, monkeypatch):
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
    result_response.json.return_value = {
        "images": [
            {"url": "https://cdn.fal.run/a.png"},
            {"url": "https://cdn.fal.run/b.png"},
            {"url": "https://cdn.fal.run/c.png"},
        ]
    }
    download_responses = [
        MagicMock(status_code=200, content=b"IMG-A"),
        MagicMock(status_code=200, content=b"IMG-B"),
        MagicMock(status_code=200, content=b"IMG-C"),
    ]

    mock_requests.post.return_value = submit_response
    mock_requests.get.side_effect = [status_response, result_response, *download_responses]

    provider = FalImageProvider()
    request = ImageGenerationRequest(
        prompt="a girl", model="nano-banana", num_candidates=3, reference_paths=[],
    )
    job = provider.submit(request)
    provider.poll(job)
    output_dir = tmp_path / "candidates"
    results = provider.get_results(job, str(output_dir))

    assert len(results) == 3
    assert {r.artifact_path for r in results} == {
        str(output_dir / "result_1.png"),
        str(output_dir / "result_2.png"),
        str(output_dir / "result_3.png"),
    }
    assert (output_dir / "result_1.png").read_bytes() == b"IMG-A"
    assert (output_dir / "result_2.png").read_bytes() == b"IMG-B"
    assert (output_dir / "result_3.png").read_bytes() == b"IMG-C"


@patch("ai_film.providers.fal.client.requests")
def test_fal_image_provider_submit_edit_full_lifecycle(mock_requests, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")

    upload_initiate_response = MagicMock(status_code=200)
    upload_initiate_response.json.return_value = {
        "upload_url": "https://up.fal.media/put/edit-upload",
        "file_url": "https://cdn.fal.run/uploaded/001.png",
    }
    upload_put_response = MagicMock(status_code=200)
    submit_response = MagicMock(status_code=200)
    submit_response.json.return_value = {
        "request_id": "req-edit-1",
        "status_url": "https://queue.fal.run/status/req-edit-1",
        "response_url": "https://queue.fal.run/result/req-edit-1",
    }
    status_response = MagicMock(status_code=200)
    status_response.json.return_value = {"status": "COMPLETED"}
    result_response = MagicMock(status_code=200)
    result_response.json.return_value = {"images": [{"url": "https://cdn.fal.run/edited.png"}]}
    download_response = MagicMock(status_code=200, content=b"EDITED-PNG")

    mock_requests.post.side_effect = [upload_initiate_response, submit_response]
    mock_requests.put.return_value = upload_put_response
    mock_requests.get.side_effect = [status_response, result_response, download_response]

    provider = FalImageProvider()
    base_image = tmp_path / "001.png"
    base_image.write_bytes(b"ORIGINAL-PNG")
    output_path = tmp_path / "candidates" / "edited.png"
    request = ImageEditRequest(
        base_image_path=str(base_image), instruction="black jacket instead of white",
    )
    job = provider.submit_edit(request)
    assert job.provider == "fal"
    assert provider.poll(job) == JobStatus.COMPLETED
    result = provider.get_result(job)
    assert Path(result.artifact_path).read_bytes() == b"EDITED-PNG"


def _mock_submit_response(mock_requests) -> None:
    submit_response = MagicMock(status_code=200)
    submit_response.json.return_value = {
        "request_id": "req-video", "status_url": "https://queue.fal.run/status/req-video",
        "response_url": "https://queue.fal.run/result/req-video",
    }
    mock_requests.post.return_value = submit_response


@patch("ai_film.providers.fal.client.requests")
def test_video_provider_uses_image_to_video_endpoint_when_a_reference_is_present(
    mock_requests, tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("FAL_KEY", "test-key")
    _mock_submit_response(mock_requests)
    reference = tmp_path / "ref.png"
    reference.write_bytes(b"REF-PNG")
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file", lambda path: "https://cdn.fal.run/ref.png"
    )

    provider = FalVideoProvider()
    provider.submit(
        VideoGenerationRequest(
            prompt="a girl walks", model="veo-3", reference_paths=[str(reference)],
            duration_seconds=6, output_path=str(tmp_path / "out.mp4"),
        )
    )

    called_url = mock_requests.post.call_args.args[0]
    assert called_url == "https://queue.fal.run/fal-ai/veo3/image-to-video"
    sent_input = mock_requests.post.call_args.kwargs["json"]
    assert sent_input["image_url"] == "https://cdn.fal.run/ref.png"
    assert sent_input["generate_audio"] is False


@patch("ai_film.providers.fal.client.requests")
def test_video_provider_uses_text_to_video_endpoint_without_a_reference(
    mock_requests, tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("FAL_KEY", "test-key")
    _mock_submit_response(mock_requests)

    provider = FalVideoProvider()
    provider.submit(
        VideoGenerationRequest(
            prompt="a girl walks", model="veo-3", reference_paths=[],
            duration_seconds=6, output_path=str(tmp_path / "out.mp4"),
        )
    )

    called_url = mock_requests.post.call_args.args[0]
    assert called_url == "https://queue.fal.run/fal-ai/veo3"
    sent_input = mock_requests.post.call_args.kwargs["json"]
    assert "image_url" not in sent_input
    assert sent_input["generate_audio"] is False


@patch("ai_film.providers.fal.client.requests")
def test_video_provider_falls_back_to_text_endpoint_for_models_without_an_i2v_mapping(
    mock_requests, tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("FAL_KEY", "test-key")
    _mock_submit_response(mock_requests)
    reference = tmp_path / "ref.png"
    reference.write_bytes(b"REF-PNG")
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file", lambda path: "https://cdn.fal.run/ref.png"
    )

    provider = FalVideoProvider()
    provider.submit(
        VideoGenerationRequest(
            prompt="a girl walks", model="seedance-1-0-pro", reference_paths=[str(reference)],
            duration_seconds=6, output_path=str(tmp_path / "out.mp4"),
        )
    )

    called_url = mock_requests.post.call_args.args[0]
    assert called_url == "https://queue.fal.run/fal-ai/seedance-1-0-pro"
    sent_input = mock_requests.post.call_args.kwargs["json"]
    assert sent_input["image_url"] == "https://cdn.fal.run/ref.png"
    assert "generate_audio" not in sent_input


def test_catalog_lists_the_three_minimax_video_models():
    catalog = FalProviderCatalog()
    model_names = {m.model for m in catalog.models(Capability.VIDEO)}
    assert {"hailuo-2.3", "hailuo-2.3-fast", "h3-max"} <= model_names


@patch("ai_film.providers.fal.client.requests")
def test_hailuo_2_3_sends_bare_string_duration(mock_requests, tmp_path: Path, monkeypatch):
    """Verified against fal.ai's OpenAPI schema for
    fal-ai/minimax/hailuo-2.3/standard/image-to-video: duration is a string
    enum of "6"/"10", not "6s"/"10s" like veo-3."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    _mock_submit_response(mock_requests)
    reference = tmp_path / "ref.png"
    reference.write_bytes(b"REF-PNG")
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file", lambda path: "https://cdn.fal.run/ref.png"
    )

    provider = FalVideoProvider()
    provider.submit(
        VideoGenerationRequest(
            prompt="a girl walks", model="hailuo-2.3", reference_paths=[str(reference)],
            duration_seconds=7, output_path=str(tmp_path / "out.mp4"),
        )
    )

    called_url = mock_requests.post.call_args.args[0]
    assert called_url == "https://queue.fal.run/fal-ai/minimax/hailuo-2.3/standard/image-to-video"
    sent_input = mock_requests.post.call_args.kwargs["json"]
    assert sent_input["duration"] == "6"  # 7s snaps to the nearest allowed value, 6
    assert sent_input["image_url"] == "https://cdn.fal.run/ref.png"
    assert "generate_audio" not in sent_input


@patch("ai_film.providers.fal.client.requests")
def test_hailuo_2_3_fast_omits_duration_field_entirely(mock_requests, tmp_path: Path, monkeypatch):
    """Verified against fal.ai's OpenAPI schema for
    fal-ai/minimax/hailuo-2.3-fast/pro/image-to-video: there is no
    "duration" field declared at all — the clip length is fixed."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    _mock_submit_response(mock_requests)
    reference = tmp_path / "ref.png"
    reference.write_bytes(b"REF-PNG")
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file", lambda path: "https://cdn.fal.run/ref.png"
    )

    provider = FalVideoProvider()
    provider.submit(
        VideoGenerationRequest(
            prompt="a girl walks", model="hailuo-2.3-fast", reference_paths=[str(reference)],
            duration_seconds=6, output_path=str(tmp_path / "out.mp4"),
        )
    )

    called_url = mock_requests.post.call_args.args[0]
    assert called_url == "https://queue.fal.run/fal-ai/minimax/hailuo-2.3-fast/pro/image-to-video"
    sent_input = mock_requests.post.call_args.kwargs["json"]
    assert "duration" not in sent_input
    assert sent_input["image_url"] == "https://cdn.fal.run/ref.png"


@patch("ai_film.providers.fal.client.requests")
def test_h3_max_sends_integer_duration_and_prompt_expansion_mode(
    mock_requests, tmp_path: Path, monkeypatch
):
    """Verified against fal.ai's OpenAPI schema for
    minimax/h3-max/image-to-video: duration is a plain integer (5-15s), not
    a string, and prompt_expansion_mode is required (default "balanced")."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    _mock_submit_response(mock_requests)
    reference = tmp_path / "ref.png"
    reference.write_bytes(b"REF-PNG")
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file", lambda path: "https://cdn.fal.run/ref.png"
    )

    provider = FalVideoProvider()
    provider.submit(
        VideoGenerationRequest(
            prompt="a girl walks", model="h3-max", reference_paths=[str(reference)],
            duration_seconds=5, output_path=str(tmp_path / "out.mp4"),
        )
    )

    called_url = mock_requests.post.call_args.args[0]
    assert called_url == "https://queue.fal.run/minimax/h3-max/image-to-video"
    sent_input = mock_requests.post.call_args.kwargs["json"]
    assert sent_input["duration"] == 5
    assert isinstance(sent_input["duration"], int)
    assert sent_input["prompt_expansion_mode"] == "balanced"
    assert sent_input["image_url"] == "https://cdn.fal.run/ref.png"
    assert "generate_audio" not in sent_input


@patch("ai_film.providers.fal.client.requests")
def test_lipsync_provider_uploads_video_and_audio_and_returns_original_duration(
    mock_requests, tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("FAL_KEY", "test-key")
    submit_response = MagicMock(status_code=200)
    submit_response.json.return_value = {
        "request_id": "req-lipsync", "status_url": "https://queue.fal.run/status/req-lipsync",
        "response_url": "https://queue.fal.run/result/req-lipsync",
    }
    status_response = MagicMock(status_code=200)
    status_response.json.return_value = {"status": "COMPLETED"}
    result_response = MagicMock(status_code=200)
    result_response.json.return_value = {"video": {"url": "https://cdn.fal.run/synced.mp4"}}
    download_response = MagicMock(status_code=200, content=b"SYNCED-MP4")

    mock_requests.post.return_value = submit_response
    mock_requests.get.side_effect = [status_response, result_response, download_response]
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file",
        lambda path: f"https://cdn.fal.run/{Path(path).name}",
    )

    provider = FalLipsyncProvider()
    output_path = tmp_path / "synced.mp4"
    request = LipsyncGenerationRequest(
        video_path=str(tmp_path / "video.mp4"), audio_path=str(tmp_path / "voice.wav"),
        model="kling-lipsync", duration_seconds=4.2, output_path=str(output_path),
    )
    job = provider.submit(request)

    called_url = mock_requests.post.call_args.args[0]
    assert called_url == "https://queue.fal.run/fal-ai/kling-video/lipsync/audio-to-video"
    sent_input = mock_requests.post.call_args.kwargs["json"]
    assert sent_input == {
        "video_url": "https://cdn.fal.run/video.mp4", "audio_url": "https://cdn.fal.run/voice.wav",
    }
    assert provider.poll(job) == JobStatus.COMPLETED
    result = provider.get_result(job)
    assert output_path.read_bytes() == b"SYNCED-MP4"
    assert result.duration_seconds == 4.2  # carried from the request, not probed from the file


def test_catalog_lists_the_lipsync_model():
    catalog = FalProviderCatalog()
    model_names = {m.model for m in catalog.models(Capability.LIPSYNC)}
    assert "kling-lipsync" in model_names
