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
    provider = MockImageProvider()
    with pytest.raises(CostGateError):
        generate_image(
            project_dir=project_dir,
            shot_path=shot_path,
            provider=provider,
            prompt="a girl in a corridor",
            model="nano-banana",
            reference_paths=[],
            output_path=project_dir / "04_storyboard" / "S01_SH01.png",
            provider_name="mock",
        )
    assert provider._submit_calls == 0


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
    # artifact.path is stored project-relative (see generation_service._project_relative_path)
    assert stage["artifact"]["path"] == "04_storyboard/S01_SH01.png"
    assert (project_dir / stage["artifact"]["path"]).exists()

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


def test_generate_video_first_completion_has_version_one_and_no_history(tmp_path: Path):
    from ai_film.providers.mock.video import MockVideoProvider
    from ai_film.services.generation_service import generate_video

    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)

    stage = generate_video(
        project_dir=project_dir, shot_path=shot_path, provider=MockVideoProvider(),
        prompt="a corridor", model="veo-3", reference_paths=[], duration_seconds=5.0,
        output_path=project_dir / "05_video" / "S01_SH01.mp4", provider_name="mock",
    )
    assert stage["version"] == 1
    assert stage["history"] == []


def test_generate_video_force_regenerate_archives_old_artifact_and_bumps_version(tmp_path: Path):
    from ai_film.providers.mock.video import MockVideoProvider
    from ai_film.services.generation_service import generate_video

    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)

    kwargs = dict(
        project_dir=project_dir, shot_path=shot_path, prompt="a corridor",
        model="veo-3", reference_paths=[], duration_seconds=5.0,
        output_path=project_dir / "05_video" / "S01_SH01.mp4", provider_name="mock",
    )
    first = generate_video(provider=MockVideoProvider(), **kwargs)
    first_path = project_dir / first["artifact"]["path"]
    first_path.write_bytes(b"FIRST-VERSION-BYTES")  # distinguish from the mock's fixed bytes

    second = generate_video(provider=MockVideoProvider(), force=True, **kwargs)

    assert second["version"] == 2
    assert len(second["history"]) == 1
    archived = second["history"][0]
    assert archived["version"] == 1
    assert archived["superseded_reason"] == "regenerate"
    archived_path = project_dir / archived["artifact"]["path"]
    assert archived_path.exists()
    assert archived_path.read_bytes() == b"FIRST-VERSION-BYTES"
    new_path = project_dir / second["artifact"]["path"]
    assert new_path.read_bytes() != b"FIRST-VERSION-BYTES"


def test_generate_video_failed_regeneration_restores_prior_artifact(tmp_path: Path):
    from ai_film.errors import ProviderError
    from ai_film.providers.mock.video import MockVideoProvider
    from ai_film.services.generation_service import generate_video

    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)

    kwargs = dict(
        project_dir=project_dir, shot_path=shot_path, prompt="a corridor",
        model="veo-3", reference_paths=[], duration_seconds=5.0,
        output_path=project_dir / "05_video" / "S01_SH01.mp4", provider_name="mock",
    )
    first = generate_video(provider=MockVideoProvider(), **kwargs)
    original_path = project_dir / first["artifact"]["path"]
    original_bytes = original_path.read_bytes()

    with pytest.raises(ProviderError):
        generate_video(
            provider=MockVideoProvider(fail_first_n_submits=10), force=True,
            max_attempts=1, **kwargs,
        )

    # a failed regeneration attempt must not leave the shot's working
    # artifact archived away or missing
    assert original_path.exists()
    assert original_path.read_bytes() == original_bytes
    shot = load_shot(shot_path)
    assert shot["generation"]["video"]["status"] == "failed"


def test_generate_video_preserves_version_across_a_failed_then_successful_regeneration(tmp_path: Path):
    from ai_film.providers.mock.video import MockVideoProvider
    from ai_film.services.generation_service import generate_video
    from ai_film.errors import ProviderError

    project_dir = _project(tmp_path)
    shot_path = _shot_path(project_dir)
    approve_generation(project_dir, "storyboard", ["S01_SH01"], estimated_cost=0.1)

    kwargs = dict(
        project_dir=project_dir, shot_path=shot_path, prompt="a corridor",
        model="veo-3", reference_paths=[], duration_seconds=5.0,
        output_path=project_dir / "05_video" / "S01_SH01.mp4", provider_name="mock",
    )
    generate_video(provider=MockVideoProvider(), **kwargs)
    second = generate_video(provider=MockVideoProvider(), force=True, **kwargs)
    assert second["version"] == 2
    second_path = project_dir / second["artifact"]["path"]
    second_path.write_bytes(b"GOOD-V2-BYTES")

    with pytest.raises(ProviderError):
        generate_video(
            provider=MockVideoProvider(fail_first_n_submits=10), force=True,
            max_attempts=1, **kwargs,
        )

    third = generate_video(provider=MockVideoProvider(), force=True, **kwargs)

    assert third["version"] == 3, "version must not reset to 1 after a failed-then-retried regeneration"
    versions_in_history = [h["version"] for h in third["history"]]
    assert versions_in_history == [1, 2], "v2 must be archived, not silently overwritten"
    v2_history_entry = next(h for h in third["history"] if h["version"] == 2)
    v2_archived_path = project_dir / v2_history_entry["artifact"]["path"]
    assert v2_archived_path.read_bytes() == b"GOOD-V2-BYTES", "the v2 artifact must survive, not be destroyed"
