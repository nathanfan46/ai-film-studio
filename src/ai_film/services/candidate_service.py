# src/ai_film/services/candidate_service.py
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ai_film.approval import is_approved
from ai_film.candidate_store import (
    add_candidates,
    load_candidate_set,
    next_candidate_id,
    scope_for_target,
    target_dir,
)
from ai_film.errors import CostGateError
from ai_film.jobs import run_job
from ai_film.logging_store import write_attempt_log
from ai_film.models import GenerationJob, ImageGenerationRequest


def _log_group(target: str) -> str:
    return target.replace(":", "_")


def generate_candidates(
    project_dir: Path,
    target: str,
    provider,
    prompt: str,
    model: str,
    count: int,
    provider_name: str,
    max_attempts: int = 3,
    poll_interval_seconds: float = 0.0,
) -> dict:
    scope = scope_for_target(target)
    if not is_approved(project_dir, scope, target):
        raise CostGateError(
            f"target {target} is not approved for generation (scope={scope}); "
            f"run `ai-film approve-generation --scope {scope} --targets {target},...` first"
        )

    output_dir = target_dir(project_dir, target) / "candidates"
    request = ImageGenerationRequest(prompt=prompt, model=model, num_candidates=count)

    def on_attempt(attempt: int, job: GenerationJob | None, outcome: str) -> None:
        write_attempt_log(
            project_dir,
            _log_group(target),
            "candidates",
            attempt,
            job={"provider": job.provider, "id": job.id} if job else None,
            request={"provider": provider_name, "model": model, "count": count},
            response={"outcome": outcome},
            outcome=outcome,
        )

    job_result = run_job(
        submit_fn=lambda: provider.submit(request),
        poll_fn=provider.poll,
        get_result_fn=lambda job: provider.get_results(job, str(output_dir)),
        max_attempts=max_attempts,
        poll_interval_seconds=poll_interval_seconds,
        on_attempt=on_attempt,
    )

    candidate_set = load_candidate_set(project_dir, target)
    created_at = datetime.now(timezone.utc).isoformat()
    entries = []
    for raw_result in job_result.result:
        candidate_id = next_candidate_id({"candidates": candidate_set["candidates"] + entries})
        final_path = output_dir / f"{candidate_id}.png"
        Path(raw_result.artifact_path).rename(final_path)
        entries.append({
            "id": candidate_id,
            "path": f"candidates/{candidate_id}.png",
            "provider": provider_name,
            "model": model,
            "prompt": prompt,
            "parent": None,
            "operation": "generate",
            "job": {"provider": job_result.job.provider, "id": job_result.job.id},
            "estimated_cost": None,
            "created_at": created_at,
        })

    add_candidates(project_dir, target, entries)
    return {"target": target, "added": [e["id"] for e in entries]}
