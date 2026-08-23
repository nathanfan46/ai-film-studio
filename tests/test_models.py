from ai_film.models import ImageEditRequest, ImageGenerationRequest


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
