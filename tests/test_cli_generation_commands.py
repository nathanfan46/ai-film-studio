import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.shot_store import load_shot, save_shot
from ai_film.models import Capability, GenerationJob, ImageGenerationResult, JobStatus, VideoGenerationResult

runner = CliRunner()


class _RecordingImageProvider:
    """Captures every ImageGenerationRequest.submit() call so tests can
    assert on the reference_paths the cli layer built, without touching
    the real mock provider (which ignores reference_paths entirely)."""

    def __init__(self):
        self.requests = []

    def submit(self, request):
        self.requests.append(request)
        return GenerationJob(provider="mock", id=f"job{len(self.requests)}", capability=Capability.IMAGE)

    def poll(self, job):
        return JobStatus.COMPLETED

    def get_result(self, job):
        request = self.requests[-1]
        return ImageGenerationResult(artifact_path=request.output_path, size_bytes=1)


class _RecordingCandidatesProvider:
    """Same idea as _RecordingImageProvider, for generate-candidates
    (which calls get_results, plural, and needs a real file on disk for
    each result since candidate_service.py renames it)."""

    def __init__(self):
        self.requests = []

    def submit(self, request):
        self.requests.append(request)
        return GenerationJob(provider="mock", id=f"job{len(self.requests)}", capability=Capability.IMAGE)

    def poll(self, job):
        return JobStatus.COMPLETED

    def get_results(self, job, output_dir):
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        result_path = out_dir / "raw_result.png"
        result_path.write_bytes(b"fake")
        return [ImageGenerationResult(artifact_path=str(result_path), size_bytes=4)]


def _shot(shot_id: str) -> dict:
    return {
        "schema_version": "1.0",
        "id": shot_id,
        "status": "draft",
        "duration_seconds": 2,
        "continuity": {"status": "pending", "checked_at": None, "issues": []},
        "action": "girl steps out of darkness",
        "visual": {"style": "cinematic sci-fi"},
        "camera": {"shot": "close_up", "movement": "slow_push_in"},
        "characters": [],
        "generation": {
            "image": {"status": "pending", "attempts": 0},
            "video": {"status": "pending", "attempts": 0},
            "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }


def _init_mock_project(tmp_path: Path) -> Path:
    project_dir = tmp_path / "project"
    runner.invoke(app, ["init", "Test Film", "--path", str(project_dir)])
    config = json.loads((project_dir / "config.json").read_text())
    for stage in config["providers"]:
        config["providers"][stage]["provider"] = "mock"
    (project_dir / "config.json").write_text(json.dumps(config))
    save_shot(project_dir / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    return project_dir


def test_generate_image_blocked_without_approval(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    result = runner.invoke(
        app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir)]
    )
    assert result.exit_code == 1


def _approve(project_dir: Path, shot_id: str = "S01_SH01") -> None:
    runner.invoke(
        app,
        ["approve-generation", "--scope", "storyboard", "--targets", shot_id, "--path", str(project_dir)],
    )


def test_generate_image_succeeds_after_approval(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    _approve(project_dir)
    result = runner.invoke(
        app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir)]
    )
    assert result.exit_code == 0
    assert "completed" in result.output
    assert (project_dir / "04_storyboard" / "S01_SH01.png").exists()


def test_generate_image_rejects_unknown_provider_name(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    config = json.loads((project_dir / "config.json").read_text())
    config["providers"]["image"]["provider"] = "not-a-real-provider"
    (project_dir / "config.json").write_text(json.dumps(config))
    _approve(project_dir)

    result = runner.invoke(
        app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir)]
    )

    assert result.exit_code == 1
    assert result.output.strip() != ""
    assert "not-a-real-provider" in result.output
    # No uncaught exception should propagate out of the command — resolve_provider's
    # ValueError must be caught and turned into a clean typer.Exit(1), not a traceback.
    assert not isinstance(result.exception, ValueError)


