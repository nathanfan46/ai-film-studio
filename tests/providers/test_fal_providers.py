import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_film.models import (
    Capability,
    ImageEditRequest,
    ImageGenerationRequest,
    JobStatus,
    LipsyncGenerationRequest,
    MotionTransferRequest,
    MusicGenerationRequest,
    SfxGenerationRequest,
    VideoGenerationRequest,
    VoiceGenerationRequest,
)
from ai_film.providers.fal.audio import FalAudioProvider, _speaker_id
from ai_film.providers.fal.catalog import FalProviderCatalog
from ai_film.providers.fal.image import FalImageProvider, _image_format_fields
from ai_film.providers.fal.lipsync import FalLipsyncProvider
from ai_film.providers.fal.motion_transfer import FalMotionTransferProvider, MODEL_TO_APP_ID
from ai_film.providers.fal.video import (
    MODELS_REQUIRING_RESIZED_REFERENCE, _nearest_aspect_ratio_enum, _resize_reference_for_target,
    _video_format_fields, FalVideoProvider,
)


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
def test_fal_image_provider_uses_base_endpoint_without_references(
    mock_requests, tmp_path: Path, monkeypatch
):
    """The base nano-banana-2 app id is text-to-image only — verified
    against fal.ai's own OpenAPI schema, it has no image_urls field at all
    and silently drops one if sent. Regression guard for a real bug: every
    reference-conditioned generation was actually being submitted to this
    endpoint, so the reference images were never used."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    submit_response = MagicMock(status_code=200)
    submit_response.json.return_value = {
        "request_id": "req-1", "status_url": "https://queue.fal.run/status/req-1",
        "response_url": "https://queue.fal.run/result/req-1",
    }
    mock_requests.post.return_value = submit_response

    provider = FalImageProvider()
    provider.submit(
        ImageGenerationRequest(
            prompt="a girl in a corridor", model="nano-banana",
            reference_paths=[], output_path=str(tmp_path / "out.png"),
        )
    )

    called_url = mock_requests.post.call_args.args[0]
    assert called_url == "https://queue.fal.run/fal-ai/nano-banana-2"
    sent_input = mock_requests.post.call_args.kwargs["json"]
    assert "image_urls" not in sent_input


@patch("ai_film.providers.fal.client.requests")
def test_fal_image_provider_uses_edit_endpoint_with_references(
    mock_requests, tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("FAL_KEY", "test-key")
    submit_response = MagicMock(status_code=200)
    submit_response.json.return_value = {
        "request_id": "req-1", "status_url": "https://queue.fal.run/status/req-1",
        "response_url": "https://queue.fal.run/result/req-1",
    }
    mock_requests.post.return_value = submit_response
    reference = tmp_path / "ref.png"
    reference.write_bytes(b"REF-PNG")
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file", lambda path: "https://cdn.fal.run/ref.png"
    )

    provider = FalImageProvider()
    provider.submit(
        ImageGenerationRequest(
            prompt="a girl in a corridor", model="nano-banana",
            reference_paths=[str(reference)], output_path=str(tmp_path / "out.png"),
        )
    )

    called_url = mock_requests.post.call_args.args[0]
    assert called_url == "https://queue.fal.run/fal-ai/nano-banana-2/edit"
    sent_input = mock_requests.post.call_args.kwargs["json"]
    assert sent_input["image_urls"] == ["https://cdn.fal.run/ref.png"]


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
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file", lambda path: "https://cdn.fal.run/video.mp4"
    )

    provider = FalAudioProvider()

    voice_job = provider.submit_voice(
        VoiceGenerationRequest(
            text="hello", model="csm-1b", output_path=str(tmp_path / "voice.wav")
        )
    )
    sfx_job = provider.submit_sfx(
        SfxGenerationRequest(
            prompt="door creak", model="thinksound",
            video_path=str(tmp_path / "shot.mp4"), output_path=str(tmp_path / "sfx.wav"),
        )
    )
    music_job = provider.submit_music(
        MusicGenerationRequest(
            prompt="tense strings", model="cassetteai-music", output_path=str(tmp_path / "music.wav")
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
    called_url = mock_requests.post.call_args_list[-1].args[0]
    assert called_url == "https://queue.fal.run/fal-ai/nano-banana-2/edit"


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
def test_h3_max_sends_end_image_url_for_dual_keyframe_continuity(
    mock_requests, tmp_path: Path, monkeypatch
):
    """Verified against fal.ai's OpenAPI schema for
    minimax/h3-max/image-to-video: end_image_url is a distinct optional
    field, "the image to use as the last frame, for first-to-last keyframe
    generation" — h3-max is the only model in MODELS_WITH_END_IMAGE_URL."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    _mock_submit_response(mock_requests)
    reference = tmp_path / "start.png"
    reference.write_bytes(b"START-PNG")
    end_reference = tmp_path / "end.png"
    end_reference.write_bytes(b"END-PNG")
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file",
        lambda path: f"https://cdn.fal.run/{Path(path).name}",
    )

    provider = FalVideoProvider()
    provider.submit(
        VideoGenerationRequest(
            prompt="a girl walks", model="h3-max", reference_paths=[str(reference)],
            end_reference_path=str(end_reference),
            duration_seconds=5, output_path=str(tmp_path / "out.mp4"),
        )
    )

    sent_input = mock_requests.post.call_args.kwargs["json"]
    assert sent_input["image_url"] == "https://cdn.fal.run/start.png"
    assert sent_input["end_image_url"] == "https://cdn.fal.run/end.png"


