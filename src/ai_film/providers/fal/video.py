from __future__ import annotations

from ai_film.models import (
    Capability, GenerationJob, JobStatus, VideoGenerationRequest, VideoGenerationResult,
)
from ai_film.providers.fal import client

MODEL_TO_APP_ID = {
    "veo-3": "fal-ai/veo3",
    "seedance-1-0-pro": "fal-ai/seedance-1-0-pro",
    "kling-v3-pro": "fal-ai/kling-video/v3/pro",
    # These three are already image-to-video-native endpoints (there is no
    # separate text-to-video app_id to fall back to for them the way veo-3
    # has) — verified directly against fal.ai's OpenAPI schema for each.
    "hailuo-2.3": "fal-ai/minimax/hailuo-2.3/standard/image-to-video",
    "hailuo-2.3-fast": "fal-ai/minimax/hailuo-2.3-fast/pro/image-to-video",
    "h3-max": "minimax/h3-max/image-to-video",  # note: no "fal-ai/" prefix
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
    "hailuo-2.3": (6, 10),
    "h3-max": tuple(range(5, 16)),  # 5-15s inclusive, any integer
}

# hailuo-2.3's "duration" field is a bare numeric string ("6"/"10"), not
# suffixed with "s" like veo-3/seedance/kling. Verified against fal.ai's
# OpenAPI schema for fal-ai/minimax/hailuo-2.3/standard/image-to-video.
_BARE_STRING_DURATION_MODELS = {"hailuo-2.3"}

# h3-max's "duration" field is a plain integer (seconds), not a string at
# all. Verified against fal.ai's OpenAPI schema for
# minimax/h3-max/image-to-video.
_INTEGER_DURATION_MODELS = {"h3-max"}

# hailuo-2.3-fast has no "duration" field in its schema whatsoever (a fixed,
# undocumented clip length) — sending one is not just ignored, it's not a
# declared field at all, so it's omitted rather than guessed at. Verified
# against fal.ai's OpenAPI schema for
# fal-ai/minimax/hailuo-2.3-fast/pro/image-to-video.
_NO_DURATION_FIELD_MODELS = {"hailuo-2.3-fast"}

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


def _duration_field(model: str, duration_seconds: float) -> str | int:
    duration = _snap_duration(model, duration_seconds)
    if model in _INTEGER_DURATION_MODELS:
        return duration
    if model in _BARE_STRING_DURATION_MODELS:
        return str(duration)
    return f"{duration}s"


class FalVideoProvider:
    def __init__(self):
        self._jobs: dict[str, tuple[str, str, VideoGenerationRequest]] = {}

    def submit(self, request: VideoGenerationRequest) -> GenerationJob:
        input_data = {"prompt": request.prompt}
        if request.model not in _NO_DURATION_FIELD_MODELS:
            input_data["duration"] = _duration_field(request.model, request.duration_seconds)
        if request.model == "h3-max":
            input_data["prompt_expansion_mode"] = "balanced"
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
