# tests/test_media_review_golden_path.py
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.feedback_store import load_feedback
from ai_film.shot_store import load_shot

runner = CliRunner()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_full_media_review_loop(tmp_path: Path, monkeypatch):
    project_dir = tmp_path / "project"
    result = runner.invoke(app, ["init", "The Last Ship", "--path", str(project_dir)])
    assert result.exit_code == 0
    config_path = project_dir / "config.json"
    config = json.loads(config_path.read_text())
    for stage in config["providers"]:
        config["providers"][stage]["provider"] = "mock"
    config_path.write_text(json.dumps(config))

    shot = {
        "schema_version": "1.0", "id": "S01_SH01", "status": "draft", "duration_seconds": 6,
        "continuity": {"status": "passed", "checked_at": None, "issues": []},
        "action": "Mara answers the phone", "camera": {"shot": "close_up"}, "characters": [],
        "generation": {
            "image": {"status": "not_required"},
            "video": {"status": "pending", "attempts": 0},
            "voice": {"status": "pending", "attempts": 0},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }
    (project_dir / "03_shots" / "S01_SH01.json").write_text(json.dumps(shot))

    result = runner.invoke(
        app,
        ["approve-generation", "--scope", "storyboard", "--targets", "S01_SH01", "--path", str(project_dir)],
    )
    assert result.exit_code == 0

    result = runner.invoke(app, ["generate-video", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["generate-voice", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output

    # first review page: everything generated, nothing flagged yet
    monkeypatch.setattr("ai_film.cli.open_in_browser", lambda path: None)
    result = runner.invoke(app, ["review-media", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    review_html = project_dir / "07_review" / "S01_SH01.html"
    assert review_html.exists()
    assert "video not generated" not in review_html.read_text()

    # human notices a sync problem in the review page; agent records it
    result = runner.invoke(
        app,
        [
            "add-feedback", "--shot", "S01_SH01", "--target", "sync",
            "--note", "voice starts ~400ms early", "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 0
    feedback = load_feedback(project_dir, "S01_SH01")
    assert len(feedback["entries"]) == 1
    fb_id = feedback["entries"][0]["id"]

    # MockAudioProvider writes non-audio placeholder bytes; apply-audio-offset
    # shells out to real ffmpeg, so swap in a real tiny wav first — the same
    # "replace mock-provider output with real content before exercising real
    # tooling" pattern test_candidate_golden_path.py uses for PNGs.
    voice_path = project_dir / "06_audio" / "dialogue" / "S01_SH01.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "1", str(voice_path)],
        check=True, capture_output=True,
    )

    # cheap fix: nudge the voice track, no new provider spend
    result = runner.invoke(
        app,
        [
            "apply-audio-offset", "--shot", "S01_SH01", "--track", "voice",
            "--offset-ms", "400", "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    shot_after = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    assert shot_after["generation"]["voice"]["version"] == 2
    assert len(shot_after["generation"]["voice"]["history"]) == 1

    result = runner.invoke(
        app,
        [
            "resolve-feedback", "--shot", "S01_SH01", "--id", fb_id,
            "--resolution", "applied +400ms offset", "--path", str(project_dir),
        ],
    )
    assert result.exit_code == 0

    # rebuilt review page reflects the new voice version and the (still
    # listed) resolved feedback note
    result = runner.invoke(app, ["review-media", "--shot", "S01_SH01", "--path", str(project_dir)])
    assert result.exit_code == 0
    final_html = review_html.read_text()
    assert "voice starts ~400ms early" in final_html
    # explicitly the voice track's label (the video panel also renders its own
    # version badge, so a bare "v2" check would be ambiguous about which
    # track it's asserting on) — the golden path doesn't regenerate video,
    # so the video panel correctly shows no history disclosure here.
    assert "Voice · v2" in final_html
    assert "1 earlier version" in final_html

    # none of the core engine's existing behavior is affected: render still
    # works exactly as before, untouched by this whole layer
    # First, swap mock video with real MP4 (same pattern as test_golden_path)
    shot_final = load_shot(project_dir / "03_shots" / "S01_SH01.json")
    video_path = Path(shot_final["generation"]["video"]["artifact"]["path"])
    resolved_video_path = project_dir / video_path if not video_path.is_absolute() else video_path
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=32x32:d=6",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "6", str(resolved_video_path)],
        check=True, capture_output=True,
    )

    result = runner.invoke(app, ["render", "--path", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert (project_dir / "final" / "reel_001.mp4").exists()
