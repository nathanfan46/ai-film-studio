# tests/services/test_candidate_service.py
from pathlib import Path

import pytest

from ai_film.approval import approve_generation
from ai_film.candidate_store import load_candidate_set
from ai_film.errors import CostGateError, ProviderError
from ai_film.providers.mock.image import MockImageProvider
from ai_film.services.candidate_service import generate_candidates


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
