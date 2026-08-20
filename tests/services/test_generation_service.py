from pathlib import Path

import pytest

from ai_film.approval import approve_generation
from ai_film.errors import CostGateError
from ai_film.providers.mock.image import MockImageProvider
from ai_film.services.generation_service import generate_image
from ai_film.shot_store import load_shot, save_shot


def _project(tmp_path: Path) -> Path:
    (tmp_path / "config.json").write_text('{"providers": {}, "generation": {}}')
    return tmp_path


def _shot_path(project_dir: Path, shot_id: str = "S01_SH01") -> Path:
    path = project_dir / "03_shots" / f"{shot_id}.json"
    save_shot(
        path,
        {
            "schema_version": "1.0",
            "id": shot_id,
            "status": "draft",
            "duration_seconds": 5,
            "continuity": {"status": "passed", "checked_at": None, "issues": []},
            "generation": {
                "image": {"status": "pending", "attempts": 0},
                "video": {"status": "pending", "attempts": 0},
                "voice": {"status": "pending", "attempts": 0},
                "sfx": {"status": "not_required"},
                "music": {"status": "not_required"},
            },
        },
    )
    return path


def test_generate_image_blocked_without_approval(tmp_path: Path):
    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    with pytest.raises(CostGateError):
        generate_image(
            project_dir=project_dir,
            shot_path=shot_path,
            provider=MockImageProvider(),
            prompt="a girl in a corridor",
            model="nano-banana",
            reference_paths=[],
            output_path=project_dir / "04_storyboard" / "S01_SH01.png",
            provider_name="mock",
        )


def test_generate_image_succeeds_once_approved(tmp_path: Path):
    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)

    stage = generate_image(
        project_dir=project_dir,
        shot_path=shot_path,
        provider=MockImageProvider(),
        prompt="a girl in a corridor",
        model="nano-banana",
        reference_paths=[],
        output_path=project_dir / "04_storyboard" / "S01_SH01.png",
        provider_name="mock",
    )

    assert stage["status"] == "completed"
    assert stage["attempts"] == 1
    assert Path(stage["artifact"]["path"]).exists()

    shot = load_shot(shot_path)
    assert shot["generation"]["image"]["status"] == "completed"
    # video/voice are still "pending" and continuity is "passed", so the derived
    # top-level status is "ready", not "completed" — see Task 5's compute_status.
    assert shot["status"] == "ready"


def test_generate_image_is_idempotent_by_default(tmp_path: Path):
    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)
    provider = MockImageProvider()

    kwargs = dict(
        project_dir=project_dir,
        shot_path=shot_path,
        provider=provider,
        prompt="a girl in a corridor",
        model="nano-banana",
        reference_paths=[],
        output_path=project_dir / "04_storyboard" / "S01_SH01.png",
        provider_name="mock",
    )
    generate_image(**kwargs)
    generate_image(**kwargs)  # second call should skip, not resubmit
    assert provider._submit_calls == 1


def test_generate_image_force_regenerates(tmp_path: Path):
    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)
    provider = MockImageProvider()

    kwargs = dict(
        project_dir=project_dir,
        shot_path=shot_path,
        provider=provider,
        prompt="a girl in a corridor",
        model="nano-banana",
        reference_paths=[],
        output_path=project_dir / "04_storyboard" / "S01_SH01.png",
        provider_name="mock",
    )
    generate_image(**kwargs)
    generate_image(**{**kwargs, "force": True})
    assert provider._submit_calls == 2


def test_generate_image_writes_attempt_log(tmp_path: Path):
    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)
    generate_image(
        project_dir=project_dir,
        shot_path=shot_path,
        provider=MockImageProvider(),
        prompt="a girl in a corridor",
        model="nano-banana",
        reference_paths=[],
        output_path=project_dir / "04_storyboard" / "S01_SH01.png",
        provider_name="mock",
    )
    logs = list((project_dir / "99_logs" / "S01_SH01").glob("*_image_attempt01.json"))
    assert len(logs) == 1


def test_generate_image_marks_stage_failed_after_exhausted_retries(tmp_path: Path):
    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)
    from ai_film.errors import ProviderError

    with pytest.raises(ProviderError):
        generate_image(
            project_dir=project_dir,
            shot_path=shot_path,
            provider=MockImageProvider(fail_first_n_submits=10),
            prompt="a girl in a corridor",
            model="nano-banana",
            reference_paths=[],
            output_path=project_dir / "04_storyboard" / "S01_SH01.png",
            provider_name="mock",
            max_attempts=2,
        )
    shot = load_shot(shot_path)
    assert shot["generation"]["image"]["status"] == "failed"
    assert shot["status"] == "failed"
