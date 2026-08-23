from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import typer

from ai_film import __version__
from ai_film.approval import approve_generation as approve_generation_service
from ai_film.batch import run_bounded
from ai_film.errors import CostGateError, ProviderError
from ai_film.models import Capability
from ai_film.project import init_project
from ai_film.prompts import build_image_prompt, build_video_prompt
from ai_film.providers.fal.catalog import FalProviderCatalog
from ai_film.providers.registry import resolve_provider
from ai_film.render import RenderPreflightError, build_manifest
from ai_film.render import render as render_engine
from ai_film.review_gallery import build_gallery, open_in_browser
from ai_film.schema import validate_shot
from ai_film.services.candidate_service import (
    edit_candidate as edit_candidate_service,
    generate_candidates as generate_candidates_service,
    select_candidate as select_candidate_service,
)
from ai_film.services.generation_service import (
    generate_image as generate_image_service,
    generate_music as generate_music_service,
    generate_sfx as generate_sfx_service,
    generate_video as generate_video_service,
    generate_voice as generate_voice_service,
)
from ai_film.shot_store import load_shot, save_shot

app = typer.Typer(name="ai-film", help="AI Film Studio production engine.")

DEFAULT_PROJECT_PATH = Path("project")


@app.command()
def version() -> None:
    """Print the ai-film package version."""
    typer.echo(__version__)


