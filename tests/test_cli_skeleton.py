import os

from typer.testing import CliRunner

from ai_film.cli import _load_env_file, app

runner = CliRunner()


def test_help_lists_program_name():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ai-film" in result.output.lower() or "ai_film" in result.output.lower()


def test_version_command_prints_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_load_env_file_populates_os_environ_from_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AI_FILM_TEST_VAR", raising=False)
    (tmp_path / ".env").write_text("AI_FILM_TEST_VAR=hello\n")

    _load_env_file()

    assert os.environ["AI_FILM_TEST_VAR"] == "hello"


def test_load_env_file_is_a_noop_without_a_dotenv_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AI_FILM_TEST_VAR", raising=False)

    _load_env_file()  # must not raise even when no .env exists anywhere above tmp_path

    assert "AI_FILM_TEST_VAR" not in os.environ
