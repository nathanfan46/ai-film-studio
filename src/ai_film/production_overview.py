"""Static, whole-project status snapshot: every shot, grouped by scene,
with its storyboard thumbnail, camera framing, cast, per-stage generation
status, staleness, and a link into its existing per-shot review page —
so a human can see where the whole production stands without opening
each shot individually. Follows the same build-a-static-file pattern as
review_gallery.build_gallery/media_review.build_media_review: pure HTML,
no JS, no server. Unlike those, never raises on "nothing to show yet" —
a project with no shots is a normal state here, not a caller mistake."""

from __future__ import annotations

import html
import json
from pathlib import Path

from ai_film.asset_staleness import check_stale
from ai_film.scene_continuity import scene_id_for_shot
from ai_film.shot_store import list_shot_paths, load_shot

_STAGES = (
    ("image", "IMAGE"), ("video", "VIDEO"), ("voice", "VOICE"),
    ("sfx", "SFX"), ("music", "MUSIC"),
)

_STAGE_STATUS_COLOR = {
    "completed": "#2e7d32",
    "not_required": "#9e9e9e",
    "failed": "#c62828",
    "running": "#f9a825",
    "queued": "#f9a825",
    "pending": "#757575",
}

_STAGE_STATUS_SYMBOL = {
    "completed": "✓",
    "not_required": "—",
    "failed": "✗",
}


def build_production_overview(project_dir: Path) -> Path:
    shots_dir = project_dir / "03_shots"
    shots = (
        [load_shot(p) for p in list_shot_paths(shots_dir)] if shots_dir.exists() else []
    )
    stale_ids = {entry["shot_id"] for entry in check_stale(project_dir)}

    out_dir = project_dir / "07_review"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "overview.html"
    out_path.write_text(_render_html(project_dir, _project_title(project_dir), shots, stale_ids))
    return out_path


def _project_title(project_dir: Path) -> str:
    config_path = project_dir / "config.json"
    if not config_path.exists():
        return project_dir.name
    return json.loads(config_path.read_text()).get("title", project_dir.name)


def _rel(project_relative_path: str) -> str:
    # overview.html sits in 07_review/, one level below the project root,
    # same as every stage directory — see media_review.py's identical _rel.
    return f"../{project_relative_path}"


def _group_by_scene(shots: list[dict]) -> list[tuple[str, list[dict]]]:
    # `shots` is already in scene/shot order (list_shot_paths sorts by
    # filename, and shot ids are S<NN>_SH<NN>), so scene groups are always
    # contiguous — no re-sorting needed here.
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for shot in shots:
        scene_id = scene_id_for_shot(shot["id"]) or "?"
        if scene_id not in groups:
            groups[scene_id] = []
            order.append(scene_id)
        groups[scene_id].append(shot)
    return [(scene_id, groups[scene_id]) for scene_id in order]


def _thumbnail_html(project_dir: Path, shot: dict) -> str:
    # Deliberately keyed on the artifact file existing on disk, not on the
    # image stage's status field — a file that's actually there is the more
    # reliable display signal (see design discussion: stage metadata can
    # disagree with reality; the file on disk cannot).
    artifact = (shot.get("generation", {}).get("image") or {}).get("artifact")
    if artifact and artifact.get("path") and (project_dir / artifact["path"]).exists():
        return f'<img class="thumb" src="{html.escape(_rel(artifact["path"]))}" alt="">'
    return '<div class="thumb placeholder">No storyboard</div>'


def _review_link_html(project_dir: Path, shot_id: str) -> str:
    if (project_dir / "07_review" / f"{shot_id}.html").exists():
        return f'<a class="review-link" href="{html.escape(shot_id)}.html">Review shot &rarr;</a>'
    return '<span class="review-unavailable">Review unavailable</span>'


def _stage_badge_html(shot: dict, stage_key: str, label: str) -> str:
    status = shot.get("generation", {}).get(stage_key, {}).get("status", "pending")
    symbol = _STAGE_STATUS_SYMBOL.get(status, "…")
    color = _STAGE_STATUS_COLOR.get(status, "#757575")
    return f'<span class="stage" style="color:{color}">{label} {symbol}</span>'


