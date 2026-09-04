from ai_film.models import ImageEditRequest, ImageGenerationRequest, VideoGenerationRequest


def test_image_generation_request_defaults_num_candidates_to_one():
    request = ImageGenerationRequest(prompt="a girl", model="nano-banana")
    assert request.num_candidates == 1


def test_image_generation_request_accepts_explicit_num_candidates():
    request = ImageGenerationRequest(prompt="a girl", model="nano-banana", num_candidates=4)
    assert request.num_candidates == 4


def test_image_edit_request_defaults():
    request = ImageEditRequest(base_image_path="candidates/001.png", instruction="warmer lighting")
    assert request.mask_path is None
    assert request.reference_paths == []


def test_image_edit_request_is_immutable_reference_paths_per_instance():
    a = ImageEditRequest(base_image_path="x.png", instruction="a")
    b = ImageEditRequest(base_image_path="y.png", instruction="b")
    a.reference_paths.append("z.png")
    assert b.reference_paths == []


def test_video_generation_request_target_format_fields_default_to_zero():
    request = VideoGenerationRequest(prompt="x", model="veo-3")
    assert request.target_width == 0
    assert request.target_height == 0
    assert request.target_fps == 0


def test_image_generation_request_target_format_fields_default_to_zero():
    request = ImageGenerationRequest(prompt="x", model="nano-banana")
    assert request.target_width == 0
    assert request.target_height == 0


def test_motion_transfer_request_defaults():
    from ai_film.models import MotionTransferRequest

    request = MotionTransferRequest(
        image_path="ref.png", driving_video_path="dance.mp4", model="kling-motion-control",
    )
    assert request.character_orientation == "video"
    assert request.prompt == ""
    assert request.output_path == ""
    assert request.target_width == 0
    assert request.target_height == 0
    assert request.target_fps == 0
    # keep_original_sound is deliberately NOT a field on this dataclass —
    # it's hardcoded at the provider layer (Task 3), never a request param.
    assert not hasattr(request, "keep_original_sound")
