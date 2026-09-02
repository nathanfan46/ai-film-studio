from __future__ import annotations

from pathlib import Path

from ai_film.models import (
    Capability, GenerationJob, ImageEditRequest, ImageGenerationRequest,
    ImageGenerationResult, JobStatus,
)
from ai_film.providers.fal import client

MODEL_TO_APP_ID = {
    "nano-banana": "fal-ai/nano-banana-2",
    "nano-banana-pro": "fal-ai/nano-banana-pro",
}

# The base app ids above are text-to-image only — verified against fal.ai's
# own OpenAPI schema for both, neither declares an image_urls field at all.
# An image_urls key sent to them isn't rejected, it's silently dropped, so
# every "reference-conditioned" generation submitted here was actually
# unconditioned text-to-image the whole time: real character-appearance
# drift across shots (same locked reference.png every time) traced back to
# this. Reference-conditioned generation requires the dedicated /edit
# endpoint instead, confirmed to declare image_urls (array of strings) for
# both models.
MODEL_TO_EDIT_APP_ID = {
    "nano-banana": "fal-ai/nano-banana-2/edit",
    "nano-banana-pro": "fal-ai/nano-banana-pro/edit",
}

EDIT_APP_ID = "fal-ai/nano-banana-2/edit"

# Verified against fal's real OpenAPI schemas for fal-ai/nano-banana-2 and
# fal-ai/nano-banana-pro (and their /edit variants, which share the same
# aspect_ratio/resolution fields): "resolution" is a coarse quality tier
# (long-side pixel count), never an exact width/height. nano-banana-pro's
# schema has no "0.5K" option — its own smallest real tier is "1K".
_IMAGE_QUALITY_TIERS = {
    "nano-banana": {512: "0.5K", 1024: "1K", 2048: "2K", 4096: "4K"},
    "nano-banana-pro": {1024: "1K", 2048: "2K", 4096: "4K"},
}
_IMAGE_ASPECT_RATIOS = {"21:9", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4", "2:3", "9:16"}


def _nearest_aspect_ratio_enum(width: int, height: int, allowed: set[str]) -> str:
    """Duplicated from providers/fal/video.py's helper of the same name —
    this codebase duplicates small helpers per module rather than sharing
    a utils file (see _probe_duration/_has_audio_stream elsewhere)."""
    target_ratio = width / height

    def _ratio_value(enum: str) -> float:
        w_str, h_str = enum.split(":")
        return int(w_str) / int(h_str)

    return min(allowed, key=lambda enum: abs(_ratio_value(enum) - target_ratio))


def _image_format_fields(model: str, width: int, height: int) -> dict:
    """Native aspect_ratio + resolution (quality-tier) fields for a
    model's request body, derived from the canonical target. Empty dict
    for a model with no known quality-tier mapping."""
    tiers = _IMAGE_QUALITY_TIERS.get(model)
    if tiers is None:
        return {}
    long_side = max(width, height)
    tier = min(tiers, key=lambda t: abs(t - long_side))
    return {
        "aspect_ratio": _nearest_aspect_ratio_enum(width, height, _IMAGE_ASPECT_RATIOS),
        "resolution": tiers[tier],
    }


class FalImageProvider:
    def __init__(self):
        self._jobs: dict[str, tuple[str, str, ImageGenerationRequest]] = {}
        self._edits: dict[str, tuple[str, str, ImageEditRequest]] = {}

    def submit(self, request: ImageGenerationRequest) -> GenerationJob:
        input_data = {"prompt": request.prompt, "num_images": request.num_candidates}
        if request.target_width and request.target_height:
            input_data.update(
                _image_format_fields(request.model, request.target_width, request.target_height)
            )
        if request.reference_paths:
            input_data["image_urls"] = [client.upload_file(p) for p in request.reference_paths]
            app_id = MODEL_TO_EDIT_APP_ID.get(request.model, MODEL_TO_APP_ID[request.model])
        else:
            app_id = MODEL_TO_APP_ID[request.model]
        job, status_url, response_url = client.submit(app_id, input_data, Capability.IMAGE)
        self._jobs[job.id] = (status_url, response_url, request)
        return job

    def poll(self, job: GenerationJob) -> JobStatus:
        status_url = self._status_url(job)
        return client.poll(status_url)

    def get_result(self, job: GenerationJob) -> ImageGenerationResult:
        """Fetch the single artifact for a job submitted via `submit()` with
        `num_candidates=1`, or for an edit job submitted via `submit_edit()`.
        Do not call on a job also fetched with `get_results`.
        """
        if job.id in self._edits:
            _, response_url, request = self._edits[job.id]
            body = client.result(response_url)
            image_url = body["images"][0]["url"]
            output_path = str(Path(request.base_image_path).with_name(
                Path(request.base_image_path).stem + "_edited.png"
            ))
            size_bytes = client.download(image_url, output_path)
            return ImageGenerationResult(artifact_path=output_path, size_bytes=size_bytes)
        _, response_url, request = self._jobs[job.id]
        body = client.result(response_url)
        image_url = body["images"][0]["url"]
        size_bytes = client.download(image_url, request.output_path)
        return ImageGenerationResult(artifact_path=request.output_path, size_bytes=size_bytes)

    def get_results(self, job: GenerationJob, output_dir: str) -> list[ImageGenerationResult]:
        """Fetch all artifacts for a multi-candidate job submitted via `submit()`
        with `num_candidates > 1`. Do not call on a job also fetched with `get_result`.
        """
        _, response_url, _ = self._jobs[job.id]
        body = client.result(response_url)
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for i, image in enumerate(body["images"], start=1):
            path = out_dir / f"result_{i}.png"
            size_bytes = client.download(image["url"], str(path))
            results.append(ImageGenerationResult(artifact_path=str(path), size_bytes=size_bytes))
        return results

    def supports_edit(self) -> bool:
        return True

    def submit_edit(self, request: ImageEditRequest) -> GenerationJob:
        input_data = {
            "prompt": request.instruction,
            "image_urls": [client.upload_file(request.base_image_path)],
        }
        job, status_url, response_url = client.submit(EDIT_APP_ID, input_data, Capability.IMAGE)
        self._edits[job.id] = (status_url, response_url, request)
        return job

    def _status_url(self, job: GenerationJob) -> str:
        if job.id in self._edits:
            return self._edits[job.id][0]
        return self._jobs[job.id][0]
