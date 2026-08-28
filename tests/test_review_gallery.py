from pathlib import Path
from unittest.mock import patch

import pytest

from ai_film.candidate_store import add_candidates
from ai_film.review_gallery import build_gallery, open_in_browser


def _seed_candidates(tmp_path: Path) -> None:
    directory = tmp_path / "assets" / "characters" / "girl" / "candidates"
    directory.mkdir(parents=True)
    (directory / "001.png").write_bytes(b"ONE")
    (directory / "002.png").write_bytes(b"TWO")
    add_candidates(tmp_path, "character:girl", [
        {
            "id": "001", "path": "candidates/001.png", "provider": "mock", "model": "nano-banana",
            "prompt": "a girl", "parent": None, "operation": "generate", "job": None,
            "estimated_cost": None, "created_at": "2026-08-22T00:00:00Z",
        },
        {
            "id": "002", "path": "candidates/002.png", "provider": "mock", "model": "nano-banana",
            "prompt": "a girl, black jacket", "parent": "001", "operation": "edit", "job": None,
            "estimated_cost": None, "created_at": "2026-08-22T00:05:00Z",
        },
    ])


def test_build_gallery_raises_on_empty_pool(tmp_path: Path):
    with pytest.raises(ValueError):
        build_gallery(tmp_path, "character:girl")


def test_build_gallery_writes_html_listing_every_candidate_with_id_and_parent(tmp_path: Path):
    _seed_candidates(tmp_path)

    html_path = build_gallery(tmp_path, "character:girl")

    assert html_path == tmp_path / "assets" / "characters" / "girl" / "candidates" / "review.html"
    content = html_path.read_text()
    assert "001" in content
    assert "002" in content
    assert "001" in content.split("002")[1] or "parent" in content.lower() or "edit of 001" in content
    assert 'src="001.png"' in content
    assert 'src="002.png"' in content


def test_build_gallery_uses_a_responsive_grid_with_large_images(tmp_path: Path):
    _seed_candidates(tmp_path)

    html_path = build_gallery(tmp_path, "character:girl")

    content = html_path.read_text()
    assert "grid-template-columns" in content
    assert "max-width:300px" not in content


def test_build_gallery_marks_the_selected_candidate(tmp_path: Path):
    _seed_candidates(tmp_path)
    from ai_film.candidate_store import load_candidate_set, save_candidate_set
    candidate_set = load_candidate_set(tmp_path, "character:girl")
    candidate_set["selected"] = "002"
    save_candidate_set(tmp_path, "character:girl", candidate_set)

    html_path = build_gallery(tmp_path, "character:girl")

    content = html_path.read_text()
    assert "SELECTED" in content.upper()


@patch("ai_film.review_gallery.webbrowser")
def test_open_in_browser_calls_webbrowser_open_with_file_uri(mock_webbrowser, tmp_path: Path):
    html_path = tmp_path / "review.html"
    html_path.write_text("<html></html>")

    open_in_browser(html_path)

    mock_webbrowser.open.assert_called_once()
    call_arg = mock_webbrowser.open.call_args[0][0]
    assert call_arg.startswith("file://")
    assert str(html_path.resolve()) in call_arg
