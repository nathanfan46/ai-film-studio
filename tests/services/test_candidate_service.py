# tests/services/test_candidate_service.py
from pathlib import Path

import pytest

from ai_film.approval import approve_generation
from ai_film.candidate_store import add_candidates, load_candidate_set
from ai_film.errors import CostGateError, ProviderError
from ai_film.providers.mock.image import MockImageProvider
from ai_film.services.candidate_service import generate_candidates, select_candidate
from ai_film.shot_store import load_shot, save_shot


def _init_config(project_dir: Path) -> None:
    (project_dir / "config.json").write_text('{"providers": {}, "generation": {}}')


def test_generate_candidates_blocked_without_approval(tmp_path: Path):
    _init_config(tmp_path)
    provider = MockImageProvider()
    with pytest.raises(CostGateError):
        generate_candidates(
            project_dir=tmp_path, target="character:girl", provider=provider,
            prompt="a girl, sci-fi style", model="nano-banana", count=4,
            provider_name="mock",
        )
    assert provider._submit_calls == 0


def test_generate_candidates_writes_n_candidates_once_approved(tmp_path: Path):
    _init_config(tmp_path)
    approve_generation(tmp_path, "bibles", ["character:girl"], estimated_cost=0.32)
    provider = MockImageProvider()

    result = generate_candidates(
        project_dir=tmp_path, target="character:girl", provider=provider,
        prompt="a girl, sci-fi style", model="nano-banana", count=4,
        provider_name="mock",
    )

    assert result["target"] == "character:girl"
    assert result["added"] == ["001", "002", "003", "004"]
    candidate_set = load_candidate_set(tmp_path, "character:girl")
    assert len(candidate_set["candidates"]) == 4
    for candidate in candidate_set["candidates"]:
        assert candidate["operation"] == "generate"
        assert candidate["parent"] is None
        assert candidate["prompt"] == "a girl, sci-fi style"
        assert candidate["provider"] == "mock"
        assert candidate["model"] == "nano-banana"
        artifact_path = tmp_path / "assets" / "characters" / "girl" / candidate["path"]
        assert artifact_path.exists()


def test_generate_candidates_from_one_call_share_job_id_and_cost(tmp_path: Path):
    _init_config(tmp_path)
    approve_generation(tmp_path, "bibles", ["character:girl"], estimated_cost=0.32)
    provider = MockImageProvider()

    generate_candidates(
        project_dir=tmp_path, target="character:girl", provider=provider,
        prompt="a girl", model="nano-banana", count=3, provider_name="mock",
    )

    candidate_set = load_candidate_set(tmp_path, "character:girl")
    job_ids = {c["job"]["id"] for c in candidate_set["candidates"]}
    assert len(job_ids) == 1, "all candidates from one call must share the same job id"
    costs = {c["estimated_cost"] for c in candidate_set["candidates"]}
    assert len(costs) == 1, "all candidates from one call must share the same estimated_cost"


def test_generate_candidates_second_call_continues_id_sequence(tmp_path: Path):
    _init_config(tmp_path)
    approve_generation(tmp_path, "bibles", ["character:girl"], estimated_cost=0.32)
    provider = MockImageProvider()

    generate_candidates(
        project_dir=tmp_path, target="character:girl", provider=provider,
        prompt="a girl", model="nano-banana", count=2, provider_name="mock",
    )
    result = generate_candidates(
        project_dir=tmp_path, target="character:girl", provider=provider,
        prompt="a girl, take two", model="nano-banana", count=2, provider_name="mock",
    )

    assert result["added"] == ["003", "004"]


def test_generate_candidates_raises_provider_error_after_exhausted_retries(tmp_path: Path):
    _init_config(tmp_path)
    approve_generation(tmp_path, "bibles", ["character:girl"], estimated_cost=0.32)
    provider = MockImageProvider(fail_first_n_submits=10)

    with pytest.raises(ProviderError):
        generate_candidates(
            project_dir=tmp_path, target="character:girl", provider=provider,
            prompt="a girl", model="nano-banana", count=2, provider_name="mock",
            max_attempts=2,
        )


