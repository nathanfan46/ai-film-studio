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


def _format_mmss(seconds: float) -> str:
    minutes = int(seconds // 60)
    rem = seconds - minutes * 60
    return f"{minutes:02d}:{rem:05.2f}"


def _entry_ts_label(entry: dict) -> str:
    if entry.get("range"):
        return f'{entry["range"]["start"]:.1f}-{entry["range"]["end"]:.1f}s'
    if entry.get("at") is not None:
        return _format_ts(entry["at"])
    return "general"


def _entry_list_html(entries: list[dict], status: str) -> str:
    rows = "".join(
        f'<li>[{e["id"]}] {html.escape(e["target"])} · {_entry_ts_label(e)} — {html.escape(e["note"])}</li>'
        for e in entries if e["status"] == status
    )
    return rows or "<li>none</li>"


def _flag_html(entry: dict, duration: float) -> str:
    if entry.get("at") is None and entry.get("range") is None:
        return ""
    band_html = ""
    if entry.get("range"):
        start = entry["range"]["start"]
        end = entry["range"]["end"]
        position = (start + end) / 2
        ts_label = f'{start:.1f}-{end:.1f}s'
        if duration > 0:
            start_pct = max(0.0, min(100.0, start / duration * 100))
            end_pct = max(0.0, min(100.0, end / duration * 100))
            band_class = "resolved" if entry["status"] == "resolved" else ""
            band_html = (
                f'<div class="band {band_class}" '
                f'style="left:{start_pct:.2f}%;width:{max(0.0, end_pct - start_pct):.2f}%"></div>'
            )
    else:
        position = entry["at"]
        ts_label = _format_ts(entry["at"])
    left_pct = 0.0 if duration <= 0 else max(0.0, min(100.0, position / duration * 100))
    status_class = "resolved" if entry["status"] == "resolved" else ""
    note = html.escape(entry["note"][:40])
    return (
        f'{band_html}'
        f'<div class="flag {status_class}" style="left:{left_pct:.2f}%">'
        f'<span class="tag">{ts_label} · {note}</span>'
        f'<span class="pin"></span><span class="stem"></span></div>'
    )


def _version_history_html(stage_data: dict, tag: str) -> tuple[str, str]:
    version = stage_data.get("version", 1)
    history = stage_data.get("history", [])
    history_html = ""
    if history:
        rows = "".join(
            f'<div class="history-row">v{h["version"]} '
            f'<{tag} controls src="{_rel(h["artifact"]["path"])}"></{tag}>'
            f'</div>'
            for h in history
        )
        history_html = (
            f'<details><summary>{len(history)} earlier version(s)</summary>{rows}</details>'
        )
    return f'v{version}', history_html


def _track_row_html(shot: dict, stage: str, label: str, waveforms: dict) -> str:
    stage_data = shot["generation"].get(stage, {})
    if stage_data.get("status") != "completed" or not stage_data.get("artifact"):
        return (
            f'<div class="track"><span class="name">{label}</span>'
            f'<span class="placeholder">not generated for this shot</span></div>'
        )
    artifact = stage_data["artifact"]
    version_label, history_html = _version_history_html(stage_data, "audio")
    waveform_html = ""
    if stage in waveforms:
        waveform_html = f'<img class="waveform" src="{waveforms[stage]}" alt="">'
    return (
        f'<div class="track"><span class="name">{label} · {version_label}</span>'
        f'<audio controls src="{_rel(artifact["path"])}"></audio>'
        f'{waveform_html}{history_html}</div>'
    )


def _render_html(shot: dict, feedback: dict, waveforms: dict) -> str:
    shot_id = shot["id"]
    action = html.escape(shot.get("action") or "")
    status = html.escape(shot.get("status") or "")
    video_stage = shot["generation"].get("video", {})
    has_video = video_stage.get("status") == "completed" and video_stage.get("artifact")
    # Prefer the actual artifact's duration (what the <video> element and JS
    # playhead use) over the planned shot duration — they can differ, and
    # flags/ticks must be positioned against the real playable duration.
    duration = float(
        (video_stage.get("artifact", {}).get("duration_seconds") if has_video else None)
        or shot.get("duration_seconds")
        or 0
    )
    if has_video:
        video_version_label, video_history_html = _version_history_html(video_stage, "video")
        video_html = (
            f'<video controls id="player" src="{_rel(video_stage["artifact"]["path"])}"></video>'
            f'<div class="track"><span class="name">Video · {video_version_label}</span>'
            f'{video_history_html}</div>'
        )
    else:
        video_html = '<div class="placeholder">video not generated for this shot</div>'

    flags = "".join(_flag_html(e, duration) for e in feedback["entries"])
    open_list = _entry_list_html(feedback["entries"], "open")
    resolved_list = _entry_list_html(feedback["entries"], "resolved")
    tracks_html = "".join(
        _track_row_html(shot, stage, label, waveforms) for stage, label in _AUDIO_STAGES
    )

    tick_count = max(1, int(duration))
    ticks = "".join(
        f'<div class="tick" style="left:{(i / duration * 100) if duration else 0:.2f}%"></div>'
        f'<div class="tick-label" style="left:{(i / duration * 100) if duration else 0:.2f}%">{i}s</div>'
        for i in range(tick_count + 1)
    ) if duration > 0 else ""

    script = ""
    if has_video:
        script = """
<script>
(function () {
  var player = document.getElementById("player");
  var tc = document.getElementById("tc");
  var playhead = document.getElementById("playhead");
  var ruler = document.getElementById("ruler");
  function fmt(s) {
    var m = Math.floor(s / 60);
    var rem = (s % 60).toFixed(2);
    return (m < 10 ? "0" + m : m) + ":" + (rem < 10 ? "0" + rem : rem);
  }
  player.addEventListener("timeupdate", function () {
    var d = player.duration || 0;
    tc.textContent = fmt(player.currentTime) + " / " + fmt(d);
    if (d > 0) { playhead.style.left = (player.currentTime / d * 100) + "%"; }
  });
  ruler.addEventListener("click", function (evt) {
    var rect = ruler.getBoundingClientRect();
    var frac = (evt.clientX - rect.left) / rect.width;
    if (player.duration) { player.currentTime = Math.max(0, Math.min(1, frac)) * player.duration; }
  });
})();
</script>
"""

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Review: {shot_id}</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 780px; margin: 40px auto; }}
.mono {{ font-variant-numeric: tabular-nums; }}
.placeholder {{ color: #888; font-size: 13px; }}
video {{ width: 100%; }}
.status-pill {{ display: inline-block; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em;
  padding: 2px 8px; border-radius: 100px; background: #eee; color: #555; }}
.action {{ color: #555; margin: 4px 0 12px; }}
.transport {{ display: flex; justify-content: flex-end; font-size: 12px; color: #555; margin-top: 6px; }}
.ruler {{ position: relative; height: 44px; border-top: 1px solid #ccc; margin-top: 12px; cursor: pointer; }}
.tick {{ position: absolute; top: 0; width: 1px; height: 8px; background: #ccc; }}
.tick-label {{ position: absolute; top: 10px; transform: translateX(-50%); font-size: 10px; color: #999; }}
.playhead {{ position: absolute; top: 0; bottom: 0; width: 1px; background: #2f8a7a; left: 0; }}
.band {{ position: absolute; top: -2px; height: 6px; background: rgba(217, 165, 74, 0.35); }}
.band.resolved {{ background: rgba(95, 174, 122, 0.35); }}
.flag {{ position: absolute; top: -18px; font-size: 10px; display: flex; flex-direction: column; align-items: center; }}
.flag .tag {{ white-space: nowrap; color: #b23e3e; }}
.flag.resolved .tag {{ color: #2f7a4d; }}
.flag .pin {{ width: 6px; height: 6px; border-radius: 1px; background: currentColor; transform: rotate(45deg); margin-top: 2px; }}
.flag .stem {{ width: 1px; height: 6px; background: #ccc; }}
.tracks-header {{ margin-top: 16px; }}
.tracks-header p {{ margin: 2px 0; font-size: 12px; color: #888; }}
.track {{ padding: 8px 0; border-top: 1px solid #eee; }}
.track .name {{ font-size: 12px; font-weight: 600; display: block; margin-bottom: 4px; }}
.track audio {{ width: 100%; }}
.track .waveform {{ width: 100%; display: block; margin-top: 4px; }}
.history-row {{ font-size: 11px; color: #666; margin-top: 4px; }}
</style></head>
<body>
<h1>{shot_id}</h1>
<span class="status-pill">{status}</span>
<p class="action">{action}</p>
<p>Review only — changes are made by the agent, not by editing here.</p>
{video_html}
<div class="transport"><span id="tc" class="mono">00:00.00 / {_format_mmss(duration)}</span></div>
<div class="ruler" id="ruler">{ticks}<div class="playhead" id="playhead"></div>{flags}</div>
<div class="tracks-header"><p>Media review tracks — playback only</p></div>
{tracks_html}
<h3>How to give feedback</h3>
<p>Review the shot, then describe any issue in chat. Include an approximate
timestamp when relevant: a point (3.3s), a range (3.3-4.1s), or a relative
offset (~400ms).</p>
<h3>Open feedback</h3><ul>{open_list}</ul>
<h3>Resolved feedback</h3><ul>{resolved_list}</ul>
{script}
</body></html>"""