def _shot_card_html(project_dir: Path, shot: dict, stale_ids: set[str]) -> str:
    shot_id = shot["id"]
    camera = (shot.get("camera") or {}).get("shot")
    camera_html = (
        f'<div class="camera">{html.escape(camera.upper())}</div>' if camera else ""
    )
    character_names = [c["name"] for c in shot.get("characters", []) if c.get("name")]
    cast_html = (
        f'<div class="cast">{html.escape(" · ".join(character_names))}</div>'
        if character_names else ""
    )
    environment_name = (shot.get("environment") or {}).get("name")
    environment_html = (
        f'<div class="environment">{html.escape(environment_name)}</div>'
        if environment_name else ""
    )
    stage_badges = "".join(_stage_badge_html(shot, key, label) for key, label in _STAGES)
    stale_html = '<span class="badge stale">STALE</span>' if shot_id in stale_ids else ""

    return (
        '<div class="card">'
        f'{_thumbnail_html(project_dir, shot)}'
        f'<div class="shot-id">{html.escape(shot_id)}</div>'
        f'{camera_html}{cast_html}{environment_html}'
        f'<div class="stages">{stage_badges}</div>'
        f'{stale_html}'
        f'<div class="review">{_review_link_html(project_dir, shot_id)}</div>'
        '</div>'
    )


def _summary_line(shots: list[dict], stale_count: int) -> str:
    if not shots:
        return "0 shots"
    counts: dict[str, int] = {}
    for shot in shots:
        counts[shot["status"]] = counts.get(shot["status"], 0) + 1
    parts = " · ".join(f"{count} {status}" for status, count in counts.items())
    stale_part = f" · {stale_count} stale" if stale_count else ""
    return f"{len(shots)} shots · {parts}{stale_part}"


def _render_html(project_dir: Path, title: str, shots: list[dict], stale_ids: set[str]) -> str:
    scenes = _group_by_scene(shots)
    sections = []
    for scene_id, scene_shots in scenes:
        cards = "".join(_shot_card_html(project_dir, shot, stale_ids) for shot in scene_shots)
        sections.append(
            f'<section><h2>{html.escape(scene_id)}</h2>'
            f'<div class="grid">{cards}</div></section>'
        )
    body = "".join(sections) or "<p>No shots yet.</p>"

    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)} — Production Overview</title>"
        "<style>"
        "body{font-family:sans-serif;margin:24px;color:#222}"
        "h1{margin-bottom:4px}"
        ".summary{color:#555;margin-bottom:24px}"
        "h2{border-bottom:1px solid #ddd;padding-bottom:4px}"
        ".grid{display:grid;grid-template-columns:repeat(auto-fit, minmax(220px, 1fr));gap:16px;margin-bottom:24px}"
        ".card{border:1px solid #ddd;border-radius:6px;padding:12px}"
        ".thumb{width:100%;border-radius:4px;display:block;margin-bottom:8px}"
        ".thumb.placeholder{aspect-ratio:16/9;background:#f0f0f0;color:#999;"
        "display:flex;align-items:center;justify-content:center;font-size:0.85em}"
        ".shot-id{font-weight:bold}"
        ".camera{font-size:0.8em;letter-spacing:0.05em;color:#555}"
        ".cast,.environment{font-size:0.85em;color:#555}"
        ".stages{margin-top:8px;font-size:0.8em;display:flex;gap:8px;flex-wrap:wrap}"
        ".badge.stale{display:inline-block;margin-top:6px;padding:2px 6px;background:#fff3cd;"
        "color:#856404;border-radius:4px;font-size:0.75em;font-weight:bold}"
        ".review{margin-top:8px;font-size:0.85em}"
        ".review-unavailable{color:#999}"
        "</style></head>"
        "<body>"
        f"<h1>{html.escape(title)}</h1>"
        f'<div class="summary">{html.escape(_summary_line(shots, len(stale_ids)))}</div>'
        f"{body}"
        "</body></html>"
    )
