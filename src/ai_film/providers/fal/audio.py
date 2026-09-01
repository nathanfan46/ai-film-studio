from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from ai_film.models import (
    AudioGenerationResult, Capability, GenerationJob, JobStatus,
    MusicGenerationRequest, SfxGenerationRequest, VoiceGenerationRequest,
)
from ai_film.providers.fal import client

# csm-1b's speaker_id is just an arbitrary integer voice slot for the call
# (no named voice bank without supplying `context` audio samples, and the
# API documents no upper bound on the value) — derive a stable int per
# speaker name so the same character consistently maps to the same slot
# across separate generate-voice calls. The modulus is intentionally wide:
# a small one (e.g. 5) collides in practice with as few as 2-3 distinct
# character names, silently giving two different characters the same
# voice — the opposite of "locked". 1000 keeps collision probability
# negligible for any realistic per-story character count while staying a
# small, sane integer.
def _speaker_id(speaker: str) -> int:
    if not speaker:
        return 0
    return int(hashlib.sha256(speaker.encode()).hexdigest(), 16) % 1000

VOICE_MODEL_TO_APP_ID = {
    "csm-1b": "fal-ai/csm-1b",
    "speech-02-hd": "fal-ai/minimax/speech-02-hd",
}
DEFAULT_VOICE_PRESET = "Wise_Woman"
SFX_MODEL_TO_APP_ID = {"thinksound": "fal-ai/thinksound"}
MUSIC_MODEL_TO_APP_ID = {"cassetteai-music": "cassetteai/music-generator"}

AudioRequest = VoiceGenerationRequest | SfxGenerationRequest | MusicGenerationRequest


class FalAudioProvider:
    def __init__(self):
        self._jobs: dict[str, tuple[str, str, AudioRequest, float]] = {}

    def submit_voice(self, request: VoiceGenerationRequest) -> GenerationJob:
        app_id = VOICE_MODEL_TO_APP_ID[request.model]
        if request.model == "speech-02-hd":
            input_data = {
                "text": request.text,
                "voice_setting": {"voice_id": request.voice_preset or DEFAULT_VOICE_PRESET},
            }
        else:
            speaker_id = (
                request.speaker_id
                if request.speaker_id is not None
                else _speaker_id(request.speaker)
            )
            input_data = {"scene": [{"speaker_id": speaker_id, "text": request.text}]}
        # Placeholder duration — overridden in get_result() from the API's own
        # duration_ms when the response provides one (speech-02-hd does; csm-1b
        # doesn't, so its duration stays this rough estimate).
        return self._submit(app_id, input_data, request, Capability.VOICE, duration_seconds=2.0)

    def submit_sfx(self, request: SfxGenerationRequest) -> GenerationJob:
        # ThinkSound is video-to-audio: video_url is a required input (its
        # own OpenAPI schema has no way to submit text-only). It never
        # touches this shot's actual video artifact — see get_result's
        # _extract_sfx_audio, which discards the video it returns.
        app_id = SFX_MODEL_TO_APP_ID[request.model]
        input_data = {
            "video_url": client.upload_file(request.video_path),
            "prompt": request.prompt,
        }
        return self._submit(app_id, input_data, request, Capability.SFX, duration_seconds=2.0)

    def submit_music(self, request: MusicGenerationRequest) -> GenerationJob:
        app_id = MUSIC_MODEL_TO_APP_ID[request.model]
        input_data = {"prompt": request.prompt, "duration": int(request.duration_seconds)}
        return self._submit(
            app_id, input_data, request, Capability.MUSIC, duration_seconds=request.duration_seconds
        )

    def _submit(
        self,
        app_id: str,
        input_data: dict,
        request: AudioRequest,
        capability: Capability,
        duration_seconds: float,
    ) -> GenerationJob:
        job, status_url, response_url = client.submit(app_id, input_data, capability)
        self._jobs[job.id] = (status_url, response_url, request, duration_seconds)
        return job

    def poll(self, job: GenerationJob) -> JobStatus:
        status_url, _, _, _ = self._jobs[job.id]
        return client.poll(status_url)

    def get_result(self, job: GenerationJob) -> AudioGenerationResult:
        _, response_url, request, duration_seconds = self._jobs[job.id]
        body = client.result(response_url)
        if isinstance(request, SfxGenerationRequest):
            return _extract_sfx_audio(body, request)
        if isinstance(request, MusicGenerationRequest):
            audio_url = body["audio_file"]["url"]
        else:
            audio_url = body["audio"]["url"]
        size_bytes = client.download(audio_url, request.output_path)
        if body.get("duration_ms") is not None:
            duration_seconds = body["duration_ms"] / 1000.0
        return AudioGenerationResult(
            artifact_path=request.output_path,
            size_bytes=size_bytes,
            duration_seconds=duration_seconds,
        )


def _probe_audio_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _extract_sfx_audio(body: dict, request: SfxGenerationRequest) -> AudioGenerationResult:
    """ThinkSound (fal-ai/thinksound) returns a full video with its
    generated audio baked in — verified against its real OpenAPI schema,
    the output field is "video", there is no separate "audio" field at
    all. We only want the sound it invented, never the video itself: this
    project's own video pipeline (image -> video -> lipsync) must never be
    superseded or replaced by anything ThinkSound returns, since its
    behavior mixing with any audio already in the source video is
    unverified. Download the returned video to a throwaway temp file, pull
    just its audio track out with ffmpeg, and discard the video entirely —
    the shot's actual video artifact is never touched by this."""
    video_url = body["video"]["url"]
    tmp_dir = Path(tempfile.mkdtemp(prefix="ai-film-sfx-"))
    try:
        tmp_video_path = tmp_dir / "thinksound_output.mp4"
        client.download(video_url, str(tmp_video_path))
        output_path = Path(request.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(tmp_video_path),
                "-vn", "-acodec", "pcm_s16le", str(output_path),
            ],
            check=True, capture_output=True,
        )
        return AudioGenerationResult(
            artifact_path=str(output_path),
            size_bytes=output_path.stat().st_size,
            duration_seconds=_probe_audio_duration(output_path),
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