@patch("ai_film.providers.fal.client.requests")
def test_hailuo_ignores_end_reference_path_since_its_schema_has_no_such_field(
    mock_requests, tmp_path: Path, monkeypatch
):
    """Verified against fal.ai's OpenAPI schema for
    fal-ai/minimax/hailuo-2.3/standard/image-to-video: no end_image_url-
    equivalent field exists — a caller can pass end_reference_path without
    needing to know per-model support; it's silently dropped here."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    _mock_submit_response(mock_requests)
    reference = tmp_path / "start.png"
    reference.write_bytes(b"START-PNG")
    end_reference = tmp_path / "end.png"
    end_reference.write_bytes(b"END-PNG")
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file",
        lambda path: f"https://cdn.fal.run/{Path(path).name}",
    )

    provider = FalVideoProvider()
    provider.submit(
        VideoGenerationRequest(
            prompt="a girl walks", model="hailuo-2.3", reference_paths=[str(reference)],
            end_reference_path=str(end_reference),
            duration_seconds=6, output_path=str(tmp_path / "out.mp4"),
        )
    )

    sent_input = mock_requests.post.call_args.kwargs["json"]
    assert sent_input["image_url"] == "https://cdn.fal.run/start.png"
    assert "end_image_url" not in sent_input


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


def test_catalog_lists_kling_motion_control_for_motion_transfer():
    from ai_film.models import Capability
    from ai_film.providers.fal.catalog import FalProviderCatalog

    models = FalProviderCatalog().models(Capability.MOTION_TRANSFER)
    assert [m.model for m in models] == ["kling-motion-control"]
    assert models[0].provider == "fal"


@patch("ai_film.providers.fal.client.requests")
def test_video_result_records_the_snapped_duration_not_the_raw_request(
    mock_requests, tmp_path: Path, monkeypatch
):
    """Regression test: get_result() previously recorded
    request.duration_seconds verbatim — the value ASKED for, before
    per-model snapping — rather than what was actually sent (and, per each
    model's docs, honored). Confirmed against a real generation: a 2.0s
    request to h3-max (whose floor is 5s) produced a real 5.18s video file
    (checked with ffprobe), but the artifact recorded duration_seconds:
    2.0 — silently wrong, and undermining the whole point of syncing video
    length to voice length in the first place."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    reference = tmp_path / "ref.png"
    reference.write_bytes(b"REF-PNG")
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file", lambda path: "https://cdn.fal.run/ref.png"
    )
    submit_response = MagicMock(status_code=200)
    submit_response.json.return_value = {
        "request_id": "req-1", "status_url": "https://queue.fal.run/status/req-1",
        "response_url": "https://queue.fal.run/result/req-1",
    }
    status_response = MagicMock(status_code=200)
    status_response.json.return_value = {"status": "COMPLETED"}
    result_response = MagicMock(status_code=200)
    result_response.json.return_value = {"video": {"url": "https://cdn.fal.run/out.mp4"}}
    download_response = MagicMock(status_code=200, content=b"MP4-BYTES")
    mock_requests.post.return_value = submit_response
    mock_requests.get.side_effect = [status_response, result_response, download_response]

    provider = FalVideoProvider()
    job = provider.submit(
        VideoGenerationRequest(
            prompt="a woman listens", model="h3-max", reference_paths=[str(reference)],
            duration_seconds=2.0, output_path=str(tmp_path / "out.mp4"),
        )
    )
    assert provider.poll(job) == JobStatus.COMPLETED
    result = provider.get_result(job)

    assert result.duration_seconds == 5  # h3-max's floor — what was actually sent and honored
    assert result.duration_seconds != 2.0  # not the raw, pre-snap request value


