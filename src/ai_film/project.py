from __future__ import annotations

import json
from pathlib import Path

PROJECT_DIRS = (
    "assets/characters", "assets/environments", "assets/props",
    "assets/reference-images", "assets/fonts",
    "00_story", "01_bibles", "02_scenes", "03_shots",
    "04_storyboard", "05_video",
    "06_audio/dialogue", "06_audio/sfx", "06_audio/music",
    "07_review",
    "final", "99_logs",
)

DEFAULT_CONFIG = {
    "providers": {
        "image": {"provider": "fal", "model": "nano-banana", "parameters": {}},
        "video": {"provider": "fal", "model": "veo-3", "parameters": {}},
        "voice": {"provider": "fal", "model": "csm-1b", "parameters": {}},
        "sfx": {"provider": "fal", "model": "thinksound", "parameters": {}},
        "music": {"provider": "fal", "model": "cassetteai-music", "parameters": {}},
        "lipsync": {"provider": "fal", "model": "kling-lipsync", "parameters": {}},
    },
    "generation": {"max_attempts": 3, "max_parallel_jobs": 3, "poll_interval_seconds": 5},
    "render": {"resolution": "1280x720", "fps": 24, "strict_format": False},
    "generation_approval": {},
}


def init_project(project_dir: Path, title: str) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    for rel in PROJECT_DIRS:
        (project_dir / rel).mkdir(parents=True, exist_ok=True)

    config_path = project_dir / "config.json"
    if not config_path.exists():
        config = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
        config["title"] = title
        config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False))
    return project_dir
