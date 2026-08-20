from __future__ import annotations

from ai_film.models import (
    AudioGenerationResult, Capability, GenerationJob, JobStatus,
    MusicGenerationRequest, SfxGenerationRequest, VoiceGenerationRequest,
)
from ai_film.providers.fal import client

VOICE_MODEL_TO_APP_ID = {"csm-1b": "fal-ai/csm-1b"}
SFX_MODEL_TO_APP_ID = {"thinksound": "fal-ai/thinksound"}
MUSIC_MODEL_TO_APP_ID = {"csm-1b": "fal-ai/csm-1b"}

AudioRequest = VoiceGenerationRequest | SfxGenerationRequest | MusicGenerationRequest


class FalAudioProvider:
    def __init__(self):
        self._jobs: dict[str, tuple[str, str, AudioRequest, float]] = {}

    def submit_voice(self, request: VoiceGenerationRequest) -> GenerationJob:
        app_id = VOICE_MODEL_TO_APP_ID[request.model]
        input_data = {"text": request.text, "speaker_id": request.speaker or "0"}
        return self._submit(app_id, input_data, request, Capability.VOICE, duration_seconds=2.0)

    def submit_sfx(self, request: SfxGenerationRequest) -> GenerationJob:
        app_id = SFX_MODEL_TO_APP_ID[request.model]
        input_data = {"prompt": request.prompt}
        return self._submit(app_id, input_data, request, Capability.SFX, duration_seconds=2.0)

    def submit_music(self, request: MusicGenerationRequest) -> GenerationJob:
        app_id = MUSIC_MODEL_TO_APP_ID[request.model]
        input_data = {"prompt": request.prompt, "duration": request.duration_seconds}
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
        audio_url = body["audio"]["url"]
        size_bytes = client.download(audio_url, request.output_path)
        return AudioGenerationResult(
            artifact_path=request.output_path,
            size_bytes=size_bytes,
            duration_seconds=duration_seconds,
        )
