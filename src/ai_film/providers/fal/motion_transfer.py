from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ai_film.errors import ProviderError
from ai_film.models import (
    Capability, GenerationJob, JobStatus, MotionTransferRequest, VideoGenerationResult,
)
from ai_film.providers.fal import client
from ai_film.providers.fal.video import _resize_reference_for_target

# Verified against fal's real OpenAPI schema for
# fal-ai/kling-video/v2.6/standard/motion-control.
MODEL_TO_APP_ID = {
    "kling-motion-control": "fal-ai/kling-video/v2.6/standard/motion-control",
}


def _probe_duration(video_path: Path) -> float:
    """Motion-transfer's endpoint has no duration request field at all —
    output length is entirely provider-determined (bounded by the driving
    video's own 3-30.05s constraint) — so the real artifact must be
    probed, there's no static value to fall back to. Hardened the same
    way render.py's/generation_service.py's own probe helpers already are
    (raise ProviderError, not a bare subprocess/parse exception, so a
    corrupt or unparseable artifact flows through run_generation_stage's
    existing restore-and-mark-failed path instead of crashing uncaught)."""
    if shutil.which("ffprobe") is None:
        raise ProviderError("ffprobe is not installed or not on PATH")
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
        ],
        capture_output=True, text=True,
    )
    raw = probe.stdout.strip()
    try:
        return float(raw)
    except ValueError as exc:
        raise ProviderError(
            f"could not parse duration for {video_path}: ffprobe returned {raw!r}"
        ) from exc


class FalMotionTransferProvider:
    def __init__(self):
        self._jobs: dict[str, tuple[str, str, MotionTransferRequest]] = {}

    def submit(self, request: MotionTransferRequest) -> GenerationJob:
        image_path = request.image_path
        if request.target_width and request.target_height:
            image_path = str(
                _resize_reference_for_target(
                    Path(image_path), request.target_width, request.target_height
                )
            )
        input_data = {
            "image_url": client.upload_file(image_path),
            "video_url": client.upload_file(request.driving_video_path),
            "character_orientation": request.character_orientation,
            # Always False — never a request-object value. This project
            # already has a dedicated, deliberate place audio gets
            # attached to a shot (generate-lipsync, mux-audio);
            # inheriting a driving clip's own soundtrack would introduce
            # a second, accidental audio source into generation.video.
            "keep_original_sound": False,
        }
        if request.prompt:
            input_data["prompt"] = request.prompt
        app_id = MODEL_TO_APP_ID[request.model]
        job, status_url, response_url = client.submit(
            app_id, input_data, Capability.MOTION_TRANSFER
        )
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
        duration_seconds = _probe_duration(Path(request.output_path))
        return VideoGenerationResult(
            artifact_path=request.output_path,
            size_bytes=size_bytes,
            duration_seconds=duration_seconds,
        )
