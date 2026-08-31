from __future__ import annotations

from ai_film.models import (
    Capability, GenerationJob, JobStatus, VideoGenerationRequest, VideoGenerationResult,
)
from ai_film.providers.fal import client

MODEL_TO_APP_ID = {
    "veo-3": "fal-ai/veo3",
    "seedance-1-0-pro": "fal-ai/seedance-1-0-pro",
    "kling-v3-pro": "fal-ai/kling-video/v3/pro",
}

# Image-conditioned generation requires a distinct endpoint per model — the
# plain text-to-video app_id above does not accept (or reliably honor) an
# image_url input. Only used when the request actually carries a reference
# image; models with no known image-to-video endpoint fall back to the
# text-to-video app_id, which will drop the image_url as before.
MODEL_TO_IMAGE_TO_VIDEO_APP_ID = {
    "veo-3": "fal-ai/veo3/image-to-video",
}

# Models whose endpoints only accept a fixed set of clip durations. Requested
# durations are snapped to the nearest allowed value (ties favor the shorter
# clip) rather than sent through raw and rejected by the provider.
MODEL_ALLOWED_DURATIONS = {
    "veo-3": (4, 6, 8),
}

# Models whose endpoint can autonomously invent audio/dialogue (defaults to
# on) unless explicitly disabled. This project's dialogue is authored per
# shot and voiced separately via the voice-generation stage — the video
# model must never invent its own spoken lines, so audio generation is
# turned off at the source for these models.
MODELS_WITH_AUTO_AUDIO = {"veo-3"}


def _snap_duration(model: str, duration_seconds: float) -> int:
    allowed = MODEL_ALLOWED_DURATIONS.get(model)
    if not allowed:
        return int(duration_seconds)
    return min(allowed, key=lambda v: (abs(v - duration_seconds), v))


class FalVideoProvider:
    def __init__(self):
        self._jobs: dict[str, tuple[str, str, VideoGenerationRequest]] = {}

    def submit(self, request: VideoGenerationRequest) -> GenerationJob:
        duration = _snap_duration(request.model, request.duration_seconds)
        input_data = {
            "prompt": request.prompt,
            "duration": f"{duration}s",
        }
        if request.model in MODELS_WITH_AUTO_AUDIO:
            input_data["generate_audio"] = False
        if request.reference_paths:
            app_id = MODEL_TO_IMAGE_TO_VIDEO_APP_ID.get(
                request.model, MODEL_TO_APP_ID[request.model]
            )
            input_data["image_url"] = client.upload_file(request.reference_paths[0])
        else:
            app_id = MODEL_TO_APP_ID[request.model]
        job, status_url, response_url = client.submit(app_id, input_data, Capability.VIDEO)
        self._jobs[job.id] = (status_url, response_url, request)
        return job

    def poll(self, job: GenerationJob) -> JobStatus:
        status_url, _, _ = self._jobs[job.id]
        return client.poll(status_url)

    def get_result(self, job: GenerationJob) -> VideoGenerationResult:
        _, response_url, request = self._jobs[job.id]
        body = client.result(response_url)
        video_url = body["video"]["url"]
        size_bytes = client.download(video_url, request.output_path)
        return VideoGenerationResult(
            artifact_path=request.output_path,
            size_bytes=size_bytes,
            duration_seconds=request.duration_seconds,
        )
