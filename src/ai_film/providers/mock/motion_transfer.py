from __future__ import annotations

from pathlib import Path

from ai_film.errors import ProviderError
from ai_film.models import (
    Capability,
    GenerationJob,
    JobStatus,
    MotionTransferRequest,
    VideoGenerationResult,
)

# MotionTransferRequest carries no duration_seconds field (real duration is
# entirely provider-determined — see providers/fal/motion_transfer.py's own
# duration probing), so unlike every other mock provider this one has no
# input value to echo back. Any fixed positive placeholder works since
# nothing asserts on its exact value.
_MOCK_DURATION_SECONDS = 2.0


class MockMotionTransferProvider:
    def __init__(self, fail_first_n_submits: int = 0, polls_until_complete: int = 1):
        self.fail_first_n_submits = fail_first_n_submits
        self.polls_until_complete = polls_until_complete
        self._submit_calls = 0
        self._poll_counts: dict[str, int] = {}
        self._requests: dict[str, MotionTransferRequest] = {}

    def submit(self, request: MotionTransferRequest) -> GenerationJob:
        self._submit_calls += 1
        if self._submit_calls <= self.fail_first_n_submits:
            raise ProviderError("simulated submit failure")
        job_id = f"mock-motion-transfer-{self._submit_calls}"
        self._requests[job_id] = request
        self._poll_counts[job_id] = 0
        return GenerationJob(provider="mock", id=job_id, capability=Capability.MOTION_TRANSFER)

    def poll(self, job: GenerationJob) -> JobStatus:
        self._poll_counts[job.id] += 1
        if self._poll_counts[job.id] >= self.polls_until_complete:
            return JobStatus.COMPLETED
        return JobStatus.RUNNING

    def get_result(self, job: GenerationJob) -> VideoGenerationResult:
        request = self._requests[job.id]
        output_path = Path(request.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"MOCK-MP4-DATA")
        return VideoGenerationResult(
            artifact_path=str(output_path),
            size_bytes=output_path.stat().st_size,
            duration_seconds=_MOCK_DURATION_SECONDS,
        )
