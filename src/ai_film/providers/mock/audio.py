from __future__ import annotations

from pathlib import Path

from ai_film.errors import ProviderError
from ai_film.models import (
    AudioGenerationResult,
    Capability,
    GenerationJob,
    JobStatus,
    MusicGenerationRequest,
    SfxGenerationRequest,
    VoiceGenerationRequest,
)

AudioRequest = VoiceGenerationRequest | SfxGenerationRequest | MusicGenerationRequest


class MockAudioProvider:
    def __init__(self, fail_first_n_submits: int = 0, polls_until_complete: int = 1):
        self.fail_first_n_submits = fail_first_n_submits
        self.polls_until_complete = polls_until_complete
        self._submit_calls = 0
        self._poll_counts: dict[str, int] = {}
        self._requests: dict[str, AudioRequest] = {}

    def _submit(self, request: AudioRequest, capability: Capability) -> GenerationJob:
        self._submit_calls += 1
        if self._submit_calls <= self.fail_first_n_submits:
            raise ProviderError("simulated submit failure")
        job_id = f"mock-audio-{self._submit_calls}"
        self._requests[job_id] = request
        self._poll_counts[job_id] = 0
        return GenerationJob(provider="mock", id=job_id, capability=capability)

    def submit_voice(self, request: VoiceGenerationRequest) -> GenerationJob:
        return self._submit(request, Capability.VOICE)

    def submit_sfx(self, request: SfxGenerationRequest) -> GenerationJob:
        return self._submit(request, Capability.SFX)

    def submit_music(self, request: MusicGenerationRequest) -> GenerationJob:
        return self._submit(request, Capability.MUSIC)

    def poll(self, job: GenerationJob) -> JobStatus:
        self._poll_counts[job.id] += 1
        if self._poll_counts[job.id] >= self.polls_until_complete:
            return JobStatus.COMPLETED
        return JobStatus.RUNNING

    def get_result(self, job: GenerationJob) -> AudioGenerationResult:
        request = self._requests[job.id]
        output_path = Path(request.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"MOCK-AUDIO-DATA")
        duration = getattr(request, "duration_seconds", 2.0)
        return AudioGenerationResult(
            artifact_path=str(output_path),
            size_bytes=output_path.stat().st_size,
            duration_seconds=duration,
        )
