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

EDIT_APP_ID = "fal-ai/nano-banana-2"


class FalImageProvider:
    def __init__(self):
        self._jobs: dict[str, tuple[str, str, ImageGenerationRequest]] = {}
        self._edits: dict[str, tuple[str, str, ImageEditRequest]] = {}

    def submit(self, request: ImageGenerationRequest) -> GenerationJob:
        app_id = MODEL_TO_APP_ID[request.model]
        input_data = {"prompt": request.prompt, "num_images": request.num_candidates}
        if request.reference_paths:
            input_data["image_urls"] = [client.upload_file(p) for p in request.reference_paths]
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
