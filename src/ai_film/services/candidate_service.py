# src/ai_film/services/candidate_service.py
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from ai_film.approval import is_approved
from ai_film.camera_variants import resolve_camera_variants
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
from ai_film.prompts import build_image_prompt
from ai_film.scene_continuity import load_continuity, scene_id_for_shot
from ai_film.services.generation_service import project_relative_path, sha256_of_file
from ai_film.shot_store import load_shot, previous_shot_image_reference, save_shot


def _log_group(target: str) -> str:
    return target.replace(":", "_")


def _source_assets_for_shot(project_dir: Path, shot: dict, shot_id: str) -> list[dict]:
    """Every reference path this shot's candidates were actually generated
    against, hashed the same way generate_image records them — so a
    candidate-locked shot is tracked by check-stale exactly as fully as a
    shot imaged via generate-image. Deliberately mirrors cli.py's
    _image_references (character/environment references, the previous
    shot's locked image, and the scene's continuity-master reference),
    minus its CLI-only "no continuity anchor yet" warning: generate-candidates
    resolves references through that exact same function, so a shot's
    candidate image already depends on the previous shot's image and the
    continuity master for every shot but a scene's first — the common
    case, not a rare one — and this must track the same set or
    check-stale silently under-reports for most shots in a scene."""
    paths = [
        project_dir / c["reference"]
        for c in shot.get("characters", [])
        if c.get("reference")
    ]
    environment_reference = (shot.get("environment") or {}).get("reference")
    if environment_reference:
        paths.append(project_dir / environment_reference)

    prev_ref = previous_shot_image_reference(project_dir, shot_id)
    prev_path = project_dir / prev_ref if prev_ref else None

    scene_id = scene_id_for_shot(shot_id)
    if scene_id is not None:
        continuity = load_continuity(project_dir, scene_id)
        master_ref = continuity.get("master_reference_image")
        if master_ref and continuity.get("master_shot") != shot_id:
            master_path = project_dir / master_ref
            if master_path != prev_path:
                paths.append(master_path)

    if prev_path:
        paths.append(prev_path)

    return [
        {"path": project_relative_path(str(p), project_dir), "sha256": sha256_of_file(p)}
        for p in paths
        if p.exists()
    ]


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
    reference_paths: list[str] | None = None,
    camera_variant: str | None = None,
) -> dict:
    if count <= 0:
        raise ValueError(f"count must be positive, got {count}")

    scope = scope_for_target(target)
    if not is_approved(project_dir, scope, target):
        raise CostGateError(
            f"target {target} is not approved for generation (scope={scope}); "
            f"run `ai-film approve-generation --scope {scope} --targets {target},...` first"
        )

    output_dir = target_dir(project_dir, target) / "candidates"
    request = ImageGenerationRequest(
        prompt=prompt, model=model, num_candidates=count,
        reference_paths=reference_paths or [],
    )

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
            "camera_variant": camera_variant,
        })

    add_candidates(project_dir, target, entries)
    return {"target": target, "added": [e["id"] for e in entries]}


def generate_shot_candidates(
    project_dir: Path,
    target: str,
    provider,
    shot: dict,
    model: str,
    count: int,
    provider_name: str,
    max_attempts: int = 3,
    poll_interval_seconds: float = 0.0,
    reference_paths: list[str] | None = None,
    cameras: list[str] | None = None,
    spatial: dict | None = None,
) -> dict:
    """Generate `count` shot-target candidates, each a distinct camera
    framing rather than `count` re-rolls of the same prompt — a provider's
    num_candidates only re-samples one prompt, so each variant is its own
    single-candidate job (see resolve_camera_variants for the reserved-slot
    and dedup rules). shot.camera itself is never mutated here; each
    variant's prompt is built from a shallow copy so the persisted shot
    only changes later, in select_candidate, once a variant is actually
    picked."""
    original = (shot.get("camera") or {}).get("shot")
    variants = resolve_camera_variants(original, count, cameras)

    added: list[str] = []
    for label in variants:
        variant_shot = {**shot, "camera": {**(shot.get("camera") or {}), "shot": label}}
        prompt = build_image_prompt(variant_shot, spatial=spatial)
        result = generate_candidates(
            project_dir, target, provider, prompt, model, count=1,
            provider_name=provider_name, max_attempts=max_attempts,
            poll_interval_seconds=poll_interval_seconds,
            reference_paths=reference_paths, camera_variant=label,
        )
        added.extend(result["added"])

    return {"target": target, "added": added}


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

        # A camera-variant candidate's shot.camera.shot is the actual camera
        # instruction its prompt was built from; camera_variant on the
        # candidate is provenance describing why it exists. Once selected,
        # the variant becomes canonical — shot.camera.shot must agree with
        # what's actually locked, or build_video_prompt/check-stale keep
        # reading the pre-selection value. Legacy candidates (no
        # camera_variant, e.g. anything selected before this feature or any
        # character:/env: selection) leave shot.camera untouched.
        camera_variant = candidate.get("camera_variant")
        camera_before = (shot.get("camera") or {}).get("shot")
        if camera_variant:
            shot["camera"] = {**shot.get("camera", {}), "shot": camera_variant}

        # NOTE: this intentionally does not go through archive_stage_artifact
        # (see src/ai_film/services/generation_service.py) — select_candidate
        # predates the media-review-layer's version/history bookkeeping, and
        # candidate selection already preserves prior state via the numbered
        # candidates/ directory, a different mechanism than history/. If a
        # shot's image was previously force-regenerated via generate-image
        # (bumping version/history), a subsequent select-candidate call here
        # will overwrite that artifact without archiving it — known, low-risk
        # since media_review.py deliberately doesn't render the image stage.
        shot["generation"]["image"] = {
            **shot["generation"].get("image", {}),
            "status": "completed",
            "artifact": {
                "path": project_relative_path(str(dest_path), project_dir),
                "size_bytes": dest_path.stat().st_size,
                "sha256": None,
            },
            "source_assets": _source_assets_for_shot(project_dir, shot, shot_id),
        }
        save_shot(shot_path, shot)

        if camera_variant:
            write_attempt_log(
                project_dir, _log_group(target), "select-candidate", 1,
                job=None,
                request={"candidate_id": candidate_id, "camera_variant": camera_variant},
                response={
                    "camera_shot_before": camera_before,
                    "camera_shot_after": camera_variant,
                },
                outcome="camera_updated" if camera_before != camera_variant else "camera_unchanged",
            )

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
