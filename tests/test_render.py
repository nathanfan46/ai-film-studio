import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ai_film.render import RenderPreflightError, build_manifest, preflight, preflight_warnings, render
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


def _make_video(
    path: Path, width: int = 64, height: int = 64, fps: int = 24,
    duration: float = 1.0, with_audio: bool = False, audio_channels: int = 2,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"testsrc=duration={duration}:size={width}x{height}:rate={fps}",
    ]
    if with_audio:
        layout = "mono" if audio_channels == 1 else "stereo"
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}", "-ac", str(audio_channels)]
    cmd += [str(path)]
    subprocess.run(cmd, check=True, capture_output=True)


def _write_render_config(project_dir: Path, resolution: str = "1280x720", fps: int = 24) -> None:
    (project_dir / "config.json").write_text(
        json.dumps({"render": {"resolution": resolution, "fps": fps, "strict_format": False}})
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


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_render_normalizes_mixed_resolutions_to_config_target(tmp_path: Path):
    _write_render_config(tmp_path, resolution="640x360", fps=24)
    _make_video(tmp_path / "05_video" / "S01_SH01.mp4", width=640, height=360)
    _make_video(tmp_path / "05_video" / "S01_SH02.mp4", width=800, height=450)  # different native size
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01", "05_video/S01_SH01.mp4"))
    save_shot(tmp_path / "03_shots" / "S01_SH02.json", _shot("S01_SH02", "05_video/S01_SH02.mp4"))

    manifest = build_manifest(tmp_path)
    output_path = render(tmp_path, manifest)

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(output_path),
        ],
        capture_output=True, text=True,
    )
    assert probe.stdout.strip() == "640x360"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_render_never_drops_audio_when_shots_mix_silent_and_voiced(tmp_path: Path):
    _write_render_config(tmp_path)
    _make_video(tmp_path / "05_video" / "S01_SH01.mp4", width=640, height=360, with_audio=False)
    _make_video(tmp_path / "05_video" / "S01_SH02.mp4", width=640, height=360, with_audio=True)
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01", "05_video/S01_SH01.mp4"))
    save_shot(tmp_path / "03_shots" / "S01_SH02.json", _shot("S01_SH02", "05_video/S01_SH02.mp4"))

    manifest = build_manifest(tmp_path)
    output_path = render(tmp_path, manifest)

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
            "-of", "csv=p=0", str(output_path),
        ],
        capture_output=True, text=True,
    )
    assert probe.stdout.strip() != ""  # the final file has an audio stream — the original bug


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_render_normalizes_mixed_mono_stereo_and_silent_audio(tmp_path: Path):
    _write_render_config(tmp_path)
    _make_video(tmp_path / "05_video" / "S01_SH01.mp4", with_audio=True, audio_channels=1)
    _make_video(tmp_path / "05_video" / "S01_SH02.mp4", with_audio=True, audio_channels=2)
    _make_video(tmp_path / "05_video" / "S01_SH03.mp4", with_audio=False)
    for shot_id in ("S01_SH01", "S01_SH02", "S01_SH03"):
        save_shot(tmp_path / "03_shots" / f"{shot_id}.json", _shot(shot_id, f"05_video/{shot_id}.mp4"))

    manifest = build_manifest(tmp_path)
    output_path = render(tmp_path, manifest)  # must not raise — this is the regression this test guards

    assert output_path.exists()
    assert output_path.stat().st_size > 0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_render_normalizes_mixed_fps_to_config_target(tmp_path: Path):
    _write_render_config(tmp_path, resolution="640x360", fps=24)
    _make_video(tmp_path / "05_video" / "S01_SH01.mp4", width=640, height=360, fps=24)
    _make_video(tmp_path / "05_video" / "S01_SH02.mp4", width=640, height=360, fps=30)
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01", "05_video/S01_SH01.mp4"))
    save_shot(tmp_path / "03_shots" / "S01_SH02.json", _shot("S01_SH02", "05_video/S01_SH02.mp4"))

    manifest = build_manifest(tmp_path)
    output_path = render(tmp_path, manifest)

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate", "-of", "default=noprint_wrappers=1:nokey=1",
            str(output_path),
        ],
        capture_output=True, text=True,
    )
    from fractions import Fraction
    assert Fraction(probe.stdout.strip()) == Fraction(24, 1)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_preflight_warnings_reports_resolution_and_audio_spread(tmp_path: Path):
    _write_render_config(tmp_path, resolution="1280x720", fps=24)
    _make_video(tmp_path / "05_video" / "S01_SH01.mp4", width=640, height=360, with_audio=True)
    _make_video(tmp_path / "05_video" / "S01_SH02.mp4", width=800, height=450, with_audio=False)
    save_shot(tmp_path / "03_shots" / "S01_SH01.json", _shot("S01_SH01", "05_video/S01_SH01.mp4"))
    save_shot(tmp_path / "03_shots" / "S01_SH02.json", _shot("S01_SH02", "05_video/S01_SH02.mp4"))

    manifest = build_manifest(tmp_path)
    warnings = preflight_warnings(manifest, tmp_path)

    joined = "\n".join(warnings)
    assert "S01_SH01" in joined
    assert "S01_SH02" in joined
    assert "audio: no" in joined
    assert "final output format: 1280x720@24" in joined


def test_preflight_warnings_returns_empty_list_without_ffprobe(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("ai_film.render.shutil.which", lambda name: None)
    manifest = {"shots": [{"id": "S01_SH01", "video": "05_video/S01_SH01.mp4"}]}
    assert preflight_warnings(manifest, tmp_path) == []