@app.command(name="init")
def init_cmd(title: str, path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path")) -> None:
    """Scaffold a new project directory tree and default config.json."""
    init_project(path, title)
    typer.echo(f"Initialized project '{title}' at {path}")


@app.command(name="models")
def models_cmd(
    capability: str = typer.Option(..., "--capability", help="image|video|voice|sfx|music"),
) -> None:
    """List the provider/model catalog for a capability (v1: fal.ai only)."""
    try:
        capability_enum = Capability(capability)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    catalog = FalProviderCatalog()
    for model in catalog.models(capability_enum):
        typer.echo(f"{model.provider}/{model.model}  {model.display_name}")


@app.command(name="status")
def status_cmd(path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path")) -> None:
    """Report every shot's aggregate status and a project-wide summary."""
    counts: dict[str, int] = {}
    for shot_path in sorted((path / "03_shots").glob("*.json")):
        shot = load_shot(shot_path)
        counts[shot["status"]] = counts.get(shot["status"], 0) + 1
        typer.echo(f"{shot['id']}  {shot['status']}")
    if counts:
        summary = ", ".join(f"{status}: {count}" for status, count in counts.items())
        typer.echo(f"\n{sum(counts.values())} shots — {summary}")


@app.command(name="validate")
def validate_cmd(path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path")) -> None:
    """Validate every shot.json against the schema; exit 1 if any are invalid."""
    had_errors = False
    for shot_path in sorted((path / "03_shots").glob("*.json")):
        errors = validate_shot(json.loads(shot_path.read_text()))
        if errors:
            had_errors = True
            typer.echo(f"{shot_path.name}: INVALID")
            for error in errors:
                typer.echo(f"  - {error}")
        else:
            typer.echo(f"{shot_path.name}: valid")
    if had_errors:
        raise typer.Exit(code=1)


def _stage_config(path: Path, stage: str) -> dict:
    config = json.loads((path / "config.json").read_text())
    return config["providers"][stage], config["generation"]


def _run_generation(shot_id: str, stage_name: str, run_fn) -> None:
    try:
        result = run_fn()
    except CostGateError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    except ProviderError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{shot_id}: {stage_name} {result['status']}")


@app.command(name="generate-image")
def generate_image_cmd(
    shot: str = typer.Option(..., "--shot"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    stage_config, gen_config = _stage_config(path, "image")
    shot_path = path / "03_shots" / f"{shot}.json"
    shot_data = load_shot(shot_path)
    references = [c["reference"] for c in shot_data.get("characters", []) if c.get("reference")]

    def _run():
        provider = resolve_provider(Capability.IMAGE, stage_config["provider"])
        return generate_image_service(
            project_dir=path, shot_path=shot_path, provider=provider,
            prompt=build_image_prompt(shot_data), model=stage_config["model"],
            reference_paths=references, output_path=path / "04_storyboard" / f"{shot}.png",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
        )

    _run_generation(shot, "image", _run)


@app.command(name="generate-video")
def generate_video_cmd(
    shot: str = typer.Option(..., "--shot"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    stage_config, gen_config = _stage_config(path, "video")
    shot_path = path / "03_shots" / f"{shot}.json"
    shot_data = load_shot(shot_path)
    references = [c["reference"] for c in shot_data.get("characters", []) if c.get("reference")]

    def _run():
        provider = resolve_provider(Capability.VIDEO, stage_config["provider"])
        return generate_video_service(
            project_dir=path, shot_path=shot_path, provider=provider,
            prompt=build_video_prompt(shot_data), model=stage_config["model"],
            reference_paths=references, duration_seconds=shot_data["duration_seconds"],
            output_path=path / "05_video" / f"{shot}.mp4",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
        )

    _run_generation(shot, "video", _run)


@app.command(name="generate-voice")
def generate_voice_cmd(
    shot: str = typer.Option(..., "--shot"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    stage_config, gen_config = _stage_config(path, "voice")
    shot_path = path / "03_shots" / f"{shot}.json"
    shot_data = load_shot(shot_path)
    dialogue = shot_data.get("dialogue", {})

    def _run():
        provider = resolve_provider(Capability.VOICE, stage_config["provider"])
        return generate_voice_service(
            project_dir=path, shot_path=shot_path, provider=provider,
            text=dialogue.get("text", ""), model=stage_config["model"],
            speaker=dialogue.get("speaker", ""),
            output_path=path / "06_audio" / "dialogue" / f"{shot}.wav",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
        )

    _run_generation(shot, "voice", _run)


@app.command(name="generate-sfx")
def generate_sfx_cmd(
    shot: str = typer.Option(..., "--shot"),
    prompt: str = typer.Option(..., "--prompt"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    stage_config, gen_config = _stage_config(path, "sfx")
    shot_path = path / "03_shots" / f"{shot}.json"

    def _run():
        provider = resolve_provider(Capability.SFX, stage_config["provider"])
        return generate_sfx_service(
            project_dir=path, shot_path=shot_path, provider=provider,
            prompt=prompt, model=stage_config["model"],
            output_path=path / "06_audio" / "sfx" / f"{shot}.wav",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
        )

    _run_generation(shot, "sfx", _run)


@app.command(name="generate-music")
def generate_music_cmd(
    shot: str = typer.Option(..., "--shot"),
    prompt: str = typer.Option(..., "--prompt"),
    duration_seconds: float = typer.Option(30.0, "--duration-seconds"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    stage_config, gen_config = _stage_config(path, "music")
    shot_path = path / "03_shots" / f"{shot}.json"

    def _run():
        provider = resolve_provider(Capability.MUSIC, stage_config["provider"])
        return generate_music_service(
            project_dir=path, shot_path=shot_path, provider=provider,
            prompt=prompt, model=stage_config["model"], duration_seconds=duration_seconds,
            output_path=path / "06_audio" / "music" / f"{shot}.wav",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
        )

    _run_generation(shot, "music", _run)


@app.command(name="check-continuity")
def check_continuity_cmd(
    shot: str = typer.Option(..., "--shot"),
    status: str = typer.Option(..., "--status", help="passed|warning|failed"),
    issue: list[str] = typer.Option([], "--issue"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Record the Continuity agent's text/spec-level judgment onto shot.json."""
    shot_path = path / "03_shots" / f"{shot}.json"
    shot_data = load_shot(shot_path)
    shot_data["continuity"] = {
        "status": status,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "issues": list(issue),
    }
    try:
        save_shot(shot_path, shot_data)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{shot}: continuity {status}")


@app.command(name="approve-generation")
def approve_generation_cmd(
    scope: str = typer.Option(..., "--scope", help="bibles|storyboard"),
    targets: str = typer.Option(..., "--targets", help="comma-separated target IDs"),
    estimated_cost: float = typer.Option(None, "--estimated-cost"),
    revision: int = typer.Option(None, "--revision"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    target_ids = [t for t in targets.split(",") if t]
    try:
        approve_generation_service(path, scope, target_ids, estimated_cost, revision)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"approved {scope}: {len(target_ids)} target(s)")


@app.command(name="render")
def render_cmd(path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path")) -> None:
    manifest = build_manifest(path)
    try:
        output_path = render_engine(path, manifest)
    except RenderPreflightError as exc:
        typer.echo("render preflight failed:", err=True)
        for error in exc.errors:
            typer.echo(f"  - {error}", err=True)
        raise typer.Exit(code=1)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"rendered {output_path}")


_BATCH_SERVICE_BY_STAGE = {
    "image": (Capability.IMAGE, generate_image_service),
    "video": (Capability.VIDEO, generate_video_service),
    "voice": (Capability.VOICE, generate_voice_service),
}


def _build_stage_call(path: Path, shot_id: str, stage: str, force: bool):
    capability, service_fn = _BATCH_SERVICE_BY_STAGE[stage]
    stage_config, gen_config = _stage_config(path, stage)
    shot_path = path / "03_shots" / f"{shot_id}.json"
    shot_data = load_shot(shot_path)
    provider = resolve_provider(capability, stage_config["provider"])
    references = [c["reference"] for c in shot_data.get("characters", []) if c.get("reference")]

    if stage == "image":
        return lambda: service_fn(
            project_dir=path, shot_path=shot_path, provider=provider,
            prompt=build_image_prompt(shot_data), model=stage_config["model"],
            reference_paths=references, output_path=path / "04_storyboard" / f"{shot_id}.png",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
        )
    if stage == "video":
        return lambda: service_fn(
            project_dir=path, shot_path=shot_path, provider=provider,
            prompt=build_video_prompt(shot_data), model=stage_config["model"],
            reference_paths=references, duration_seconds=shot_data["duration_seconds"],
            output_path=path / "05_video" / f"{shot_id}.mp4",
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
        )
    dialogue = shot_data.get("dialogue", {})
    return lambda: service_fn(
        project_dir=path, shot_path=shot_path, provider=provider,
        text=dialogue.get("text", ""), model=stage_config["model"],
        speaker=dialogue.get("speaker", ""),
        output_path=path / "06_audio" / "dialogue" / f"{shot_id}.wav",
        provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
        poll_interval_seconds=gen_config["poll_interval_seconds"], force=force,
    )


@app.command(name="generate-all")
def generate_all_cmd(
    stage: str = typer.Option(..., "--stage", help="image|video|voice"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Generate `stage` for every shot, bounded by config.generation.max_parallel_jobs."""
    if stage not in _BATCH_SERVICE_BY_STAGE:
        typer.echo(
            f"generate-all supports stage in {sorted(_BATCH_SERVICE_BY_STAGE)}, got {stage!r}",
            err=True,
        )
        raise typer.Exit(code=1)

    _, gen_config = _stage_config(path, stage)
    shot_ids = [p.stem for p in sorted((path / "03_shots").glob("*.json"))]
    calls = [_build_stage_call(path, shot_id, stage, force) for shot_id in shot_ids]
    results = run_bounded(calls, max_workers=gen_config["max_parallel_jobs"])

    failed_ids = {shot_id for shot_id, error in zip(shot_ids, results) if error is not None}
    for shot_id in shot_ids:
        status = "FAILED" if shot_id in failed_ids else "done"
        typer.echo(f"{shot_id}: {stage} {status}")
    if failed_ids:
        for shot_id, error in zip(shot_ids, results):
            if error is not None:
                typer.echo(f"  {shot_id}: {error}", err=True)
        raise typer.Exit(code=1)


def _shot_default_prompt(path: Path, shot_id: str) -> str:
    shot_data = load_shot(path / "03_shots" / f"{shot_id}.json")
    return build_image_prompt(shot_data)


@app.command(name="generate-candidates")
def generate_candidates_cmd(
    target: str = typer.Option(..., "--target"),
    count: int = typer.Option(..., "--count"),
    prompt: str = typer.Option(None, "--prompt"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Generate N image candidates for a character:/env:/shot: target (cost-gated)."""
    stage_config, gen_config = _stage_config(path, "image")

    if prompt is None:
        if target.startswith("shot:"):
            shot_id = target.split(":")[1]
            prompt = _shot_default_prompt(path, shot_id)
        else:
            typer.echo("--prompt is required for character:/env: targets", err=True)
            raise typer.Exit(code=1)

    def _run():
        provider = resolve_provider(Capability.IMAGE, stage_config["provider"])
        return generate_candidates_service(
            project_dir=path, target=target, provider=provider,
            prompt=prompt, model=stage_config["model"], count=count,
            provider_name=stage_config["provider"], max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"],
        )

    try:
        result = _run()
    except CostGateError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    except ProviderError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{target}: generated {len(result['added'])} candidate(s): {', '.join(result['added'])}")


@app.command(name="review")
def review_cmd(
    target: str = typer.Option(..., "--target"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Build (or rebuild) the candidate review gallery and open it in the browser."""
    try:
        html_path = build_gallery(path, target)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    open_in_browser(html_path)
    typer.echo(f"opened {html_path}")


@app.command(name="select-candidate")
def select_candidate_cmd(
    target: str = typer.Option(..., "--target"),
    id: str = typer.Option(..., "--id"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Lock in a candidate as the canonical artifact for a target."""
    try:
        result = select_candidate_service(path, target, id)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{target}: selected {id} -> {result['canonical_path']}")


@app.command(name="edit-candidate")
def edit_candidate_cmd(
    target: str = typer.Option(..., "--target"),
    id: str = typer.Option(..., "--id"),
    instruction: str = typer.Option(..., "--instruction"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Refine a candidate via true edit (if the provider supports it) or regeneration."""
    stage_config, gen_config = _stage_config(path, "image")

    def _run():
        provider = resolve_provider(Capability.IMAGE, stage_config["provider"])
        return edit_candidate_service(
            project_dir=path, target=target, candidate_id=id, instruction=instruction,
            provider=provider, provider_name=stage_config["provider"], model=stage_config["model"],
            max_attempts=gen_config["max_attempts"],
            poll_interval_seconds=gen_config["poll_interval_seconds"],
        )

    try:
        entry = _run()
    except CostGateError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    except ProviderError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"{target}: added candidate {entry['id']} (edit of {entry['parent']})")


if __name__ == "__main__":
    app()
