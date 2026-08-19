from typer.testing import CliRunner

from ai_film.cli import app

runner = CliRunner()


def test_help_lists_program_name():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ai-film" in result.output.lower() or "ai_film" in result.output.lower()


def test_version_command_prints_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output
