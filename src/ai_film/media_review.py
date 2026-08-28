# src/ai_film/media_review.py
from __future__ import annotations

import html
import shutil
import subprocess
from pathlib import Path

from ai_film.feedback_store import load_feedback
from ai_film.shot_store import load_shot

_AUDIO_STAGES = (("voice", "Voice"), ("music", "Music"), ("sfx", "SFX"))


def build_media_review(project_dir: Path, shot_id: str) -> Path:
    shot_path = project_dir / "03_shots" / f"{shot_id}.json"
    if not shot_path.exists():
        raise ValueError(f"no shot.json for {shot_id!r} at {shot_path}")
    shot = load_shot(shot_path)
    feedback = load_feedback(project_dir, shot_id)

    review_dir = project_dir / "07_review"
    review_dir.mkdir(parents=True, exist_ok=True)

    waveforms: dict[str, str] = {}
    if shutil.which("ffmpeg"):
        for stage, _label in _AUDIO_STAGES:
            stage_data = shot["generation"].get(stage, {})
            artifact = stage_data.get("artifact")
            if stage_data.get("status") == "completed" and artifact:
                out_png = review_dir / f"{shot_id}_{stage}_waveform.png"
                track_path = project_dir / artifact["path"]
                try:
                    subprocess.run(
                        [
                            "ffmpeg", "-y", "-i", str(track_path), "-filter_complex",
                            "showwavespic=s=640x60:colors=#6fbdb0", "-frames:v", "1",
                            str(out_png),
                        ],
                        check=True, capture_output=True,
                    )
                    waveforms[stage] = out_png.name
                except subprocess.CalledProcessError:
                    pass

    out_path = review_dir / f"{shot_id}.html"
    out_path.write_text(_render_html(shot, feedback, waveforms))
    return out_path


def _rel(project_relative_path: str) -> str:
    # 07_review/<id>.html sits one level below the project root, same as
    # every other top-level stage directory — so a stage artifact path
    # already stored project-relative (e.g. "05_video/S01_SH01.mp4") is
    # reached with a single "../" prefix.
    return f"../{project_relative_path}"


def _format_ts(seconds: float) -> str:
    return f"{seconds:.2f}s"


def _flag_html(entry: dict, duration: float) -> str:
    if entry.get("at") is None and entry.get("range") is None:
        return ""
    if entry.get("range"):
        position = entry["range"]["start"]
        ts_label = f'{entry["range"]["start"]:.1f}-{entry["range"]["end"]:.1f}s'
    else:
        position = entry["at"]
        ts_label = _format_ts(entry["at"])
    left_pct = 0.0 if duration <= 0 else max(0.0, min(100.0, position / duration * 100))
    status_class = "resolved" if entry["status"] == "resolved" else ""
    note = html.escape(entry["note"][:40])
    return (
        f'<div class="flag {status_class}" style="left:{left_pct:.2f}%">'
        f'<span class="tag">{ts_label} · {note}</span>'
        f'<span class="pin"></span><span class="stem"></span></div>'
    )


def _track_row_html(shot: dict, stage: str, label: str, waveforms: dict) -> str:
    stage_data = shot["generation"].get(stage, {})
    if stage_data.get("status") != "completed" or not stage_data.get("artifact"):
        return (
            f'<div class="track"><span class="name">{label}</span>'
            f'<span class="placeholder">not generated for this shot</span></div>'
        )
    artifact = stage_data["artifact"]
    version = stage_data.get("version", 1)
    history = stage_data.get("history", [])
    history_html = ""
    if history:
        rows = "".join(
            f'<div class="history-row">v{h["version"]} '
            f'<audio controls src="{_rel(h["artifact"]["path"])}"></audio></div>'
            for h in history
        )
        history_html = (
            f'<details><summary>{len(history)} earlier version(s)</summary>{rows}</details>'
        )
    waveform_html = ""
    if stage in waveforms:
        waveform_html = f'<img class="waveform" src="{waveforms[stage]}" alt="">'
    return (
        f'<div class="track"><span class="name">{label} · v{version}</span>'
        f'<audio controls src="{_rel(artifact["path"])}"></audio>'
        f'{waveform_html}{history_html}</div>'
    )


def _render_html(shot: dict, feedback: dict, waveforms: dict) -> str:
    shot_id = shot["id"]
    duration = float(shot.get("duration_seconds") or 0)
    video_stage = shot["generation"].get("video", {})
    if video_stage.get("status") == "completed" and video_stage.get("artifact"):
        video_html = (
            f'<video controls id="player" src="{_rel(video_stage["artifact"]["path"])}"></video>'
        )
    else:
        video_html = '<div class="placeholder">video not generated for this shot</div>'

    flags = "".join(_flag_html(e, duration) for e in feedback["entries"])
    open_list = "".join(
        f'<li>[{e["id"]}] {html.escape(e["target"])} — {html.escape(e["note"])}</li>'
        for e in feedback["entries"] if e["status"] == "open"
    )
    resolved_list = "".join(
        f'<li>[{e["id"]}] {html.escape(e["target"])} — {html.escape(e["note"])}</li>'
        for e in feedback["entries"] if e["status"] == "resolved"
    )
    tracks_html = "".join(
        _track_row_html(shot, stage, label, waveforms) for stage, label in _AUDIO_STAGES
    )

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Review: {shot_id}</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 780px; margin: 40px auto; }}
.mono {{ font-variant-numeric: tabular-nums; }}
.placeholder {{ color: #888; font-size: 13px; }}
video {{ width: 100%; }}
.ruler {{ position: relative; height: 40px; border-top: 1px solid #ccc; margin-top: 12px; }}
.flag {{ position: absolute; top: -14px; font-size: 10px; }}
.flag.resolved {{ color: green; }}
.track {{ padding: 8px 0; border-top: 1px solid #eee; }}
</style></head>
<body>
<h1>{shot_id}</h1>
<p>Review only — changes are made by the agent, not by editing here.</p>
{video_html}
<div class="ruler">{flags}</div>
<div class="tracks-header"><p>Media review tracks — playback only</p></div>
{tracks_html}
<h3>How to give feedback</h3>
<p>Review the shot, then describe any issue in chat. Include an approximate
timestamp when relevant: a point (3.3s), a range (3.3-4.1s), or a relative
offset (~400ms).</p>
<h3>Open feedback</h3><ul>{open_list or "<li>none</li>"}</ul>
<h3>Resolved feedback</h3><ul>{resolved_list or "<li>none</li>"}</ul>
</body></html>"""
