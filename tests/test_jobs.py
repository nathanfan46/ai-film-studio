import pytest

from ai_film.errors import ProviderError
from ai_film.jobs import run_job
from ai_film.models import GenerationJob, Capability, JobStatus


def _job() -> GenerationJob:
    return GenerationJob(provider="mock", id="j1", capability=Capability.IMAGE)


def test_run_job_succeeds_on_first_attempt():
    result = run_job(
        submit_fn=lambda: _job(),
        poll_fn=lambda job: JobStatus.COMPLETED,
        get_result_fn=lambda job: "artifact",
        max_attempts=3,
    )
    assert result.result == "artifact"
    assert result.attempts == 1


def test_run_job_retries_and_succeeds():
    attempts = {"n": 0}

    def submit_fn():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ProviderError("boom")
        return _job()

    result = run_job(
        submit_fn=submit_fn,
        poll_fn=lambda job: JobStatus.COMPLETED,
        get_result_fn=lambda job: "artifact",
        max_attempts=3,
    )
    assert result.attempts == 3


def test_run_job_raises_after_exhausting_attempts():
    def submit_fn():
        raise ProviderError("always fails")

    with pytest.raises(ProviderError):
        run_job(
            submit_fn=submit_fn,
            poll_fn=lambda job: JobStatus.COMPLETED,
            get_result_fn=lambda job: "artifact",
            max_attempts=3,
        )


def test_run_job_calls_on_attempt_for_each_try():
    log = []

    def submit_fn():
        if len(log) < 2:
            raise ProviderError("boom")
        return _job()

    run_job(
        submit_fn=submit_fn,
        poll_fn=lambda job: JobStatus.COMPLETED,
        get_result_fn=lambda job: "artifact",
        max_attempts=3,
        on_attempt=lambda attempt, job, outcome: log.append((attempt, outcome)),
    )
    assert len(log) == 3
    assert log[-1][1] == "completed"
