from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

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

# Models whose endpoint accepts a separate end_image_url, i.e. true
# first-frame-to-last-frame (dual-keyframe) generation — verified against
# h3-max's real OpenAPI schema: "end_image_url" is a distinct optional
# field alongside "image_url", described as "the image to use as the last
# frame, for first-to-last keyframe generation." hailuo-2.3/veo-3/etc.
# have no such field; end_reference_path is silently ignored for them, so
# a caller can always pass one without needing to know per-model support.
MODELS_WITH_END_IMAGE_URL = {"h3-max"}

# Verified against fal's real OpenAPI schemas — see the design spec's
# provider capability table. veo-3 has both resolution (720p/1080p) and
# aspect_ratio (auto/16:9/9:16) fields; h3-max has only resolution
# (480P/768P), its aspect ratio "follows image_url" per its own docs;
# hailuo has neither field at all.
_VEO3_RESOLUTION_TIERS = {720: "720p", 1080: "1080p"}
_VEO3_ASPECT_RATIOS = {"16:9", "9:16"}
_H3_MAX_RESOLUTION_TIERS = {480: "480P", 768: "768P"}

# Models whose output aspect ratio is controlled entirely by the input
# reference image, not a request field — the target aspect ratio can only
# reach the provider by resizing the reference image itself before
# upload. veo-3 is excluded: its own aspect_ratio field already covers it.
MODELS_REQUIRING_RESIZED_REFERENCE = {"h3-max", "hailuo-2.3", "hailuo-2.3-fast"}


def _nearest_aspect_ratio_enum(width: int, height: int, allowed: set[str]) -> str:
    """Reduce width:height to a ratio and pick the closest allowed enum
    value by numeric ratio distance — same snap-to-nearest idea
    _snap_duration already uses for clip length."""
    target_ratio = width / height

    def _ratio_value(enum: str) -> float:
        w_str, h_str = enum.split(":")
        return int(w_str) / int(h_str)

    return min(allowed, key=lambda enum: abs(_ratio_value(enum) - target_ratio))


def _video_format_fields(model: str, width: int, height: int) -> dict:
    """Native resolution/aspect_ratio fields for a model's request body,
    derived from the canonical target. Empty dict for models with no such
    fields at all (the hailuo family), which rely entirely on
    _resize_reference_for_target instead."""
    if model == "veo-3":
        tier = min(_VEO3_RESOLUTION_TIERS, key=lambda t: abs(t - height))
        return {
            "resolution": _VEO3_RESOLUTION_TIERS[tier],
            "aspect_ratio": _nearest_aspect_ratio_enum(width, height, _VEO3_ASPECT_RATIOS),
        }
    if model == "h3-max":
        tier = min(_H3_MAX_RESOLUTION_TIERS, key=lambda t: abs(t - height))
        return {"resolution": _H3_MAX_RESOLUTION_TIERS[tier]}
    return {}


def _resize_reference_for_target(image_path: Path, width: int, height: int) -> Path:
    """Resize/pad image_path to exactly width x height via the same
    scale+pad technique render.py uses for clip normalization, writing a
    throwaway temp file. For h3-max/hailuo, whose output aspect ratio
    follows the reference image, this is how the target aspect ratio
    actually reaches the provider — the exact pixel size is incidental
    (the simplest way to produce a well-formed image carrying the right
    ratio), not a promise the provider is expected to reproduce. See the
    design spec's Section 3 note."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not installed or not on PATH")
    tmp_dir = Path(tempfile.mkdtemp(prefix="ai-film-refsize-"))
    output_path = tmp_dir / f"resized_{image_path.name}"
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(image_path),
            "-vf", (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
            ),
            str(output_path),
        ],
        check=True, capture_output=True,
    )
    return output_path

# Some models (observed on a hailuo-2.3 base video, S01_SH03) burn a
# subtitle-style on-screen caption of the spoken line into the frame, with
# timing that doesn't reliably match the actual voice track once combined
# via lipsync. No model documents a dedicated "captions off" field, so this
# is suppressed via prompt text — the one lever every text-to-video model
# accepts. Models with a documented negative_prompt field also get it set
# there for a stronger signal.
_CAPTION_SUPPRESSION_CLAUSE = (
    " No on-screen text, no captions, no subtitles, no burned-in text overlays."
)
MODELS_WITH_NEGATIVE_PROMPT = {"veo-3"}


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
        prompt = request.prompt
        if request.suppress_captions:
            prompt += _CAPTION_SUPPRESSION_CLAUSE
        input_data = {"prompt": prompt}
        if request.suppress_captions and request.model in MODELS_WITH_NEGATIVE_PROMPT:
            input_data["negative_prompt"] = "on-screen text, captions, subtitles"
        if request.model not in _NO_DURATION_FIELD_MODELS:
            input_data["duration"] = _duration_field(request.model, request.duration_seconds)
        if request.model == "h3-max":
            input_data["prompt_expansion_mode"] = "balanced"
        if request.model in MODELS_WITH_AUTO_AUDIO:
            input_data["generate_audio"] = False
        has_target = bool(request.target_width and request.target_height)
        if has_target:
            input_data.update(
                _video_format_fields(request.model, request.target_width, request.target_height)
            )
        if request.reference_paths:
            app_id = MODEL_TO_IMAGE_TO_VIDEO_APP_ID.get(
                request.model, MODEL_TO_APP_ID[request.model]
            )
            reference_path = request.reference_paths[0]
            if has_target and request.model in MODELS_REQUIRING_RESIZED_REFERENCE:
                reference_path = str(
                    _resize_reference_for_target(
                        Path(reference_path), request.target_width, request.target_height
                    )
                )
            input_data["image_url"] = client.upload_file(reference_path)
            if request.end_reference_path and request.model in MODELS_WITH_END_IMAGE_URL:
                input_data["end_image_url"] = client.upload_file(request.end_reference_path)
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
        if request.model in _NO_DURATION_FIELD_MODELS:
            # No duration was ever sent for this model — the requested value
            # was never honored (or rejected) either way, so it's the only
            # information available; the model's actual fixed clip length is
            # undocumented.
            duration_seconds = request.duration_seconds
        else:
            # request.duration_seconds is what was ASKED for, before
            # per-model snapping (e.g. h3-max's 5-15s floor/ceiling). What
            # actually got sent — and, per each model's own docs, honored —
            # is _snap_duration's result. Recording the pre-snap value here
            # was a real, verified bug: for a shot whose voice measured
            # under a model's minimum (h3-max's floor is 5s), the recorded
            # artifact duration silently didn't match the actual video file
            # (confirmed via ffprobe against a real generation: request said
            # 2.0s, the real output was 5.18s) — undermining the exact
            # video/voice duration-matching this file's _duration_field
            # exists to support.
            duration_seconds = _snap_duration(request.model, request.duration_seconds)
        return VideoGenerationResult(
            artifact_path=request.output_path,
            size_bytes=size_bytes,
            duration_seconds=duration_seconds,
        )
