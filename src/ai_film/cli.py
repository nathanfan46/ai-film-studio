import typer

from ai_film import __version__

app = typer.Typer(name="ai-film", help="AI Film Studio production engine.")


@app.callback(invoke_without_command=True)
def main() -> None:
    """AI Film Studio production engine."""
    pass


@app.command()
def version() -> None:
    """Print the ai-film package version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
