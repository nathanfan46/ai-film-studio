import shutil
import subprocess
from pathlib import Path

import pytest

from ai_film.render import RenderPreflightError, _escape_concat_path, build_manifest, preflight, render
from ai_film.shot_store import save_shot


def _shot(shot_id: str, video_path: str | None, video_status: str = "completed") -> dict:
    return {
        "schema_version": "1.0",
        "id": shot_id,
        "status": "draft",
        "duration_seconds": 2,
        "continuity": {"status": "passed", "checked_at": None, "issues": []},
        "generation": {
            "image": {"status": "not_required"},
            "video": {
                "status": video_status,
                "attempts": 1,
                "artifact": (
                    {"path": video_path, "size_bytes": 10, "sha256": None, "duration_seconds": 2.0}
                    if video_path else None
                ),
            },
            "voice": {"status": "not_required"},
            "sfx": {"status": "not_required"},
            "music": {"status": "not_required"},
        },
    }


def _make_tiny_mp4(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=32x32:d=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "1", str(path),
        ],
        check=True, capture_output=True,
    )


def test_build_manifest_orders_shots_and_includes_duration(tmp_path: Path):
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01", "05_video/S01_SH01.mp4"))
    save_shot(tmp_path / "03_shots" / "S01_SH02.json", _shot("S01_SH02", "05_video/S01_SH02.mp4"))
    manifest = build_manifest(tmp_path)
    assert [s["id"] for s in manifest["shots"]] == ["S01_SH01", "S01_SH02"]
    assert manifest["shots"][0]["duration"] == 2


def test_build_manifest_ignores_feedback_files(tmp_path: Path):
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01", "05_video/S01_SH01.mp4"))
    (tmp_path / "03_shots" / "S01_SH01.feedback.json").write_text('{"shot_id": "S01_SH01", "entries": []}')
    manifest = build_manifest(tmp_path)
    assert [s["id"] for s in manifest["shots"]] == ["S01_SH01"]


def test_preflight_reports_missing_artifact(tmp_path: Path):
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01", "05_video/missing.mp4"))
    manifest = build_manifest(tmp_path)
    errors = preflight(manifest, tmp_path)
    assert any("does not exist" in e for e in errors)


def test_preflight_reports_failed_generation_status(tmp_path: Path):
    video_path = tmp_path / "05_video" / "S01_SH01.mp4"
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"data")
    save_shot(
        tmp_path / "03_shots" / "S01_SH01.json",
        _shot("S01_SH01", "05_video/S01_SH01.mp4", video_status="failed"),
    )
    manifest = build_manifest(tmp_path)
    errors = preflight(manifest, tmp_path)
    assert any("failed" in e for e in errors)


def test_preflight_rejects_path_escaping_project_dir(tmp_path: Path):
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01", "../../etc/passwd"))
    manifest = build_manifest(tmp_path)
    errors = preflight(manifest, tmp_path)
    assert any("escapes project directory" in e for e in errors)


def test_render_raises_preflight_error_when_artifact_missing(tmp_path: Path):
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01", "05_video/missing.mp4"))
    manifest = build_manifest(tmp_path)
    with pytest.raises(RenderPreflightError):
        render(tmp_path, manifest)


def test_render_raises_preflight_error_for_empty_manifest(tmp_path: Path):
    manifest = {"shots": [], "audio": [], "captions": []}
    with pytest.raises(RenderPreflightError) as exc_info:
        render(tmp_path, manifest)
    assert any("no shots" in e for e in exc_info.value.errors)


def test_preflight_reports_error_for_empty_manifest(tmp_path: Path):
    manifest = {"shots": [], "audio": [], "captions": []}
    errors = preflight(manifest, tmp_path)
    assert errors == ["manifest contains no shots to render"]


def test_escape_concat_path_escapes_single_quote():
    escaped = _escape_concat_path(Path("/tmp/it's a dir/S01_SH01.mp4"))
    assert escaped == "/tmp/it'\\''s a dir/S01_SH01.mp4"
    # unescaping a shell single-quoted string containing this value should
    # round-trip back to the original path
    assert "'" not in escaped.replace("'\\''", "")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_render_handles_single_quote_in_artifact_path(tmp_path: Path):
    tricky_name = "S01_SH01's.mp4"
    _make_tiny_mp4(tmp_path / "05_video" / tricky_name)
    save_shot(
        tmp_path / "03_shots" / "S01_SH01.json",
        _shot("S01_SH01", f"05_video/{tricky_name}"),
    )

    manifest = build_manifest(tmp_path)
    output_path = render(tmp_path, manifest)

    assert output_path.exists()
    assert output_path.stat().st_size > 0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_render_produces_playable_final_video(tmp_path: Path):
    _make_tiny_mp4(tmp_path / "05_video" / "S01_SH01.mp4")
    _make_tiny_mp4(tmp_path / "05_video" / "S01_SH02.mp4")
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01", "05_video/S01_SH01.mp4"))
    save_shot(tmp_path / "03_shots" / "S01_SH02.json", _shot("S01_SH02", "05_video/S01_SH02.mp4"))

    manifest = build_manifest(tmp_path)
    output_path = render(tmp_path, manifest)

    assert output_path == tmp_path / "final" / "reel_001.mp4"
    assert output_path.exists()
    assert output_path.stat().st_size > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", str(output_path)],
        capture_output=True, text=True,
    )
    assert probe.returncode == 0
