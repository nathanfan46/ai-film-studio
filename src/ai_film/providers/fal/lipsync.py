from __future__ import annotations

from ai_film.models import (
    Capability, GenerationJob, JobStatus, LipsyncGenerationRequest, VideoGenerationResult,
)
from ai_film.providers.fal import client

MODEL_TO_APP_ID = {"kling-lipsync": "fal-ai/kling-video/lipsync/audio-to-video"}


class FalLipsyncProvider:
    def __init__(self):
        self._jobs: dict[str, tuple[str, str, LipsyncGenerationRequest]] = {}

    def submit(self, request: LipsyncGenerationRequest) -> GenerationJob:
        app_id = MODEL_TO_APP_ID[request.model]
        input_data = {
            "video_url": client.upload_file(request.video_path),
            "audio_url": client.upload_file(request.audio_path),
        }
        job, status_url, response_url = client.submit(app_id, input_data, Capability.LIPSYNC)
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
            artifact_path=request.output_path, size_bytes=size_bytes,
            duration_seconds=request.duration_seconds,
        )