def test_generate_candidates_writes_attempt_log(tmp_path: Path):
    _init_config(tmp_path)
    approve_generation(tmp_path, "bibles", ["character:girl"], estimated_cost=0.32)
    provider = MockImageProvider()

    generate_candidates(
        project_dir=tmp_path, target="character:girl", provider=provider,
        prompt="a girl", model="nano-banana", count=2, provider_name="mock",
    )

    logs = list((tmp_path / "99_logs" / "character_girl").glob("*_candidates_attempt01.json"))
    assert len(logs) == 1


def _seed_character_candidate(tmp_path: Path, target: str = "character:girl") -> None:
    directory = tmp_path / "assets" / "characters" / "girl" / "candidates"
    directory.mkdir(parents=True)
    (directory / "001.png").write_bytes(b"CANDIDATE-ONE")
    add_candidates(tmp_path, target, [{
        "id": "001", "path": "candidates/001.png", "provider": "mock", "model": "nano-banana",
        "prompt": "a girl", "parent": None, "operation": "generate", "job": None,
        "estimated_cost": None, "created_at": "2026-08-22T00:00:00Z",
    }])


def test_select_candidate_copies_to_reference_png_for_character_target(tmp_path: Path):
    _seed_character_candidate(tmp_path)

    result = select_candidate(tmp_path, "character:girl", "001")

    assert result == {
        "target": "character:girl", "selected": "001",
        "canonical_path": "assets/characters/girl/reference.png",
    }
    reference_path = tmp_path / "assets" / "characters" / "girl" / "reference.png"
    assert reference_path.read_bytes() == b"CANDIDATE-ONE"
    candidate_set = load_candidate_set(tmp_path, "character:girl")
    assert candidate_set["selected"] == "001"


def test_select_candidate_can_be_re_run_to_change_pick(tmp_path: Path):
    _seed_character_candidate(tmp_path)
    directory = tmp_path / "assets" / "characters" / "girl" / "candidates"
    (directory / "002.png").write_bytes(b"CANDIDATE-TWO")
    add_candidates(tmp_path, "character:girl", [{
        "id": "002", "path": "candidates/002.png", "provider": "mock", "model": "nano-banana",
        "prompt": "a girl v2", "parent": "001", "operation": "edit", "job": None,
        "estimated_cost": None, "created_at": "2026-08-22T00:05:00Z",
    }])

    select_candidate(tmp_path, "character:girl", "001")
    select_candidate(tmp_path, "character:girl", "002")

    reference_path = tmp_path / "assets" / "characters" / "girl" / "reference.png"
    assert reference_path.read_bytes() == b"CANDIDATE-TWO"
    assert load_candidate_set(tmp_path, "character:girl")["selected"] == "002"


def _seed_shot(tmp_path: Path) -> None:
    shot = {
        "schema_version": "1.0", "id": "S01_SH01", "status": "draft", "duration_seconds": 3,
        "continuity": {"status": "pending", "checked_at": None, "issues": []},
        "generation": {
            "image": {"status": "pending", "attempts": 0},
            "video": {"status": "pending", "attempts": 0},
            "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", shot)


def _seed_shot_candidate(tmp_path: Path) -> None:
    directory = tmp_path / "04_storyboard" / "candidates" / "S01_SH01" / "candidates"
    directory.mkdir(parents=True)
    (directory / "001.png").write_bytes(b"SHOT-CANDIDATE")
    add_candidates(tmp_path, "shot:S01_SH01:image", [{
        "id": "001", "path": "candidates/001.png", "provider": "mock", "model": "nano-banana",
        "prompt": "wide shot", "parent": None, "operation": "generate", "job": None,
        "estimated_cost": None, "created_at": "2026-08-22T00:00:00Z",
    }])


def test_select_candidate_writes_into_shot_json_for_shot_target(tmp_path: Path):
    _seed_shot(tmp_path)
    _seed_shot_candidate(tmp_path)

    result = select_candidate(tmp_path, "shot:S01_SH01:image", "001")

    assert result["canonical_path"] == "04_storyboard/S01_SH01.png"
    artifact_path = tmp_path / "04_storyboard" / "S01_SH01.png"
    assert artifact_path.read_bytes() == b"SHOT-CANDIDATE"
    shot = load_shot(tmp_path / "03_shots" / "S01_SH01.json")
    assert shot["generation"]["image"]["status"] == "completed"
    assert shot["generation"]["image"]["artifact"]["path"] == "04_storyboard/S01_SH01.png"