@patch("ai_film.providers.fal.client.requests")
def test_fal_sfx_provider_sends_video_url_and_prompt(mock_requests, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    submit_response = MagicMock(status_code=200)
    submit_response.json.return_value = {
        "request_id": "req-sfx", "status_url": "https://queue.fal.run/status/req-sfx",
        "response_url": "https://queue.fal.run/result/req-sfx",
    }
    mock_requests.post.return_value = submit_response
    video_path = tmp_path / "shot.mp4"
    video_path.write_bytes(b"VIDEO-BYTES")
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file", lambda path: "https://cdn.fal.run/shot.mp4"
    )

    provider = FalAudioProvider()
    provider.submit_sfx(
        SfxGenerationRequest(
            prompt="phone rings", model="thinksound",
            video_path=str(video_path), output_path=str(tmp_path / "sfx.wav"),
        )
    )

    sent_input = mock_requests.post.call_args.kwargs["json"]
    assert sent_input == {"video_url": "https://cdn.fal.run/shot.mp4", "prompt": "phone rings"}


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_fal_sfx_provider_extracts_audio_and_discards_video(tmp_path: Path, monkeypatch):
    """The core of the SFX redesign: ThinkSound returns a full video with
    generated audio baked in, not a standalone audio file — this must
    extract only the audio track and never let the returned video become
    or touch this shot's own video artifact."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    fake_returned_video = tmp_path / "thinksound_returned.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=64x64:rate=10",
            "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "2", "-shortest",
            str(fake_returned_video),
        ],
        check=True, capture_output=True,
    )

    provider = FalAudioProvider()
    request = SfxGenerationRequest(
        prompt="phone rings", model="thinksound",
        video_path=str(tmp_path / "shot.mp4"), output_path=str(tmp_path / "sfx.wav"),
    )
    job_id = "job-sfx-1"
    provider._jobs[job_id] = ("status-url", "response-url", request, 2.0)
    monkeypatch.setattr(
        "ai_film.providers.fal.client.result",
        lambda response_url: {"video": {"url": "https://cdn.fal.run/returned.mp4"}},
    )
    monkeypatch.setattr(
        "ai_film.providers.fal.client.download",
        lambda url, output_path: shutil.copy(fake_returned_video, output_path),
    )

    from ai_film.models import GenerationJob
    result = provider.get_result(GenerationJob(provider="fal", id=job_id, capability=Capability.SFX))

    output_path = Path(request.output_path)
    assert output_path.exists()
    assert result.artifact_path == str(output_path)
    assert result.duration_seconds > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "default=noprint_wrappers=1", str(output_path)],
        capture_output=True, text=True,
    )
    assert "codec_type=audio" in probe.stdout
    assert "codec_type=video" not in probe.stdout


@patch("ai_film.providers.fal.client.requests")
def test_fal_music_provider_sends_prompt_and_integer_duration(mock_requests, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    submit_response = MagicMock(status_code=200)
    submit_response.json.return_value = {
        "request_id": "req-music", "status_url": "https://queue.fal.run/status/req-music",
        "response_url": "https://queue.fal.run/result/req-music",
    }
    mock_requests.post.return_value = submit_response

    provider = FalAudioProvider()
    provider.submit_music(
        MusicGenerationRequest(
            prompt="tense strings", model="cassetteai-music", duration_seconds=45.0,
            output_path=str(tmp_path / "music.wav"),
        )
    )

    sent_input = mock_requests.post.call_args.kwargs["json"]
    assert sent_input == {"prompt": "tense strings", "duration": 45}


def test_nearest_aspect_ratio_enum_picks_closest_by_ratio_distance():
    assert _nearest_aspect_ratio_enum(1280, 720, {"16:9", "9:16"}) == "16:9"
    assert _nearest_aspect_ratio_enum(720, 1280, {"16:9", "9:16"}) == "9:16"


def test_video_format_fields_for_veo3_sends_resolution_and_aspect_ratio():
    fields = _video_format_fields("veo-3", 1280, 720)
    assert fields == {"resolution": "720p", "aspect_ratio": "16:9"}


def test_video_format_fields_for_veo3_picks_1080p_tier():
    fields = _video_format_fields("veo-3", 1920, 1080)
    assert fields["resolution"] == "1080p"


def test_video_format_fields_for_h3_max_sends_resolution_only():
    fields = _video_format_fields("h3-max", 1280, 720)
    assert fields == {"resolution": "768P"}


def test_video_format_fields_for_hailuo_is_empty_no_native_fields_exist():
    assert _video_format_fields("hailuo-2.3", 1280, 720) == {}
    assert _video_format_fields("hailuo-2.3-fast", 1280, 720) == {}


def test_models_requiring_resized_reference_is_h3_max_and_both_hailuo_models():
    assert MODELS_REQUIRING_RESIZED_REFERENCE == {"h3-max", "hailuo-2.3", "hailuo-2.3-fast"}


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_resize_reference_for_target_produces_exact_target_dimensions(tmp_path: Path):
    source = tmp_path / "source.png"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=500x500:d=1", "-frames:v", "1", str(source)],
        check=True, capture_output=True,
    )

    output = _resize_reference_for_target(source, 1280, 720)

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(output),
        ],
        capture_output=True, text=True,
    )
    assert probe.stdout.strip() == "1280x720"


@patch("ai_film.providers.fal.client.requests")
def test_hailuo_uploads_resized_reference_when_target_is_set(mock_requests, tmp_path: Path, monkeypatch):
    """hailuo has no aspect_ratio/resolution field at all (verified against
    its OpenAPI schema) — the resized reference image is its only lever,
    so submit() must upload the RESIZED file, not the raw one, whenever a
    target was resolved."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    _mock_submit_response(mock_requests)
    reference = tmp_path / "ref.png"
    reference.write_bytes(b"REF-PNG")
    uploaded_paths = []
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file",
        lambda path: uploaded_paths.append(path) or "https://cdn.fal.run/uploaded.png",
    )
    monkeypatch.setattr(
        "ai_film.providers.fal.video._resize_reference_for_target",
        lambda image_path, width, height: Path(tmp_path / "resized.png"),
    )

    provider = FalVideoProvider()
    provider.submit(
        VideoGenerationRequest(
            prompt="a girl walks", model="hailuo-2.3", reference_paths=[str(reference)],
            duration_seconds=6, output_path=str(tmp_path / "out.mp4"),
            target_width=1280, target_height=720,
        )
    )

    assert uploaded_paths == [str(tmp_path / "resized.png")]


