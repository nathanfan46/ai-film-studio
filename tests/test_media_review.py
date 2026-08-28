# tests/test_media_review.py
from pathlib import Path

import pytest

from ai_film.feedback_store import add_feedback_entry
from ai_film.media_review import build_media_review
from ai_film.shot_store import save_shot


def _shot(shot_id: str, video_completed: bool = False) -> dict:
    video = (
        {
            "status": "completed", "provider": "fal", "model": "veo-3", "attempts": 1,
            "version": 1, "history": [],
            "artifact": {
                "path": f"05_video/{shot_id}.mp4", "size_bytes": 10, "sha256": None,
                "duration_seconds": 6.0,
            },
        }
        if video_completed
        else {"status": "pending", "attempts": 0}
    )
    return {
        "schema_version": "1.0", "id": shot_id, "status": "draft", "duration_seconds": 6,
        "continuity": {"status": "passed", "checked_at": None, "issues": []},
        "generation": {
            "image": {"status": "not_required"}, "video": video,
            "voice": {"status": "not_required"}, "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }


def test_build_media_review_raises_for_missing_shot(tmp_path: Path):
    with pytest.raises(ValueError):
        build_media_review(tmp_path, "S01_SH01")


def test_build_media_review_shows_placeholder_when_nothing_generated(tmp_path: Path):
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    html_path = build_media_review(tmp_path, "S01_SH01")
    content = html_path.read_text()
    assert "video not generated for this shot" in content
    assert "not generated for this shot" in content  # at least one audio row too


def test_build_media_review_embeds_video_source_path(tmp_path: Path):
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01", video_completed=True))
    html_path = build_media_review(tmp_path, "S01_SH01")
    content = html_path.read_text()
    assert "../05_video/S01_SH01.mp4" in content


def test_build_media_review_plots_a_timeline_flag_per_timed_entry(tmp_path: Path):
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01", video_completed=True))
    add_feedback_entry(tmp_path, "S01_SH01", target="video", note="too calm", at=3.29)
    add_feedback_entry(tmp_path, "S01_SH01", target="sync", note="general pacing note")
    html_path = build_media_review(tmp_path, "S01_SH01")
    content = html_path.read_text()
    assert 'class="flag' in content
    assert "3.29s" in content
    assert "general pacing note" in content  # listed even without a plotted flag


def test_build_media_review_writes_to_07_review_directory(tmp_path: Path):
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01"))
    html_path = build_media_review(tmp_path, "S01_SH01")
    assert html_path == tmp_path / "07_review" / "S01_SH01.html"
