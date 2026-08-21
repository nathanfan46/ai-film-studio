from __future__ import annotations

import json
from pathlib import Path

import typer

from ai_film import __version__
from ai_film.models import Capability
from ai_film.project import init_project
from ai_film.providers.fal.catalog import FalProviderCatalog
from ai_film.schema import validate_shot
from ai_film.shot_store import load_shot

app = typer.Typer(name="ai-film", help="AI Film Studio production engine.")

DEFAULT_PROJECT_PATH = Path("project")


@app.command()
def version() -> None:
    """Print the ai-film package version."""
    typer.echo(__version__)


@app.command(name="init")
def init_cmd(
    title: str,
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
) -> None:
    """Scaffold a new project directory tree and default config.json."""
    init_project(path, title)
    typer.echo(f"Initialized project '{title}' at {path}")


@app.command(name="models")
def models_cmd(
    capability: str = typer.Option(..., "--capability", help="image|video|voice|sfx|music"),
) -> None:
    """List the provider/model catalog for a capability (v1: fal.ai only)."""
    cap = Capability(capability)
    catalog = FalProviderCatalog()
    for model in catalog.models(cap):
        typer.echo(f"{model.provider}/{model.model}  {model.display_name}")


@app.command(name="status")
def status_cmd(path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path")) -> None:
    """Report every shot's aggregate status and a project-wide summary."""
    shots_dir = path / "03_shots"
    counts: dict[str, int] = {}
    for shot_path in sorted(shots_dir.glob("*.json")):
        shot = load_shot(shot_path)
        counts[shot["status"]] = counts.get(shot["status"], 0) + 1
        typer.echo(f"{shot['id']}  {shot['status']}")
    if counts:
        summary = ", ".join(f"{status}: {count}" for status, count in counts.items())
        typer.echo(f"\n{sum(counts.values())} shots — {summary}")


@app.command(name="validate")
def validate_cmd(path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path")) -> None:
    """Validate every shot.json against the schema; exit 1 if any are invalid."""
    shots_dir = path / "03_shots"
    had_errors = False
    for shot_path in sorted(shots_dir.glob("*.json")):
        shot = json.loads(shot_path.read_text())
        errors = validate_shot(shot)
        if errors:
            had_errors = True
            typer.echo(f"{shot_path.name}: INVALID")
            for error in errors:
                typer.echo(f"  - {error}")
        else:
            typer.echo(f"{shot_path.name}: valid")
    if had_errors:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
