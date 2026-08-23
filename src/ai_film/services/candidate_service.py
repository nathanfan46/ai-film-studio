# src/ai_film/services/candidate_service.py
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from ai_film.approval import is_approved
from ai_film.candidate_store import (
    add_candidates,
    get_candidate,
    load_candidate_set,
    next_candidate_id,
    save_candidate_set,
    scope_for_target,
    target_dir,
)
from ai_film.errors import CostGateError
from ai_film.jobs import run_job
from ai_film.logging_store import write_attempt_log
from ai_film.models import GenerationJob, ImageEditRequest, ImageGenerationRequest
from ai_film.services.generation_service import project_relative_path
from ai_film.shot_store import load_shot, save_shot


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


def select_candidate(project_dir: Path, target: str, candidate_id: str) -> dict:
    candidate_set = load_candidate_set(project_dir, target)
    candidate = get_candidate(candidate_set, candidate_id)
    candidate_set["selected"] = candidate_id
    save_candidate_set(project_dir, target, candidate_set)

    directory = target_dir(project_dir, target)
    source_path = directory / candidate["path"]
    kind = target.split(":", 1)[0]

    if kind in ("character", "env"):
        dest_path = directory / "reference.png"
        shutil.copy(source_path, dest_path)
    else:
        shot_id = target.split(":")[1]
        shot_path = project_dir / "03_shots" / f"{shot_id}.json"
        shot = load_shot(shot_path)
        dest_path = project_dir / "04_storyboard" / f"{shot_id}.png"
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source_path, dest_path)
        shot["generation"]["image"] = {
            **shot["generation"].get("image", {}),
            "status": "completed",
            "artifact": {
                "path": project_relative_path(str(dest_path), project_dir),
                "size_bytes": dest_path.stat().st_size,
                "sha256": None,
            },
        }
        save_shot(shot_path, shot)

    return {
        "target": target,
        "selected": candidate_id,
        "canonical_path": project_relative_path(str(dest_path), project_dir),
    }


def edit_candidate(
    project_dir: Path,
    target: str,
    candidate_id: str,
    instruction: str,
    provider,
    provider_name: str,
    model: str,
    max_attempts: int = 3,
    poll_interval_seconds: float = 0.0,
) -> dict:
    scope = scope_for_target(target)
    if not is_approved(project_dir, scope, target):
        raise CostGateError(
            f"target {target} is not approved for generation (scope={scope}); "
            f"run `ai-film approve-generation --scope {scope} --targets {target},...` first"
        )

    directory = target_dir(project_dir, target)
    candidate_set = load_candidate_set(project_dir, target)
    source = get_candidate(candidate_set, candidate_id)
    output_dir = directory / "candidates"
    base_image_path = str(directory / source["path"])

    def on_attempt(attempt: int, job: GenerationJob | None, outcome: str) -> None:
        write_attempt_log(
            project_dir, _log_group(target), "edit-candidate", attempt,
            job={"provider": job.provider, "id": job.id} if job else None,
            request={"provider": provider_name, "model": model, "instruction": instruction},
            response={"outcome": outcome}, outcome=outcome,
        )

    if provider.supports_edit():
        edit_request = ImageEditRequest(base_image_path=base_image_path, instruction=instruction)
        job_result = run_job(
            submit_fn=lambda: provider.submit_edit(edit_request),
            poll_fn=provider.poll,
            get_result_fn=provider.get_result,
            max_attempts=max_attempts, poll_interval_seconds=poll_interval_seconds,
            on_attempt=on_attempt,
        )
        prompt_used = instruction
    else:
        merged_prompt = f"{source['prompt']}, {instruction}"
        request = ImageGenerationRequest(
            prompt=merged_prompt, model=model,
            output_path=str(output_dir / "edit_regeneration.png"),
        )
        job_result = run_job(
            submit_fn=lambda: provider.submit(request),
            poll_fn=provider.poll,
            get_result_fn=provider.get_result,
            max_attempts=max_attempts, poll_interval_seconds=poll_interval_seconds,
            on_attempt=on_attempt,
        )
        prompt_used = merged_prompt

    new_id = next_candidate_id(candidate_set)
    final_path = output_dir / f"{new_id}.png"
    Path(job_result.result.artifact_path).rename(final_path)
    entry = {
        "id": new_id,
        "path": f"candidates/{new_id}.png",
        "provider": provider_name,
        "model": model,
        "prompt": prompt_used,
        "parent": candidate_id,
        "operation": "edit",
        "job": {"provider": job_result.job.provider, "id": job_result.job.id},
        "estimated_cost": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    add_candidates(project_dir, target, [entry])
    return entry