@patch("ai_film.providers.fal.client.requests")
def test_veo3_uploads_raw_reference_never_resized(mock_requests, tmp_path: Path, monkeypatch):
    """veo-3 has its own aspect_ratio field (verified against its OpenAPI
    schema) — it must never go through the reference-resize path."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    _mock_submit_response(mock_requests)
    reference = tmp_path / "ref.png"
    reference.write_bytes(b"REF-PNG")
    uploaded_paths = []
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file",
        lambda path: uploaded_paths.append(path) or "https://cdn.fal.run/uploaded.png",
    )

    provider = FalVideoProvider()
    provider.submit(
        VideoGenerationRequest(
            prompt="a girl walks", model="veo-3", reference_paths=[str(reference)],
            duration_seconds=6, output_path=str(tmp_path / "out.mp4"),
            target_width=1280, target_height=720,
        )
    )

    assert uploaded_paths == [str(reference)]
    sent_input = mock_requests.post.call_args.kwargs["json"]
    assert sent_input["resolution"] == "720p"
    assert sent_input["aspect_ratio"] == "16:9"


@patch("ai_film.providers.fal.client.requests")
def test_fal_music_provider_parses_audio_file_field(mock_requests, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    submit_response = MagicMock(status_code=200)
    submit_response.json.return_value = {
        "request_id": "req-music", "status_url": "https://queue.fal.run/status/req-music",
        "response_url": "https://queue.fal.run/result/req-music",
    }
    status_response = MagicMock(status_code=200)
    status_response.json.return_value = {"status": "COMPLETED"}
    result_response = MagicMock(status_code=200)
    result_response.json.return_value = {"audio_file": {"url": "https://cdn.fal.run/music.wav"}}
    download_response = MagicMock(status_code=200, content=b"MUSIC-BYTES")
    mock_requests.post.return_value = submit_response
    mock_requests.get.side_effect = [status_response, result_response, download_response]

    provider = FalAudioProvider()
    job = provider.submit_music(
        MusicGenerationRequest(
            prompt="tense strings", model="cassetteai-music", duration_seconds=45.0,
            output_path=str(tmp_path / "music.wav"),
        )
    )
    assert provider.poll(job) == JobStatus.COMPLETED
    result = provider.get_result(job)
    assert Path(result.artifact_path).read_bytes() == b"MUSIC-BYTES"


def test_image_format_fields_for_nano_banana_sends_aspect_ratio_and_quality_tier():
    fields = _image_format_fields("nano-banana", 1280, 720)
    assert fields == {"aspect_ratio": "16:9", "resolution": "1K"}


def test_image_format_fields_for_nano_banana_pro_has_no_half_k_tier():
    # nano-banana-pro's real schema has no "0.5K" option (verified against
    # its OpenAPI schema) — a small target must still snap to its lowest
    # real tier, "1K", not an invalid "0.5K".
    fields = _image_format_fields("nano-banana-pro", 100, 100)
    assert fields["resolution"] == "1K"


def test_image_format_fields_for_unknown_model_is_empty():
    assert _image_format_fields("some-future-model", 1280, 720) == {}


@patch("ai_film.providers.fal.client.requests")
def test_image_provider_sends_format_fields_on_edit_call(mock_requests, tmp_path: Path, monkeypatch):
    """nano-banana-2/edit's real schema also has aspect_ratio + resolution
    (verified) — the reference-conditioned /edit path must get them too,
    not just the base text-to-image path."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    _mock_submit_response(mock_requests)
    reference = tmp_path / "ref.png"
    reference.write_bytes(b"REF-PNG")
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file", lambda path: "https://cdn.fal.run/ref.png"
    )

    provider = FalImageProvider()
    provider.submit(
        ImageGenerationRequest(
            prompt="a girl", model="nano-banana", reference_paths=[str(reference)],
            output_path=str(tmp_path / "out.png"), target_width=1280, target_height=720,
        )
    )

    sent_input = mock_requests.post.call_args.kwargs["json"]
    assert sent_input["aspect_ratio"] == "16:9"
    assert sent_input["resolution"] == "1K"


