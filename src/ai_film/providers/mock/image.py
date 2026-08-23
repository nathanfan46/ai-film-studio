from __future__ import annotations

from pathlib import Path

from ai_film.errors import ProviderError
from ai_film.models import (
    Capability,
    GenerationJob,
    ImageEditRequest,
    ImageGenerationRequest,
    ImageGenerationResult,
    JobStatus,
)


class MockImageProvider:
    """In-memory image provider for tests. No network calls."""

    def __init__(
        self,
        fail_first_n_submits: int = 0,
        polls_until_complete: int = 1,
        supports_edit: bool = False,
    ):
        self.fail_first_n_submits = fail_first_n_submits
        self.polls_until_complete = polls_until_complete
        self._supports_edit = supports_edit
        self._submit_calls = 0
        self._poll_counts: dict[str, int] = {}
        self._requests: dict[str, ImageGenerationRequest] = {}
        self._edits: dict[str, ImageEditRequest] = {}

    def submit(self, request: ImageGenerationRequest) -> GenerationJob:
        self._submit_calls += 1
        if self._submit_calls <= self.fail_first_n_submits:
            raise ProviderError("simulated submit failure")
        job_id = f"mock-image-{self._submit_calls}"
        self._requests[job_id] = request
        self._poll_counts[job_id] = 0
        return GenerationJob(provider="mock", id=job_id, capability=Capability.IMAGE)

    def poll(self, job: GenerationJob) -> JobStatus:
        self._poll_counts[job.id] += 1
        if self._poll_counts[job.id] >= self.polls_until_complete:
            return JobStatus.COMPLETED
        return JobStatus.RUNNING

    def get_result(self, job: GenerationJob) -> ImageGenerationResult:
        if job.id in self._edits:
            request = self._edits[job.id]
            output_path = Path(request.base_image_path).with_name(
                Path(request.base_image_path).stem + "_edited.png"
            )
        else:
            request = self._requests[job.id]
            output_path = Path(request.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"MOCK-PNG-DATA")
        return ImageGenerationResult(
            artifact_path=str(output_path), size_bytes=output_path.stat().st_size
        )

    def get_results(self, job: GenerationJob, output_dir: str) -> list[ImageGenerationResult]:
        request = self._requests[job.id]
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for i in range(1, request.num_candidates + 1):
            path = out_dir / f"result_{i}.png"
            path.write_bytes(b"MOCK-PNG-DATA")
            results.append(
                ImageGenerationResult(artifact_path=str(path), size_bytes=path.stat().st_size)
            )
        return results

    def supports_edit(self) -> bool:
        return self._supports_edit

    def submit_edit(self, request: ImageEditRequest) -> GenerationJob:
        if not self._supports_edit:
            raise NotImplementedError("MockImageProvider(supports_edit=False) cannot edit")
        self._submit_calls += 1
        job_id = f"mock-edit-{self._submit_calls}"
        self._edits[job_id] = request
        self._poll_counts[job_id] = 0
        return GenerationJob(provider="mock", id=job_id, capability=Capability.IMAGE)
