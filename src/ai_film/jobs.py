from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

from ai_film.errors import ProviderError
from ai_film.models import GenerationJob, JobStatus

T = TypeVar("T")


@dataclass
class JobResult(Generic[T]):
    job: GenerationJob
    result: T
    attempts: int


def run_job(
    submit_fn: Callable[[], GenerationJob],
    poll_fn: Callable[[GenerationJob], JobStatus],
    get_result_fn: Callable[[GenerationJob], T],
    max_attempts: int = 3,
    poll_interval_seconds: float = 0.0,
    sleep_fn: Callable[[float], None] = time.sleep,
    on_attempt: Callable[[int, GenerationJob | None, str], None] | None = None,
) -> JobResult[T]:
    last_error = "unknown error"
    for attempt in range(1, max_attempts + 1):
        job: GenerationJob | None = None
        try:
            job = submit_fn()
            status = poll_fn(job)
            while status in (JobStatus.QUEUED, JobStatus.RUNNING):
                sleep_fn(poll_interval_seconds)
                status = poll_fn(job)
            if status == JobStatus.COMPLETED:
                result = get_result_fn(job)
                if on_attempt:
                    on_attempt(attempt, job, "completed")
                return JobResult(job=job, result=result, attempts=attempt)
            last_error = f"job ended with status {status.value}"
        except ProviderError as exc:
            last_error = str(exc)
        if on_attempt:
            on_attempt(attempt, job, f"failed: {last_error}")
    raise ProviderError(f"generation failed after {max_attempts} attempts: {last_error}")
