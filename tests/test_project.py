import json
from pathlib import Path

from ai_film.project import PROJECT_DIRS, init_project


def test_init_project_creates_all_directories(tmp_path: Path):
    project_dir = tmp_path / "project"
    init_project(project_dir, title="The Last Ship")
    for rel in PROJECT_DIRS:
        assert (project_dir / rel).is_dir(), f"missing {rel}"


def test_init_project_writes_default_config(tmp_path: Path):
    project_dir = tmp_path / "project"
    init_project(project_dir, title="The Last Ship")
    config = json.loads((project_dir / "config.json").read_text())
    assert config["title"] == "The Last Ship"
    assert "image" in config["providers"]
    assert config["generation"]["max_attempts"] == 3
    assert config["generation_approval"] == {}


def test_init_project_does_not_overwrite_existing_config(tmp_path: Path):
    project_dir = tmp_path / "project"
    init_project(project_dir, title="The Last Ship")
    config_path = project_dir / "config.json"
    config = json.loads(config_path.read_text())
    config["generation"]["max_attempts"] = 7
    config_path.write_text(json.dumps(config))

    init_project(project_dir, title="The Last Ship")  # re-init

    reloaded = json.loads(config_path.read_text())
    assert reloaded["generation"]["max_attempts"] == 7


def test_project_dirs_includes_review_directory():
    assert "07_review" in PROJECT_DIRS
