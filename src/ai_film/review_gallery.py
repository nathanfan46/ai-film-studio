from __future__ import annotations

import webbrowser
from pathlib import Path

from ai_film.candidate_store import load_candidate_set, target_dir


def build_gallery(project_dir: Path, target: str) -> Path:
    candidate_set = load_candidate_set(project_dir, target)
    if not candidate_set["candidates"]:
        raise ValueError(
            f"no candidates for target {target!r}; run `ai-film generate-candidates` first"
        )

    directory = target_dir(project_dir, target)
    out_path = directory / "candidates" / "review.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_render_html(target, candidate_set))
    return out_path


def _render_html(target: str, candidate_set: dict) -> str:
    selected = candidate_set.get("selected")
    figures = []
    for candidate in candidate_set["candidates"]:
        filename = Path(candidate["path"]).name
        caption = candidate["id"]
        if candidate.get("parent"):
            caption += f" (edit of {candidate['parent']})"
        if selected == candidate["id"]:
            caption += " ★ SELECTED"
        figures.append(
            f'<figure style="margin:0">'
            f'<img src="{filename}" style="width:100%;display:block;border-radius:4px">'
            f'<figcaption style="margin-top:6px">{caption}</figcaption>'
            f'</figure>'
        )
    body = "".join(figures)
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>Candidates: {target}</title></head>"
        f'<body style="font-family:sans-serif;margin:24px">'
        f"<h1>{target}</h1>"
        f'<div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(360px, 1fr));gap:24px">{body}</div>'
        "</body></html>"
    )


def open_in_browser(html_path: Path) -> None:
    webbrowser.open(f"file://{html_path.resolve()}")