def test_generate_video_succeeds_after_approval(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    _approve(project_dir)
    result = runner.invoke(
        app, ["generate-video", "--shot", "S01_SH01", "--path", str(project_dir)]
    )
    assert result.exit_code == 0
    assert "completed" in result.output
    assert (project_dir / "05_video" / "S01_SH01.mp4").exists()


def test_generate_voice_succeeds_after_approval(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    _approve(project_dir)
    result = runner.invoke(
        app, ["generate-voice", "--shot", "S01_SH01", "--path", str(project_dir)]
    )
    assert result.exit_code == 0
    assert "completed" in result.output
    assert (project_dir / "06_audio" / "dialogue" / "S01_SH01.wav").exists()


def test_generate_sfx_succeeds_after_approval(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    _approve(project_dir)
    result = runner.invoke(
        app,
        [
            "generate-sfx", "--shot", "S01_SH01", "--prompt", "distant thunder rumble",
            "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 0
    assert "completed" in result.output
    assert (project_dir / "06_audio" / "sfx" / "S01_SH01.wav").exists()


def test_generate_music_succeeds_after_approval(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    _approve(project_dir)
    result = runner.invoke(
        app,
        [
            "generate-music", "--shot", "S01_SH01", "--prompt", "tense low strings",
            "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 0
    assert "completed" in result.output
    assert (project_dir / "06_audio" / "music" / "S01_SH01.wav").exists()


def test_check_continuity_updates_shot(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    result = runner.invoke(
        app,
        [
            "check-continuity", "--shot", "S01_SH01", "--status", "passed",
            "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 0
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    assert shot["continuity"]["status"] == "passed"


def test_check_continuity_records_issues(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    runner.invoke(
        app,
        [
            "check-continuity", "--shot", "S01_SH01", "--status", "failed",
            "--issue", "jacket color mismatch", "--path", str(project_dir),
        ],
    )
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    assert shot["continuity"]["issues"] == ["jacket color mismatch"]


def test_approve_generation_rejects_unknown_scope(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    result = runner.invoke(
        app,
        ["approve-generation", "--scope", "bogus", "--targets", "S01_SH01", "--path", str(project_dir)],
    )
    assert result.exit_code == 1


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_render_reports_preflight_failure_when_no_shots_generated(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    result = runner.invoke(app, ["render", "--path", str(project_dir)])
    assert result.exit_code == 1
    assert "preflight" in result.output.lower()


def test_models_rejects_unknown_capability():
    """Capability('bogus') raises a raw ValueError inside models_cmd; it must be
    caught and turned into a clean exit 1, not an uncaught traceback."""
    result = runner.invoke(app, ["models", "--capability", "bogus"])
    assert result.exit_code == 1
    assert result.output.strip() != ""
    assert not isinstance(result.exception, ValueError)


def test_check_continuity_rejects_unknown_status(tmp_path: Path):
    """An invalid --status value fails save_shot's schema validation with a raw
    ValueError deep inside check_continuity_cmd; it must be caught cleanly."""
    project_dir = _init_mock_project(tmp_path)
    result = runner.invoke(
        app,
        ["check-continuity", "--shot", "S01_SH01", "--status", "bogus", "--path", str(project_dir)],
    )
    assert result.exit_code == 1
    assert result.output.strip() != ""
    assert not isinstance(result.exception, ValueError)


def test_render_reports_clean_error_when_ffmpeg_missing(tmp_path: Path, monkeypatch):
    """RuntimeError from render()'s shutil.which(...) check (ffmpeg not on PATH)
    must be caught by render_cmd, not propagate as an uncaught exception."""
    project_dir = _init_mock_project(tmp_path)
    _approve(project_dir)
    result = runner.invoke(
        app, ["generate-video", "--shot", "S01_SH01", "--path", str(project_dir)]
    )
    assert result.exit_code == 0, result.output

    monkeypatch.setattr("ai_film.render.shutil.which", lambda name: None)
    result = runner.invoke(app, ["render", "--path", str(project_dir)])
    assert result.exit_code == 1
    assert result.output.strip() != ""
    assert not isinstance(result.exception, RuntimeError)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_render_reports_clean_error_when_ffmpeg_fails(tmp_path: Path):
    """The mock video provider writes placeholder (non-video) bytes, which pass
    preflight (file exists, non-empty) but make ffmpeg's concat/copy fail with a
    CalledProcessError; render_cmd must catch that cleanly, not crash."""
    project_dir = _init_mock_project(tmp_path)
    _approve(project_dir)
    result = runner.invoke(
        app, ["generate-video", "--shot", "S01_SH01", "--path", str(project_dir)]
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["render", "--path", str(project_dir)])
    assert result.exit_code == 1
    assert result.output.strip() != ""
    assert not isinstance(result.exception, subprocess.CalledProcessError)


def test_generate_image_includes_environment_reference_first(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["environment"] = {
        "name": "hospital_corridor",
        "reference": "assets/environments/hospital_corridor/reference.png",
    }
    shot["characters"] = [{"name": "Mara", "reference": "assets/characters/Mara/reference.png"}]
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)
    _approve(project_dir)

    provider = _RecordingImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == [
        str(project_dir / "assets/environments/hospital_corridor/reference.png"),
        str(project_dir / "assets/characters/Mara/reference.png"),
    ]


def test_generate_image_scenes_first_shot_has_no_predecessor_note(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    _approve(project_dir)

    provider = _RecordingImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == []
    assert "predecessor" not in result.output


def test_generate_image_notes_when_predecessor_not_locked_yet(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)  # writes S01_SH01, no locked image
    save_shot(project_dir / "03_shots" / "S01_SH02.json", _shot("S01_SH02"))
    _approve(project_dir, "S01_SH02")

    provider = _RecordingImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH02", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == []
    assert "S01_SH02's predecessor in this scene has no locked image yet" in result.output


def test_generate_image_chains_locked_predecessor_last(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    shot1 = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot1["environment"] = {
        "name": "hospital_corridor",
        "reference": "assets/environments/hospital_corridor/reference.png",
    }
    shot1["generation"]["image"] = {
        "status": "completed",
        "attempts": 1,
        "artifact": {"path": "04_storyboard/S01_SH01.png", "size_bytes": 10, "sha256": None},
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot1)
    shot2 = _shot("S01_SH02")
    shot2["environment"] = shot1["environment"]
    save_shot(project_dir / "03_shots" / "S01_SH02.json", shot2)
    _approve(project_dir, "S01_SH02")

    provider = _RecordingImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH02", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == [
        str(project_dir / "assets/environments/hospital_corridor/reference.png"),
        str(project_dir / "04_storyboard" / "S01_SH01.png"),
    ]


def test_generate_image_includes_master_reference_between_characters_and_previous(
    tmp_path: Path, monkeypatch
):
    from ai_film.scene_continuity import lock_continuity_master, set_scene_continuity

    project_dir = _init_mock_project(tmp_path)
    shot1 = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot1["generation"]["image"] = {
        "status": "completed", "attempts": 1,
        "artifact": {"path": "04_storyboard/S01_SH01.png", "size_bytes": 4, "sha256": None},
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot1)
    (project_dir / "04_storyboard").mkdir(parents=True, exist_ok=True)
    (project_dir / "04_storyboard" / "S01_SH01.png").write_bytes(b"MASTER-IMAGE")
    set_scene_continuity(project_dir, "S01", "A", "left", "right", master_shot="S01_SH01")
    lock_continuity_master(project_dir, "S01")

    shot2 = _shot("S01_SH02")
    shot2["characters"] = [{"name": "A", "reference": "assets/characters/A/reference.png"}]
    save_shot(project_dir / "03_shots" / "S01_SH02.json", shot2)

    shot3 = _shot("S01_SH03")
    shot3["environment"] = {
        "name": "hospital_corridor",
        "reference": "assets/environments/hospital_corridor/reference.png",
    }
    shot3["characters"] = [{"name": "A", "reference": "assets/characters/A/reference.png"}]
    save_shot(project_dir / "03_shots" / "S01_SH03.json", shot3)
    _approve(project_dir, "S01_SH03")

    provider = _RecordingImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH03", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    # S01_SH03 has no locked predecessor (S01_SH02 was never generated), so the
    # previous-shot reference is absent — but the full ordering of what IS
    # present must still hold: environment reference, then character
    # reference(s), then the scene's fixed master reference last.
    assert provider.requests[-1].reference_paths == [
        str(project_dir / "assets/environments/hospital_corridor/reference.png"),
        str(project_dir / "assets/characters/A/reference.png"),
        str(project_dir / "02_scenes" / "S01_master_reference.png"),
    ]


def test_generate_image_attaches_master_and_previous_shot_as_distinct_references(
    tmp_path: Path, monkeypatch
):
    """S01_SH02's previous shot (S01_SH01) IS the scene's master shot, but
    the master reference is a frozen COPY (02_scenes/S01_master_reference.png)
    while the previous-shot reference is S01_SH01's own live artifact
    (04_storyboard/S01_SH01.png) — two different paths by construction, so
    both attach. This is the expected, common case: master and previous
    are independent anchors and neither replaces the other, even when
    they happen to originate from the same shot."""
    from ai_film.scene_continuity import lock_continuity_master, set_scene_continuity

    project_dir = _init_mock_project(tmp_path)
    shot1 = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot1["generation"]["image"] = {
        "status": "completed", "attempts": 1,
        "artifact": {"path": "04_storyboard/S01_SH01.png", "size_bytes": 4, "sha256": None},
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot1)
    (project_dir / "04_storyboard").mkdir(parents=True, exist_ok=True)
    (project_dir / "04_storyboard" / "S01_SH01.png").write_bytes(b"MASTER-IMAGE")
    set_scene_continuity(project_dir, "S01", "A", "left", "right", master_shot="S01_SH01")
    lock_continuity_master(project_dir, "S01")

    save_shot(project_dir / "03_shots" / "S01_SH02.json", _shot("S01_SH02"))
    _approve(project_dir, "S01_SH02")

    provider = _RecordingImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH02", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == [
        str(project_dir / "02_scenes" / "S01_master_reference.png"),
        str(project_dir / "04_storyboard" / "S01_SH01.png"),
    ]


def test_generate_image_dedup_guard_fires_on_literal_path_equality(tmp_path: Path, monkeypatch):
    """The frozen-snapshot design means master_reference_image
    (02_scenes/...) and a previous-shot reference (04_storyboard/...) can
    never naturally collide — but _image_references' de-dup guard exists
    to honor the "never send the same image twice" invariant regardless,
    and must actually work if the two ever do resolve to the same path
    (e.g. a future change, or a hand-edited continuity file). Force the
    collision directly against the continuity file to prove the guard
    branch itself is correct, since the normal CLI flow can't reach it."""
    from ai_film.scene_continuity import continuity_path

    project_dir = _init_mock_project(tmp_path)
    shot1 = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot1["generation"]["image"] = {
        "status": "completed", "attempts": 1,
        "artifact": {"path": "04_storyboard/S01_SH01.png", "size_bytes": 4, "sha256": None},
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot1)
    (project_dir / "04_storyboard").mkdir(parents=True, exist_ok=True)
    (project_dir / "04_storyboard" / "S01_SH01.png").write_bytes(b"IMAGE")
    save_shot(project_dir / "03_shots" / "S01_SH02.json", _shot("S01_SH02"))
    _approve(project_dir, "S01_SH02")

    continuity_path(project_dir, "S01").parent.mkdir(parents=True, exist_ok=True)
    continuity_path(project_dir, "S01").write_text(json.dumps({
        "scene_id": "S01", "master_shot": "S01_SH01",
        # Deliberately points at the SAME path previous_shot_image_reference
        # will resolve for S01_SH02, to force the collision.
        "master_reference_image": "04_storyboard/S01_SH01.png",
        "spatial": {}, "transitions": [],
    }))

    provider = _RecordingImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH02", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == [
        str(project_dir / "04_storyboard" / "S01_SH01.png"),
    ]


def test_generate_image_never_self_references_the_master_shot(tmp_path: Path, monkeypatch):
    """Regression coverage for the guard's truthy-master_ref branch: unlike
    an unlocked scene (where master_reference_image is absent and the `and`
    short-circuits before the self-shot comparison ever runs), this locks a
    real master reference first, so the assertion below can only pass if
    `continuity.get("master_shot") != shot_id` genuinely evaluates and
    blocks — not because there was never anything to block."""
    from ai_film.scene_continuity import lock_continuity_master, set_scene_continuity

    project_dir = _init_mock_project(tmp_path)
    shot1 = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot1["generation"]["image"] = {
        "status": "completed", "attempts": 1,
        "artifact": {"path": "04_storyboard/S01_SH01.png", "size_bytes": 4, "sha256": None},
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot1)
    (project_dir / "04_storyboard").mkdir(parents=True, exist_ok=True)
    (project_dir / "04_storyboard" / "S01_SH01.png").write_bytes(b"MASTER-IMAGE")
    set_scene_continuity(project_dir, "S01", "A", "left", "right", master_shot="S01_SH01")
    lock_continuity_master(project_dir, "S01")
    _approve(project_dir)

    provider = _RecordingImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    # S01_SH01 is the scene's master shot, and master_reference_image is now
    # genuinely locked (truthy) — regenerating S01_SH01 itself (--force, since
    # it's already "completed") must still never reference its own snapshot.
    result = runner.invoke(
        app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir), "--force"]
    )
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == []


def test_generate_image_passes_effective_spatial_state_into_prompt(tmp_path: Path, monkeypatch):
    from ai_film.scene_continuity import set_scene_continuity

    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["characters"] = [{"name": "Mara Voss", "reference": "assets/characters/Mara Voss/reference.png"}]
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)
    set_scene_continuity(project_dir, "S01", "Mara Voss", "left", "right")
    _approve(project_dir)

    provider = _RecordingImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert "Mara Voss is screen-left, facing right" in provider.requests[-1].prompt


def test_generate_image_filters_spatial_state_to_shots_own_characters(tmp_path: Path, monkeypatch):
    """Finding 3: a shot only lists the characters actually on screen, so the
    spatial-canon prompt fragment must not force off-screen characters (whom
    the scene has a recorded position for but this shot doesn't show) into
    the prompt — that would actively instruct the image model to draw them
    into a shot that shouldn't contain them."""
    from ai_film.scene_continuity import set_scene_continuity

    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["characters"] = [{"name": "Mara Voss", "reference": "assets/characters/Mara Voss/reference.png"}]
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)
    set_scene_continuity(project_dir, "S01", "Mara Voss", "left", "right")
    set_scene_continuity(project_dir, "S01", "Doctor", "right", "left")
    _approve(project_dir)

    provider = _RecordingImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-image", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    prompt = provider.requests[-1].prompt
    assert "Mara Voss is screen-left, facing right" in prompt
    assert "Doctor" not in prompt


def test_generate_candidates_shot_target_includes_environment_reference(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["environment"] = {
        "name": "hospital_corridor",
        "reference": "assets/environments/hospital_corridor/reference.png",
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)
    runner.invoke(
        app,
        [
            "approve-generation", "--scope", "storyboard", "--targets", "shot:S01_SH01:image",
            "--path", str(project_dir),
        ],
    )

    provider = _RecordingCandidatesProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(
        app,
        ["generate-candidates", "--target", "shot:S01_SH01:image", "--count", "1", "--path", str(project_dir)],
    )
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == [
        str(project_dir / "assets/environments/hospital_corridor/reference.png")
    ]


def test_generate_all_image_includes_environment_reference(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["environment"] = {
        "name": "hospital_corridor",
        "reference": "assets/environments/hospital_corridor/reference.png",
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)
    _approve(project_dir)

    provider = _RecordingImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-all", "--stage", "image", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == [
        str(project_dir / "assets/environments/hospital_corridor/reference.png")
    ]


def test_generate_all_image_suppresses_missing_predecessor_note(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)  # writes S01_SH01, no locked image
    save_shot(project_dir / "03_shots" / "S01_SH02.json", _shot("S01_SH02"))
    # approve-generation --scope storyboard replaces any prior approval wholesale
    # (not additively — see ai-film-media.md's "Note on approval scope"), so both
    # shot ids must be approved in a single call, not two separate _approve() calls.
    _approve(project_dir, "S01_SH01,S01_SH02")

    provider = _RecordingImageProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-all", "--stage", "image", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert "predecessor" not in result.output


class _RecordingVideoProvider:
    def __init__(self):
        self.requests = []

    def submit(self, request):
        self.requests.append(request)
        return GenerationJob(provider="mock", id=f"job{len(self.requests)}", capability=Capability.VIDEO)

    def poll(self, job):
        return JobStatus.COMPLETED

    def get_result(self, job):
        request = self.requests[-1]
        return VideoGenerationResult(artifact_path=request.output_path, size_bytes=1, duration_seconds=2.0)


def test_generate_video_uses_locked_image_over_raw_references(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["environment"] = {
        "name": "hospital_corridor",
        "reference": "assets/environments/hospital_corridor/reference.png",
    }
    shot["characters"] = [{"name": "Mara", "reference": "assets/characters/Mara/reference.png"}]
    shot["generation"]["image"] = {
        "status": "completed",
        "attempts": 1,
        "artifact": {"path": "04_storyboard/S01_SH01.png", "size_bytes": 10, "sha256": None},
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)
    _approve(project_dir)

    provider = _RecordingVideoProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-video", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == [str(project_dir / "04_storyboard" / "S01_SH01.png")]


def test_generate_video_falls_back_to_raw_references_without_locked_image(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["environment"] = {
        "name": "hospital_corridor",
        "reference": "assets/environments/hospital_corridor/reference.png",
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)
    _approve(project_dir)

    provider = _RecordingVideoProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-video", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == [
        str(project_dir / "assets/environments/hospital_corridor/reference.png")
    ]


def test_generate_all_video_uses_locked_image(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["generation"]["image"] = {
        "status": "completed",
        "attempts": 1,
        "artifact": {"path": "04_storyboard/S01_SH01.png", "size_bytes": 10, "sha256": None},
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)
    _approve(project_dir)

    provider = _RecordingVideoProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-all", "--stage", "video", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].reference_paths == [str(project_dir / "04_storyboard" / "S01_SH01.png")]


def test_generate_video_uses_voice_duration_for_a_dialogue_shot_with_completed_voice(
    tmp_path: Path, monkeypatch
):
    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["dialogue"] = {"text": "hello there", "speaker": "girl"}
    shot["generation"]["voice"] = {
        "status": "completed", "attempts": 1,
        "artifact": {
            "path": "06_audio/dialogue/S01_SH01.wav", "size_bytes": 5, "sha256": None,
            "duration_seconds": 3.5,
        },
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)  # shot's own duration_seconds stays 2
    _approve(project_dir)

    provider = _RecordingVideoProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-video", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].duration_seconds == 3.5  # voice's real length, not the shot's static 2


def test_generate_video_uses_static_duration_without_dialogue(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    _approve(project_dir)

    provider = _RecordingVideoProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-video", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].duration_seconds == 2  # the shot's own static duration_seconds


def test_generate_video_uses_static_duration_when_voice_not_yet_completed(tmp_path: Path, monkeypatch):
    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["dialogue"] = {"text": "hello there", "speaker": "girl"}
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)  # voice stays not_required/pending
    _approve(project_dir)

    provider = _RecordingVideoProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-video", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.requests[-1].duration_seconds == 2


def test_generate_lipsync_requires_video_generated_first(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["generation"]["voice"] = {
        "status": "completed", "attempts": 1,
        "artifact": {
            "path": "06_audio/dialogue/S01_SH01.wav", "size_bytes": 5, "sha256": None,
            "duration_seconds": 3.5,
        },
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)

    result = runner.invoke(app, ["generate-lipsync", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 1
    assert "video must be generated" in result.output


def test_generate_lipsync_requires_voice_generated_first(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["generation"]["video"] = {
        "status": "completed", "attempts": 1, "version": 1, "history": [],
        "artifact": {
            "path": "05_video/S01_SH01.mp4", "size_bytes": 10, "sha256": None,
            "duration_seconds": 5.0,
        },
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)

    result = runner.invoke(app, ["generate-lipsync", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 1
    assert "voice must be generated" in result.output


def test_generate_lipsync_supersedes_the_video_artifact(tmp_path: Path):
    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["dialogue"] = {"text": "hello there", "speaker": "girl"}
    shot["generation"]["video"] = {
        "status": "completed", "attempts": 1, "version": 1, "history": [],
        "artifact": {
            "path": "05_video/S01_SH01.mp4", "size_bytes": 10, "sha256": None,
            "duration_seconds": 3.5,
        },
    }
    shot["generation"]["voice"] = {
        "status": "completed", "attempts": 1,
        "artifact": {
            "path": "06_audio/dialogue/S01_SH01.wav", "size_bytes": 5, "sha256": None,
            "duration_seconds": 3.5,
        },
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)
    _approve(project_dir)
    # generate_lipsync copies these before run_generation_stage archives the
    # current video artifact — the source files must genuinely exist on disk.
    (project_dir / "05_video" / "S01_SH01.mp4").parent.mkdir(parents=True, exist_ok=True)
    (project_dir / "05_video" / "S01_SH01.mp4").write_bytes(b"ORIGINAL-MP4-DATA")
    (project_dir / "06_audio" / "dialogue" / "S01_SH01.wav").parent.mkdir(parents=True, exist_ok=True)
    (project_dir / "06_audio" / "dialogue" / "S01_SH01.wav").write_bytes(b"ORIGINAL-WAV-DATA")

    result = runner.invoke(app, ["generate-lipsync", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output

    updated = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    video = updated["generation"]["video"]
    assert video["status"] == "completed"
    assert video["version"] == 2
    assert len(video["history"]) == 1
    assert video["history"][0]["superseded_reason"] == "lipsync"
    assert video["artifact"]["lipsynced"] is True
    # The superseded original was archived into history, not deleted.
    assert (project_dir / "05_video" / "history" / "S01_SH01_v1.mp4").read_bytes() == b"ORIGINAL-MP4-DATA"


class _FileReadingLipsyncProvider:
    """Actually reads bytes from request.video_path/audio_path at submit()
    time, mirroring what the real FalLipsyncProvider does via
    client.upload_file -> file_path.read_bytes(). This is what catches the
    archive-before-upload race directly: run_generation_stage archives
    (moves) the shot's current video artifact before calling submit_fn, so
    if generate_lipsync ever again passes through the *original* canonical
    path instead of a pre-archive copy, this raises FileNotFoundError here
    exactly like the real bug did against the live fal API."""

    def __init__(self):
        self.read_video_bytes = None
        self.read_audio_bytes = None
        self._output_path = None

    def submit(self, request):
        self.read_video_bytes = Path(request.video_path).read_bytes()
        self.read_audio_bytes = Path(request.audio_path).read_bytes()
        self._output_path = request.output_path
        return GenerationJob(provider="mock", id="job1", capability=Capability.LIPSYNC)

    def poll(self, job):
        return JobStatus.COMPLETED

    def get_result(self, job):
        output_path = Path(self._output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"SYNCED-MP4-DATA")
        return VideoGenerationResult(
            artifact_path=str(output_path), size_bytes=len(b"SYNCED-MP4-DATA"), duration_seconds=3.5,
        )


def test_generate_lipsync_reads_input_files_before_the_video_artifact_is_archived(
    tmp_path: Path, monkeypatch
):
    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["dialogue"] = {"text": "hello there", "speaker": "girl"}
    shot["generation"]["video"] = {
        "status": "completed", "attempts": 1, "version": 1, "history": [],
        "artifact": {
            "path": "05_video/S01_SH01.mp4", "size_bytes": 10, "sha256": None,
            "duration_seconds": 3.5,
        },
    }
    shot["generation"]["voice"] = {
        "status": "completed", "attempts": 1,
        "artifact": {
            "path": "06_audio/dialogue/S01_SH01.wav", "size_bytes": 5, "sha256": None,
            "duration_seconds": 3.5,
        },
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)
    _approve(project_dir)
    (project_dir / "05_video" / "S01_SH01.mp4").parent.mkdir(parents=True, exist_ok=True)
    (project_dir / "05_video" / "S01_SH01.mp4").write_bytes(b"ORIGINAL-MP4-DATA")
    (project_dir / "06_audio" / "dialogue" / "S01_SH01.wav").parent.mkdir(parents=True, exist_ok=True)
    (project_dir / "06_audio" / "dialogue" / "S01_SH01.wav").write_bytes(b"ORIGINAL-WAV-DATA")

    provider = _FileReadingLipsyncProvider()
    monkeypatch.setattr("ai_film.cli.resolve_provider", lambda capability, name: provider)

    result = runner.invoke(app, ["generate-lipsync", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert provider.read_video_bytes == b"ORIGINAL-MP4-DATA"
    assert provider.read_audio_bytes == b"ORIGINAL-WAV-DATA"


def test_generate_video_regeneration_clears_the_lipsynced_tag(tmp_path: Path):
    """A shot whose video was already lip-synced, then regenerated (e.g. a
    feedback-driven `generate-video --force` on `visual`/`camera`/`action`),
    must not carry the stale `lipsynced` tag forward onto the new, unsynced
    artifact — there is nothing enforcing this in the schema, only that
    generate-video always writes a fresh artifact dict via
    _video_or_audio_artifact, which never sets the key at all."""
    project_dir = _init_mock_project(tmp_path)
    shot = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    shot["generation"]["video"] = {
        "status": "completed", "attempts": 1, "version": 2, "history": [],
        "artifact": {
            "path": "05_video/S01_SH01.mp4", "size_bytes": 10, "sha256": None,
            "duration_seconds": 2.0, "lipsynced": True,
        },
    }
    save_shot(project_dir / "03_shots" / "S01_SH01.json", shot)
    _approve(project_dir)

    result = runner.invoke(
        app, ["generate-video", "--shot", "S01_SH01", "--path", str(project_dir), "--force"]
    )
    assert result.exit_code == 0, result.output

    updated = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    assert "lipsynced" not in updated["generation"]["video"]["artifact"]
