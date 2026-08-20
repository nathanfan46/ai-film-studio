from __future__ import annotations

from ai_film.models import (
    Capability, GenerationJob, ImageGenerationRequest, ImageGenerationResult, JobStatus,
)
from ai_film.providers.fal import client

MODEL_TO_APP_ID = {
    "nano-banana": "fal-ai/nano-banana-2",
    "nano-banana-pro": "fal-ai/nano-banana-pro",
}


class FalImageProvider:
    def __init__(self):
        self._jobs: dict[str, tuple[str, str, ImageGenerationRequest]] = {}

    def submit(self, request: ImageGenerationRequest) -> GenerationJob:
        app_id = MODEL_TO_APP_ID[request.model]
        input_data = {"prompt": request.prompt, "num_images": 1}
        if request.reference_paths:
            input_data["image_urls"] = request.reference_paths
        job, status_url, response_url = client.submit(app_id, input_data, Capability.IMAGE)
        self._jobs[job.id] = (status_url, response_url, request)
        return job

    def poll(self, job: GenerationJob) -> JobStatus:
        status_url, _, _ = self._jobs[job.id]
        return client.poll(status_url)

    def get_result(self, job: GenerationJob) -> ImageGenerationResult:
        _, response_url, request = self._jobs[job.id]
        body = client.result(response_url)
        image_url = body["images"][0]["url"]
        size_bytes = client.download(image_url, request.output_path)
        return ImageGenerationResult(artifact_path=request.output_path, size_bytes=size_bytes)