def test_motion_transfer_model_to_app_id_is_kling_motion_control():
    assert MODEL_TO_APP_ID == {
        "kling-motion-control": "fal-ai/kling-video/v2.6/standard/motion-control",
    }


@patch("ai_film.providers.fal.client.requests")
def test_motion_transfer_provider_sends_expected_request_body(
    mock_requests, tmp_path: Path, monkeypatch
):
    """Verified against fal's real OpenAPI schema for
    fal-ai/kling-video/v2.6/standard/motion-control: image_url, video_url,
    character_orientation, and keep_original_sound are the fields that
    matter here. keep_original_sound must always be False — never a
    request-object value — per this project's own audio-ownership design
    (dialogue/sfx/music are attached later, deliberately, via
    generate-lipsync/mux-audio)."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    _mock_submit_response(mock_requests)
    image = tmp_path / "ref.png"
    image.write_bytes(b"REF-PNG")
    driving_video = tmp_path / "dance.mp4"
    driving_video.write_bytes(b"DRIVING-MP4")
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file",
        lambda path: f"https://cdn.fal.run/{Path(path).name}",
    )

    provider = FalMotionTransferProvider()
    provider.submit(
        MotionTransferRequest(
            image_path=str(image), driving_video_path=str(driving_video),
            model="kling-motion-control", output_path=str(tmp_path / "out.mp4"),
        )
    )

    called_url = mock_requests.post.call_args.args[0]
    assert called_url == "https://queue.fal.run/fal-ai/kling-video/v2.6/standard/motion-control"
    sent_input = mock_requests.post.call_args.kwargs["json"]
    assert sent_input["image_url"] == "https://cdn.fal.run/ref.png"
    assert sent_input["video_url"] == "https://cdn.fal.run/dance.mp4"
    assert sent_input["character_orientation"] == "video"
    assert sent_input["keep_original_sound"] is False
    assert "prompt" not in sent_input  # empty prompt is omitted, not sent as ""


@patch("ai_film.providers.fal.client.requests")
def test_motion_transfer_provider_sends_prompt_only_when_non_empty(
    mock_requests, tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("FAL_KEY", "test-key")
    _mock_submit_response(mock_requests)
    image = tmp_path / "ref.png"
    image.write_bytes(b"REF-PNG")
    driving_video = tmp_path / "dance.mp4"
    driving_video.write_bytes(b"DRIVING-MP4")
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file",
        lambda path: f"https://cdn.fal.run/{Path(path).name}",
    )

    provider = FalMotionTransferProvider()
    provider.submit(
        MotionTransferRequest(
            image_path=str(image), driving_video_path=str(driving_video),
            model="kling-motion-control", output_path=str(tmp_path / "out.mp4"),
            prompt="a woman dancing energetically",
        )
    )

    sent_input = mock_requests.post.call_args.kwargs["json"]
    assert sent_input["prompt"] == "a woman dancing energetically"


@patch("ai_film.providers.fal.client.requests")
def test_motion_transfer_provider_resizes_reference_image_when_target_set(
    mock_requests, tmp_path: Path, monkeypatch
):
    """Reuses _resize_reference_for_target from providers/fal/video.py —
    kling-motion-control has no resolution/aspect_ratio field, so the
    reference image's own aspect ratio is the only lever available (same
    situation this project already solved once for h3-max/hailuo)."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    _mock_submit_response(mock_requests)
    image = tmp_path / "ref.png"
    image.write_bytes(b"REF-PNG")
    driving_video = tmp_path / "dance.mp4"
    driving_video.write_bytes(b"DRIVING-MP4")
    uploaded_paths = []
    monkeypatch.setattr(
        "ai_film.providers.fal.client.upload_file",
        lambda path: uploaded_paths.append(path) or f"https://cdn.fal.run/{Path(path).name}",
    )
    monkeypatch.setattr(
        "ai_film.providers.fal.motion_transfer._resize_reference_for_target",
        lambda image_path, width, height: Path(tmp_path / "resized.png"),
    )

    provider = FalMotionTransferProvider()
    provider.submit(
        MotionTransferRequest(
            image_path=str(image), driving_video_path=str(driving_video),
            model="kling-motion-control", output_path=str(tmp_path / "out.mp4"),
            target_width=1280, target_height=720,
        )
    )

    assert str(tmp_path / "resized.png") in uploaded_paths
    assert str(image) not in uploaded_paths
    # the driving video is never resized — see the spec's non-goals
    assert str(driving_video) in uploaded_paths


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not installed")
def test_motion_transfer_probe_duration_returns_zero_on_malformed_output(tmp_path: Path):
    from ai_film.providers.fal.motion_transfer import _probe_duration

    garbage = tmp_path / "not-a-video.mp4"
    garbage.write_bytes(b"not a real video")
    # A local, deterministic ffprobe failure must never raise ProviderError
    # here: get_result() is called inside run_job's paid-retry loop, and
    # raising would burn up to max_attempts paid re-submissions on a
    # failure that retrying can never fix.
    assert _probe_duration(garbage) == 0.0


def test_motion_transfer_probe_duration_returns_zero_without_ffprobe(tmp_path: Path, monkeypatch):
    from ai_film.providers.fal.motion_transfer import _probe_duration

    monkeypatch.setattr(
        "ai_film.providers.fal.motion_transfer.shutil.which", lambda name: None
    )
    video_path = tmp_path / "whatever.mp4"
    assert _probe_duration(video_path) == 0.0
