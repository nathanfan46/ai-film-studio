# Reference Video Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user analyze a local reference video into a per-scene structural/motion breakdown (zero fal.ai cost), have an agent enrich it with vision-based interpretation and a human-approval gate, then use it as grounding context in the existing story/storyboard flow — flagging shots that are good `MOTION_TRANSFER` candidates along the way.

**Architecture:** A new local-only module (`reference_analysis.py`, no provider abstraction, ffmpeg/ffprobe only) does deterministic extraction — scene cuts, a coarse per-scene motion signal, keyframes — and writes `assets/reference-video/video_analysis_brief.json`. A new agent skill (`ai-film-reference-analyst`) reads that brief, looks at the keyframes with its own vision, fills in the semantic fields, and gets human approval before marking the brief `approved`. `ai-film-director`/`ai-film-storyboard` read an approved brief, when present, as additional grounding.

**Tech Stack:** Python 3.11, ffmpeg/ffprobe (already a runtime dependency, no new pip packages), typer, pytest.

**Spec:** `docs/superpowers/specs/2026-09-04-reference-video-analysis-design.md`

## Global Constraints

- No new pip dependencies. Exactly the same 4 (`typer`, `jsonschema`, `requests`, `python-dotenv`) as today.
- Local file input only — `--source` must be an existing local path. No URL/yt-dlp support.
- Scene-cut threshold: `0.35`. Visual-change-level thresholds: `low` if mean interior scene score `< 0.02`, `medium` if `0.02 <= score <= 0.08`, `high` if `> 0.08`.
- `assets/reference-video/` layout: `source.<ext>`, `keyframes/`, `video_analysis_brief.json` — no other files.
- `analyze-reference-video` refuses to run (regardless of `--force`) if an existing brief's `"approved"` field is `true`. Without `--force` it refuses whenever the brief file exists at all. `--force` only ever overwrites an unapproved brief.
- No formal JSON Schema validation for `video_analysis_brief.json` in v1 (it's an intermediate, agent-rewritten working document, unlike `shot.json`'s `SHOT_SCHEMA`).
- No `Capability` enum entry, no fal provider, no mock provider for this feature — it is pure local ffmpeg, same category as `render`/`diagnose-video`.
- Tests that need real ffmpeg output generate a tiny synthetic clip on the fly via `ffmpeg -f lavfi` and are guarded with `@pytest.mark.skipif(shutil.which("ffmpeg") is None, ...)` — this is this codebase's actual established pattern (see `tests/test_video_diagnostics.py`), not the checked-in-fixture-file approach the spec's own Testing section suggested; following the codebase's real precedent takes priority over the spec's testing-section wording, which the spec itself leaves as an implementation detail for this plan to resolve.

---

### Task 1: Local ffmpeg probe helpers

**Files:**
- Create: `src/ai_film/reference_analysis.py`
- Test: `tests/test_reference_analysis.py`

**Interfaces:**
- Produces: `_require_ffmpeg() -> None` (raises `RuntimeError` if ffmpeg/ffprobe missing), `_probe_duration(video_path: Path) -> float`, `_probe_stream_info(video_path: Path) -> dict` (returns `{"resolution": "WxH", "fps": float}`) — all module-private, consumed by Task 5's orchestration function.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_reference_analysis.py
import shutil
import subprocess
from pathlib import Path

import pytest

from ai_film.reference_analysis import _probe_duration, _probe_stream_info, _require_ffmpeg


def _make_tiny_video(path: Path, duration: float = 2.0, size: str = "64x64", rate: int = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"testsrc=duration={duration}:size={size}:rate={rate}",
            "-t", str(duration), str(path),
        ],
        check=True, capture_output=True,
    )


def test_require_ffmpeg_raises_when_missing(monkeypatch):
    monkeypatch.setattr("ai_film.reference_analysis.shutil.which", lambda name: None)
    with pytest.raises(RuntimeError):
        _require_ffmpeg()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_probe_duration_reads_real_duration(tmp_path: Path):
    video_path = tmp_path / "clip.mp4"
    _make_tiny_video(video_path, duration=2.0)
    assert _probe_duration(video_path) == pytest.approx(2.0, abs=0.15)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_probe_stream_info_reads_resolution_and_fps(tmp_path: Path):
    video_path = tmp_path / "clip.mp4"
    _make_tiny_video(video_path, duration=1.0, size="64x48", rate=10)
    info = _probe_stream_info(video_path)
    assert info["resolution"] == "64x48"
    assert info["fps"] == pytest.approx(10.0, abs=0.01)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_reference_analysis.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_film.reference_analysis'`

- [ ] **Step 3: Write the implementation**

```python
# src/ai_film/reference_analysis.py
"""Local, zero-cost reference-video analysis: scene cuts, a coarse
per-scene motion signal, and keyframes, extracted entirely via ffmpeg —
see docs/superpowers/specs/2026-09-04-reference-video-analysis-design.md.
No provider abstraction here; this never calls a fal.ai endpoint."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ai_film.services.generation_service import project_relative_path

_FFMPEG_MISSING_MSG = "ffmpeg/ffprobe is not installed or not on PATH"


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError(_FFMPEG_MISSING_MSG)


def _probe_duration(video_path: Path) -> float:
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
        ],
        capture_output=True, text=True,
    )
    return float(probe.stdout.strip())


def _probe_stream_info(video_path: Path) -> dict:
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate",
            "-of", "json", str(video_path),
        ],
        capture_output=True, text=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    num, den = stream["r_frame_rate"].split("/")
    fps = round(float(num) / float(den), 3) if float(den) else 0.0
    return {"resolution": f"{stream['width']}x{stream['height']}", "fps": fps}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_reference_analysis.py -v`
Expected: PASS (4 tests; the two `skipif`-guarded ones run and pass if ffmpeg is installed locally)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/reference_analysis.py tests/test_reference_analysis.py
git commit -m "feat: add ffmpeg probe helpers for reference-video analysis"
```

---

### Task 2: Scene-cut detection

**Files:**
- Modify: `src/ai_film/reference_analysis.py` (append)
- Test: `tests/test_reference_analysis.py` (append)

**Interfaces:**
- Consumes: nothing from Task 1's functions directly, but lives in the same module and file.
- Produces: `_SCENE_THRESHOLD: float = 0.35` (module constant), `_detect_scene_cuts(video_path: Path, threshold: float = _SCENE_THRESHOLD) -> list[float]`, `_scenes_from_cuts(cut_timestamps: list[float], total_duration: float) -> list[tuple[float, float]]` — consumed by Task 5's orchestration.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_reference_analysis.py`:

```python
from ai_film.reference_analysis import _detect_scene_cuts, _scenes_from_cuts


def _make_two_scene_video(path: Path, seg_duration: float = 2.0, rate: int = 10, size: str = "64x64") -> None:
    """A synthetic clip with exactly one hard scene cut at seg_duration:
    seg_duration seconds of a flat color, then seg_duration seconds of
    ffmpeg's own testsrc2 moving pattern. Verified empirically (during
    spec design) that ffmpeg's scene filter correctly detects the
    boundary between these two segments."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=blue:s={size}:d={seg_duration}:r={rate}",
            "-f", "lavfi", "-i", f"testsrc2=s={size}:d={seg_duration}:r={rate}",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", str(path),
        ],
        check=True, capture_output=True,
    )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_detect_scene_cuts_finds_the_boundary(tmp_path: Path):
    video_path = tmp_path / "two_scene.mp4"
    _make_two_scene_video(video_path, seg_duration=2.0)
    cuts = _detect_scene_cuts(video_path)
    assert len(cuts) == 1
    assert cuts[0] == pytest.approx(2.0, abs=0.15)


def test_scenes_from_cuts_builds_boundary_pairs():
    assert _scenes_from_cuts([2.0], 4.0) == [(0.0, 2.0), (2.0, 4.0)]


def test_scenes_from_cuts_handles_no_cuts():
    assert _scenes_from_cuts([], 4.0) == [(0.0, 4.0)]


def test_scenes_from_cuts_deduplicates_cut_at_zero():
    assert _scenes_from_cuts([0.0], 4.0) == [(0.0, 4.0)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_reference_analysis.py -v -k scene_cuts or scenes_from_cuts`
Expected: FAIL with `ImportError: cannot import name '_detect_scene_cuts'`

- [ ] **Step 3: Write the implementation**

Append to `src/ai_film/reference_analysis.py`:

```python
_SCENE_THRESHOLD = 0.35


def _detect_scene_cuts(video_path: Path, threshold: float = _SCENE_THRESHOLD) -> list[float]:
    """Scene-cut timestamps (seconds), NOT including 0.0. ffmpeg's
    showinfo filter logs one line per frame selected by the scene
    expression; each such line's pts_time is a cut boundary. Verified
    empirically against a real synthetic 2-scene clip during spec
    design — showinfo does log pts_time correctly for this filter."""
    result = subprocess.run(
        [
            "ffmpeg", "-i", str(video_path),
            "-vf", f"select='gt(scene,{threshold})',showinfo",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    cuts = []
    for line in result.stderr.splitlines():
        if "pts_time:" not in line:
            continue
        cuts.append(float(line.split("pts_time:", 1)[1].split()[0]))
    return sorted(set(cuts))


def _scenes_from_cuts(cut_timestamps: list[float], total_duration: float) -> list[tuple[float, float]]:
    boundaries = sorted(set(cut_timestamps) | {0.0, total_duration})
    return [
        (boundaries[i], boundaries[i + 1])
        for i in range(len(boundaries) - 1)
        if boundaries[i + 1] > boundaries[i]
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_reference_analysis.py -v`
Expected: PASS (8 tests total so far)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/reference_analysis.py tests/test_reference_analysis.py
git commit -m "feat: add scene-cut detection for reference-video analysis"
```

---

### Task 3: Motion signal (visual-change-level bucketing)

**Files:**
- Modify: `src/ai_film/reference_analysis.py` (append)
- Test: `tests/test_reference_analysis.py` (append)

**Interfaces:**
- Consumes: nothing directly from earlier tasks' functions (same module).
- Produces: `_LOW_MAX: float = 0.02`, `_MEDIUM_MAX: float = 0.08` (module constants), `_scene_scores(video_path: Path) -> list[tuple[float, float]]` (list of `(pts_time, scene_score)`), `_mean_interior_score(scene_start: float, scene_end: float, scores: list[tuple[float, float]]) -> float`, `_visual_change_level(mean_score: float) -> str` (returns `"low"`/`"medium"`/`"high"`) — all consumed by Task 5's orchestration.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_reference_analysis.py`:

```python
from ai_film.reference_analysis import _mean_interior_score, _scene_scores, _visual_change_level


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_scene_scores_spike_at_the_cut(tmp_path: Path):
    video_path = tmp_path / "two_scene.mp4"
    _make_two_scene_video(video_path, seg_duration=2.0)
    scores = _scene_scores(video_path)
    cut_scores = [score for t, score in scores if abs(t - 2.0) < 0.05]
    assert cut_scores and cut_scores[0] > 0.5


def test_mean_interior_score_excludes_boundaries():
    scores = [(0.0, 0.9), (1.0, 0.01), (2.0, 0.9)]
    assert _mean_interior_score(0.0, 2.0, scores) == pytest.approx(0.01)


def test_mean_interior_score_returns_zero_when_no_interior_frames():
    assert _mean_interior_score(0.0, 2.0, []) == 0.0


def test_visual_change_level_buckets():
    assert _visual_change_level(0.01) == "low"
    assert _visual_change_level(0.02) == "medium"
    assert _visual_change_level(0.08) == "medium"
    assert _visual_change_level(0.081) == "high"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_reference_analysis.py -v -k scene_scores or interior_score or change_level`
Expected: FAIL with `ImportError: cannot import name '_scene_scores'`

- [ ] **Step 3: Write the implementation**

Append to `src/ai_film/reference_analysis.py`:

```python
_LOW_MAX = 0.02
_MEDIUM_MAX = 0.08


def _scene_scores(video_path: Path) -> list[tuple[float, float]]:
    """Every frame's raw ffmpeg scene-change score (0.0-1.0), before any
    cut threshold is applied. showinfo does NOT print this value —
    verified empirically during spec design against real ffmpeg 9.0.1
    output — it must come from the metadata filter's print mode, which
    emits a two-line block per frame: "frame:N pts:P pts_time:T" then
    "lavfi.scene_score=X" on the next line."""
    result = subprocess.run(
        [
            "ffmpeg", "-i", str(video_path),
            "-vf", "select='gte(scene,0)',metadata=print:key=lavfi.scene_score",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    scores: list[tuple[float, float]] = []
    pending_time: float | None = None
    for line in result.stderr.splitlines():
        if "pts_time:" in line:
            pending_time = float(line.split("pts_time:", 1)[1].split()[0])
        elif "lavfi.scene_score=" in line and pending_time is not None:
            scores.append((pending_time, float(line.split("lavfi.scene_score=", 1)[1].strip())))
            pending_time = None
    return scores


def _mean_interior_score(scene_start: float, scene_end: float, scores: list[tuple[float, float]]) -> float:
    """Mean score of frames strictly between a scene's boundaries — the
    boundary frames themselves are cut frames (score spikes near 1.0 by
    definition) and must not pull a static scene's average up."""
    interior = [score for t, score in scores if scene_start < t < scene_end]
    if not interior:
        return 0.0
    return sum(interior) / len(interior)


def _visual_change_level(mean_score: float) -> str:
    # Heuristic starting points, not measured constants — expect these
    # to be revisited once real reference footage has been run through
    # this, per the design spec.
    if mean_score < _LOW_MAX:
        return "low"
    if mean_score <= _MEDIUM_MAX:
        return "medium"
    return "high"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_reference_analysis.py -v`
Expected: PASS (12 tests total so far)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/reference_analysis.py tests/test_reference_analysis.py
git commit -m "feat: add motion-signal bucketing for reference-video analysis"
```

---

### Task 4: Keyframe extraction

**Files:**
- Modify: `src/ai_film/reference_analysis.py` (append)
- Test: `tests/test_reference_analysis.py` (append)

**Interfaces:**
- Produces: `_extract_keyframe(video_path: Path, timestamp: float, output_path: Path) -> None` — consumed by Task 5's orchestration.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_reference_analysis.py`:

```python
from ai_film.reference_analysis import _extract_keyframe


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_extract_keyframe_writes_a_real_file(tmp_path: Path):
    video_path = tmp_path / "clip.mp4"
    _make_tiny_video(video_path, duration=2.0)
    out_path = tmp_path / "keyframes" / "scene00_start.jpg"

    _extract_keyframe(video_path, 0.1, out_path)

    assert out_path.exists()
    assert out_path.stat().st_size > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reference_analysis.py -v -k extract_keyframe`
Expected: FAIL with `ImportError: cannot import name '_extract_keyframe'`

- [ ] **Step 3: Write the implementation**

Append to `src/ai_film/reference_analysis.py`:

```python
def _extract_keyframe(video_path: Path, timestamp: float, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(timestamp), "-i", str(video_path), "-frames:v", "1", str(output_path)],
        check=True, capture_output=True,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_reference_analysis.py -v`
Expected: PASS (13 tests total so far)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/reference_analysis.py tests/test_reference_analysis.py
git commit -m "feat: add keyframe extraction for reference-video analysis"
```

---

### Task 5: Orchestration — `analyze_reference_video`

**Files:**
- Modify: `src/ai_film/reference_analysis.py` (append)
- Modify: `src/ai_film/project.py:6-14` (add `"assets/reference-video"` to `PROJECT_DIRS`)
- Test: `tests/test_reference_analysis.py` (append)
- Test: `tests/test_project.py` (append one assertion)

**Interfaces:**
- Consumes: every function from Tasks 1-4 (`_require_ffmpeg`, `_probe_duration`, `_probe_stream_info`, `_SCENE_THRESHOLD`, `_detect_scene_cuts`, `_scenes_from_cuts`, `_LOW_MAX`, `_MEDIUM_MAX`, `_scene_scores`, `_mean_interior_score`, `_visual_change_level`, `_extract_keyframe`); `project_relative_path(path_str: str, project_dir: Path) -> str` from `ai_film.services.generation_service`; `init_project(project_dir: Path, title: str) -> Path` from `ai_film.project` (test only).
- Produces: `analyze_reference_video(project_dir: Path, source_path: Path, force: bool = False) -> dict` — the full brief dict, also written to `assets/reference-video/video_analysis_brief.json`. Consumed by Task 6's CLI command.

- [ ] **Step 1: Write the failing tests**

Add `import json` to `tests/test_reference_analysis.py`'s existing import block at the top of the file (alongside `shutil`, `subprocess`, `Path`, `pytest`) — Task 5's tests are the first ones in this file to need it.

Append to `tests/test_reference_analysis.py`:

```python
from ai_film.project import init_project
from ai_film.reference_analysis import analyze_reference_video


def test_analyze_reference_video_rejects_missing_source(tmp_path: Path):
    project_dir = init_project(tmp_path / "proj", "Test")
    with pytest.raises(ValueError):
        analyze_reference_video(project_dir, tmp_path / "missing.mp4")


def test_analyze_reference_video_refuses_when_approved(tmp_path: Path):
    project_dir = init_project(tmp_path / "proj", "Test")
    source = tmp_path / "ref.mp4"
    source.write_bytes(b"FAKE-NOT-A-REAL-VIDEO")
    ref_dir = project_dir / "assets" / "reference-video"
    ref_dir.mkdir(parents=True)
    (ref_dir / "video_analysis_brief.json").write_text(json.dumps({"approved": True}))

    with pytest.raises(RuntimeError, match="already approved"):
        analyze_reference_video(project_dir, source, force=True)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_analyze_reference_video_writes_brief_and_keyframes(tmp_path: Path):
    project_dir = init_project(tmp_path / "proj", "Test")
    source = tmp_path / "ref.mp4"
    _make_two_scene_video(source, seg_duration=2.0)

    brief = analyze_reference_video(project_dir, source)

    assert brief["approved"] is False
    assert brief["schema_version"] == "1.0"
    assert len(brief["scenes"]) == 2
    for scene in brief["scenes"]:
        assert scene["description"] is None
        assert scene["motion_transfer_candidate"] is None
        for kf_rel_path in scene["keyframes"]:
            assert (project_dir / kf_rel_path).exists()

    brief_path = project_dir / "assets" / "reference-video" / "video_analysis_brief.json"
    assert json.loads(brief_path.read_text()) == brief
    assert (project_dir / "assets" / "reference-video" / "source.mp4").exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_analyze_reference_video_refuses_second_run_without_force(tmp_path: Path):
    project_dir = init_project(tmp_path / "proj", "Test")
    source = tmp_path / "ref.mp4"
    _make_tiny_video(source, duration=1.0)
    analyze_reference_video(project_dir, source)

    with pytest.raises(RuntimeError, match="already exists"):
        analyze_reference_video(project_dir, source)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_analyze_reference_video_force_overwrites_unapproved(tmp_path: Path):
    project_dir = init_project(tmp_path / "proj", "Test")
    source = tmp_path / "ref.mp4"
    _make_tiny_video(source, duration=1.0)
    analyze_reference_video(project_dir, source)

    analyze_reference_video(project_dir, source, force=True)  # must not raise
```

Also append to `tests/test_project.py` (open the file first to match its existing style/imports before appending — it already imports `init_project` and `PROJECT_DIRS`-adjacent behavior):

```python
def test_init_project_creates_reference_video_dir(tmp_path: Path):
    project_dir = init_project(tmp_path / "proj", "Test")
    assert (project_dir / "assets" / "reference-video").is_dir()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_reference_analysis.py tests/test_project.py -v -k analyze_reference_video or reference_video_dir`
Expected: FAIL — `ImportError: cannot import name 'analyze_reference_video'` and the new `test_project.py` assertion fails (`assets/reference-video` doesn't exist yet).

- [ ] **Step 3: Write the implementation**

Edit `src/ai_film/project.py:6-14`:

```python
PROJECT_DIRS = (
    "assets/characters", "assets/environments", "assets/props",
    "assets/reference-images", "assets/fonts", "assets/reference-video",
    "00_story", "01_bibles", "02_scenes", "03_shots",
    "04_storyboard", "05_video",
    "06_audio/dialogue", "06_audio/sfx", "06_audio/music",
    "07_review",
    "final", "99_logs",
)
```

Append to `src/ai_film/reference_analysis.py`:

```python
_KEYFRAME_START_OFFSET = 0.1
_KEYFRAME_MIDPOINT_MIN_DURATION = 3.0


def _copy_source(source_path: Path, ref_dir: Path) -> Path:
    ext = source_path.suffix or ".mp4"
    dest_path = ref_dir / f"source{ext}"
    shutil.copyfile(source_path, dest_path)
    return dest_path


def analyze_reference_video(project_dir: Path, source_path: Path, force: bool = False) -> dict:
    """Copy source_path into assets/reference-video/, run scene detection
    + motion-signal bucketing + keyframe extraction, write and return
    video_analysis_brief.json's contents. Pure local ffmpeg — never
    touches a fal.ai endpoint. See the design spec's "Ownership
    invariant" for the approved/force refusal rules below."""
    if not source_path.exists():
        raise ValueError(f"source video not found: {source_path}")

    ref_dir = project_dir / "assets" / "reference-video"
    brief_path = ref_dir / "video_analysis_brief.json"
    if brief_path.exists():
        existing = json.loads(brief_path.read_text())
        if existing.get("approved"):
            raise RuntimeError(
                f"{brief_path} is already approved — move or rename it "
                "before re-running analysis"
            )
        if not force:
            raise RuntimeError(f"{brief_path} already exists — pass --force to overwrite")

    _require_ffmpeg()

    ref_dir.mkdir(parents=True, exist_ok=True)
    keyframes_dir = ref_dir / "keyframes"
    keyframes_dir.mkdir(parents=True, exist_ok=True)

    dest_path = _copy_source(source_path, ref_dir)
    duration = _probe_duration(dest_path)
    stream_info = _probe_stream_info(dest_path)

    cut_timestamps = _detect_scene_cuts(dest_path)
    scene_bounds = _scenes_from_cuts(cut_timestamps, duration)
    scores = _scene_scores(dest_path)

    scenes = []
    for index, (start, end) in enumerate(scene_bounds):
        level = _visual_change_level(_mean_interior_score(start, end, scores))

        keyframe_paths = []
        start_kf = keyframes_dir / f"scene{index:02d}_start.jpg"
        _extract_keyframe(dest_path, start + _KEYFRAME_START_OFFSET, start_kf)
        keyframe_paths.append(project_relative_path(str(start_kf), project_dir))
        if end - start > _KEYFRAME_MIDPOINT_MIN_DURATION:
            mid_kf = keyframes_dir / f"scene{index:02d}_mid.jpg"
            _extract_keyframe(dest_path, start + (end - start) / 2, mid_kf)
            keyframe_paths.append(project_relative_path(str(mid_kf), project_dir))

        scenes.append({
            "scene_index": index,
            "start_seconds": round(start, 2),
            "end_seconds": round(end, 2),
            "keyframes": keyframe_paths,
            "visual_change_level": level,
            "description": None,
            "subject": None,
            "subject_motion": None,
            "camera": None,
            "motion_transfer_candidate": None,
        })

    brief = {
        "schema_version": "1.0",
        "approved": False,
        "source": {
            "original_filename": source_path.name,
            "path": project_relative_path(str(dest_path), project_dir),
            "duration_seconds": round(duration, 2),
            "resolution": stream_info["resolution"],
            "fps": stream_info["fps"],
        },
        "scenes": scenes,
        "analysis_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scene_threshold": _SCENE_THRESHOLD,
            "change_level_thresholds": {"low_max": _LOW_MAX, "medium_max": _MEDIUM_MAX},
        },
    }
    brief_path.write_text(json.dumps(brief, indent=2, ensure_ascii=False))
    return brief
```

Note the ordering: the `approved`/`force` file-state checks run *before* `_require_ffmpeg()`, so a refusal due to an already-approved brief doesn't require ffmpeg to even be installed — this is what makes `test_analyze_reference_video_refuses_when_approved` deterministic on any machine, not just ones with ffmpeg.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_reference_analysis.py tests/test_project.py -v`
Expected: PASS (18 tests in `test_reference_analysis.py`, plus the existing `test_project.py` suite with 1 new passing test)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/reference_analysis.py src/ai_film/project.py tests/test_reference_analysis.py tests/test_project.py
git commit -m "feat: orchestrate reference-video analysis into a brief JSON"
```

---

### Task 6: CLI command — `analyze-reference-video`

**Files:**
- Modify: `src/ai_film/cli.py` (add import near the other service imports, add command function between `diagnose_video_cmd` and `review_media_cmd`, i.e. immediately after the block ending at line 916 and before `@app.command(name="review-media")` at line 920 — re-check exact line numbers with `grep -n '@app.command(name="review-media")' src/ai_film/cli.py` before editing, since earlier tasks in this plan don't touch `cli.py` and line numbers should be stable, but confirm before editing)
- Modify: `.claude/settings.json` (add `analyze-reference-video` to both permission lists, next to `diagnose-video`)
- Test: `tests/test_cli_reference_commands.py` (new file)

**Interfaces:**
- Consumes: `analyze_reference_video` from `ai_film.reference_analysis` (Task 5).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_reference_commands.py
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_film.cli import app
from ai_film.project import init_project

runner = CliRunner()


def _make_tiny_video(path: Path, duration: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"testsrc=duration={duration}:size=64x64:rate=10",
            "-t", str(duration), str(path),
        ],
        check=True, capture_output=True,
    )


def test_analyze_reference_video_cmd_rejects_missing_source(tmp_path: Path):
    project_dir = init_project(tmp_path / "proj", "Test")
    result = runner.invoke(
        app,
        ["analyze-reference-video", "--source", str(tmp_path / "missing.mp4"), "--path", str(project_dir)],
    )
    assert result.exit_code == 1
    assert "not found" in result.output


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_analyze_reference_video_cmd_success(tmp_path: Path):
    project_dir = init_project(tmp_path / "proj", "Test")
    source = tmp_path / "ref.mp4"
    _make_tiny_video(source, duration=1.0)

    result = runner.invoke(
        app,
        ["analyze-reference-video", "--source", str(source), "--path", str(project_dir)],
    )

    assert result.exit_code == 0
    assert "analyzed ref.mp4" in result.output
    assert (project_dir / "assets" / "reference-video" / "video_analysis_brief.json").exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_analyze_reference_video_cmd_refuses_second_run_without_force(tmp_path: Path):
    project_dir = init_project(tmp_path / "proj", "Test")
    source = tmp_path / "ref.mp4"
    _make_tiny_video(source, duration=1.0)
    runner.invoke(app, ["analyze-reference-video", "--source", str(source), "--path", str(project_dir)])

    result = runner.invoke(
        app,
        ["analyze-reference-video", "--source", str(source), "--path", str(project_dir)],
    )

    assert result.exit_code == 1
    assert "already exists" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_reference_commands.py -v`
Expected: FAIL — `analyze-reference-video` is not a registered command (typer reports "No such command").

- [ ] **Step 3: Write the implementation**

Add to `src/ai_film/cli.py`'s import block, alongside the other `video_diagnostics` imports:

```python
from ai_film.reference_analysis import analyze_reference_video as analyze_reference_video_service
```

Add the command, immediately after `diagnose_video_cmd`'s body and before `@app.command(name="review-media")`:

```python
@app.command(name="analyze-reference-video")
def analyze_reference_video_cmd(
    source: Path = typer.Option(..., "--source"),
    path: Path = typer.Option(DEFAULT_PROJECT_PATH, "--path"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Analyze a local reference video: scene cuts, a coarse per-scene
    motion signal, and keyframes — local ffmpeg only, no fal cost. Writes
    assets/reference-video/video_analysis_brief.json for
    ai-film-reference-analyst to read and enrich."""
    try:
        brief = analyze_reference_video_service(path, source, force=force)
    except (ValueError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    brief_path = path / "assets" / "reference-video" / "video_analysis_brief.json"
    typer.echo(f"analyzed {source.name}: {len(brief['scenes'])} scene(s) -> {brief_path}")
```

Edit `.claude/settings.json` — add one line after `"Bash(ai-film diagnose-video *)",` (line 14) and one after `"Bash(./.venv/bin/ai-film diagnose-video *)",` (line 30):

```json
      "Bash(ai-film diagnose-video *)",
      "Bash(ai-film analyze-reference-video *)",
```

```json
      "Bash(./.venv/bin/ai-film diagnose-video *)",
      "Bash(./.venv/bin/ai-film analyze-reference-video *)",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli_reference_commands.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/cli.py .claude/settings.json tests/test_cli_reference_commands.py
git commit -m "feat: wire analyze-reference-video into the CLI"
```

---

### Task 7: Agent skill, slash command, and pipeline integration

**Files:**
- Create: `.claude/agents/ai-film-reference-analyst.md`
- Create: `.claude/commands/analyze-reference.md`
- Modify: `.claude/agents/ai-film-director.md` (add a new step)
- Modify: `.claude/agents/ai-film-storyboard.md` (add a new step)

No automated test for this task — these are `.md` prompt files this project's test suite does not execute, consistent with how `ai-film-director`/`ai-film-storyboard`'s existing brainstorm logic isn't unit-tested either (confirmed in the design spec's own Testing section). Verification here is a careful read-through against the spec's Agent skill section, not `pytest`.

**Interfaces:**
- Consumes: `ai-film analyze-reference-video` (Task 6's CLI command), the `video_analysis_brief.json` schema (Task 5).
- Produces: the approved, enriched `video_analysis_brief.json` (with `"approved": true` and every scene's `description`/`subject`/`subject_motion`/`camera`/`motion_transfer_candidate` filled in) — consumed by the `ai-film-director`/`ai-film-storyboard` integration steps this task also adds.

- [ ] **Step 1: Write `.claude/agents/ai-film-reference-analyst.md`**

```markdown
---
name: ai-film-reference-analyst
description: Analyzes a local reference video into a per-scene structural/motion breakdown, enriches it with vision, and gets human approval before it's used as grounding for the story/storyboard flow. Dispatched by /analyze-reference — do not invoke directly except to redo an existing project's reference analysis.
tools: ["Read", "Write", "Bash", "Glob"]
model: sonnet
---

You are the Reference Video Analyst for an `ai-film-studio` project. Your job produces exactly one artifact: an approved `assets/reference-video/video_analysis_brief.json`. You never create or modify `03_shots/*.json` yourself — that stays the job of `ai-film-director`/`ai-film-storyboard`, which read your approved brief as grounding context.

You are given the project's root path (`PROJECT_PATH`) and a local video source path (`SOURCE_PATH`) in your dispatch instructions. All paths below are relative to `PROJECT_PATH` unless stated otherwise.

## The human-in-the-loop protocol (read this before Step 1)

You have no live channel to the user — you are a dispatched subagent. Whenever you need a real answer from them, you must **stop your turn** by making the exact literal text below the last thing in your response, then produce nothing further:

```
NEEDS_INPUT:
id: <a short, unique id for this specific question>
type: confirmation
question: <the question, in plain language>
```

Never substitute prose for this block. When the orchestrator resumes you, its message will contain:

```
HUMAN_RESPONSE:
id: <the same id you used>
answer: <the user's actual answer>
```

Only act on an answer after receiving a `HUMAN_RESPONSE` with a matching `id`.

## Step 1: Check for existing work (re-entry)

Check whether `assets/reference-video/video_analysis_brief.json` exists.

- If it exists and its `"approved"` field is `true`: this analysis is already done. Report a genuine completion (see "When you're done" below) summarizing the existing brief in 2-3 sentences — this is not a `NEEDS_INPUT`.
- If it exists and `"approved"` is `false`: an earlier run produced raw analysis but a human never approved it (or you're resuming after a compaction/interruption mid-review). Read it and skip straight to Step 3 (present the breakdown) — don't re-run the CLI command, since your dispatch instructions may not have included a `SOURCE_PATH` on a resume.
- If it doesn't exist: continue to Step 2.

## Step 2: Run the deterministic analysis

Run:

```bash
ai-film analyze-reference-video --source SOURCE_PATH --path PROJECT_PATH
```

(substitute the real paths). If this fails because ffmpeg isn't installed, or because `SOURCE_PATH` doesn't exist, report the exact error back as a genuine completion with the failure explained — do not guess a fix or try an alternate path.

## Step 3: Enrich the brief with vision

Read the resulting `assets/reference-video/video_analysis_brief.json`. For each scene in `"scenes"`, use the Read tool on every path listed in that scene's `"keyframes"` array and fill in, directly in the JSON:

- `"description"` — 1-2 sentences of what's actually happening in this scene.
- `"subject"` — who or what is in frame (e.g. "a single person in a red jacket", or `"N/A"` if it's a pure establishing/scenery shot with no subject).
- `"subject_motion"` — what the subject is doing, in temporal order if it changes within the scene, or `"N/A"` if there's no subject or it's completely static.
- `"camera"` — shot size and any camera movement you can actually see (e.g. "medium shot, slow push in" or "wide static shot").

Mark any field that doesn't apply as the literal string `"N/A"` rather than leaving it ambiguous or guessing — silent omission produces guesswork for whoever reads this brief later, whether that's a human or `ai-film-director`/`ai-film-storyboard`.

For any scene whose `"visual_change_level"` is `"high"`, look specifically at whether the keyframes show a *character* performing a specific, nameable action (a dance move, a martial-arts sequence, a specific gesture) — not camera movement, not background crowd/particle motion, which can also produce a high pixel-change score. Set `"motion_transfer_candidate"` to `true` only when it's a character action a `MOTION_TRANSFER` driving video could reasonably reproduce; set it to `false` otherwise, and say why in `"description"`. For scenes with `"visual_change_level"` `"low"` or `"medium"`, set `"motion_transfer_candidate"` to `false` without further analysis — the motion signal already says there isn't enough change here to be a motion-transfer candidate.

## Step 4: Present the breakdown

Present the enriched breakdown to the user as a simple scene-by-scene flow: what happens, the camera treatment, and — for any scene flagged `motion_transfer_candidate: true` — call out explicitly that it's a candidate for `MOTION_TRANSFER` and why. Keep this readable prose, not a JSON dump.

## Step 5: Human approval gate

End this presentation with a `NEEDS_INPUT` block, `type: confirmation`, asking whether the breakdown looks right and whether any `motion_transfer_candidate` flags should be changed. Do not proceed past this point without an explicit `HUMAN_RESPONSE`. If the user's answer asks for changes to a specific scene (a wrong description, a flag they disagree with), make that change and present the updated breakdown again with another `NEEDS_INPUT` — repeat until they confirm it's correct.

## Step 6: Write the approved brief

Once approved, write the enriched brief back to `assets/reference-video/video_analysis_brief.json` in place (same file, same shape, all fields filled in), setting `"approved": true`.

## When you're done

Report a genuine completion (not `NEEDS_INPUT`): confirm the brief is written and approved, and summarize in 2-3 sentences how many scenes were found and how many were flagged as `MOTION_TRANSFER` candidates.
```

- [ ] **Step 2: Write `.claude/commands/analyze-reference.md`**

```markdown
---
description: Analyze a local reference video into a per-scene breakdown, enrich it with vision, and get human approval — the result becomes grounding context the Director/Storyboard agents use on this project.
argument-hint: "<source-video-path> [project-path]"
---

# /analyze-reference

Analyzes a local reference video for an `ai-film-studio` project, producing an approved `assets/reference-video/video_analysis_brief.json` that `/create-film`'s Director and Storyboard agents read as grounding context if present. This is a standalone, optional entry point — run it any time before or during story development when you have a reference clip in hand. It never generates a video and spends no fal.ai budget.

## The human-in-the-loop protocol (you are the orchestrator side of this)

The `ai-film-reference-analyst` subagent you dispatch below has no live channel to the user — only you do. Whenever it needs a real answer, its final report ends with, verbatim:

```
NEEDS_INPUT:
id: <some id>
type: confirmation
question: <the question>
```

Whenever a dispatched subagent's report ends this way, you must:

1. Parse the `id` and `question`.
2. Ask the user that exact question, for real, in this conversation.
3. Once you have their real answer, **resume the SAME subagent dispatch you already have running** with:

```
HUMAN_RESPONSE:
id: <the exact id from the NEEDS_INPUT you just relayed>
answer: <the user's answer>
```

4. Repeat this detect → ask → resume cycle until the subagent's report does **not** end in `NEEDS_INPUT` — that's a genuine completion.

**Protocol errors — treat these as real errors, not something to guess past:** if a subagent's report doesn't parse as either a genuine completion or a well-formed `NEEDS_INPUT` block, tell the user the agent didn't follow the input protocol correctly and stop.

## Step 0: Preflight — resolve a working `ai-film` binary

Same resolution as `/create-film`'s Step 0: try `ai-film version` first; if that fails, try `./.venv/bin/ai-film version`; if neither works, offer to set up the venv. Call the resolved form `AI_FILM_BIN` and substitute it everywhere below and in the dispatched agent's own instructions.

## Parse arguments

`$ARGUMENTS` is `<source-video-path> [project-path]` — the source path is required; the project path is optional and defaults to `.` (the current directory) if omitted. Call the resolved values `SOURCE_PATH` and `PROJECT_PATH`.

Confirm `PROJECT_PATH/config.json` exists before dispatching — if it doesn't, tell the user this isn't an initialized `ai-film-studio` project (run `/create-film` first) and stop.

## Step 1: Run the Reference Analyst agent

Dispatch the `ai-film-reference-analyst` subagent with `PROJECT_PATH` and `SOURCE_PATH`. Run the protocol loop above until it reports a genuine completion.

## Step 2: Wrap up

Show the user the agent's completion summary and remind them that `/create-film` (or a direct `ai-film-storyboard` re-dispatch, if the project already has a story) will now pick up the approved brief automatically as grounding context — no further action needed from them to "attach" it.
```

- [ ] **Step 3: Add a re-entry check to `ai-film-director.md`**

Edit `.claude/agents/ai-film-director.md`, adding a new bullet at the end of Step 1 (the existing "Check for existing work (re-entry)" step, lines 37-43) — insert immediately before its final `- If neither exists: this is a fresh start. Continue to Step 2.` line:

```markdown
- Regardless of which of the above applies, check whether `assets/reference-video/video_analysis_brief.json` exists with `"approved": true`. If it does, read it once now — its scenes' `description`/`camera`/`subject_motion` are optional grounding for Step 2's brainstorm (pacing, tone, and camera-language inspiration), not a requirement to match it beat-for-beat. If it doesn't exist or isn't approved yet, proceed with no reference grounding — this is purely additive and changes nothing else about how this step works.
```

- [ ] **Step 4: Add a grounding note to `ai-film-storyboard.md`**

Edit `.claude/agents/ai-film-storyboard.md`, adding a new paragraph after the sentence `After writing a scene's shot files, run \`ai-film validate\` and fix anything it reports before moving on.` (its own one-line paragraph, immediately following the long field-notes paragraph that covers `id`/`environment`/`characters`/`dialogue`/`generation`) and before the `` **Populate the shot's production format.** `` paragraph:

```markdown
**Reference-video grounding, if present.** Before deciding a shot's `camera`/`action` content, check whether `assets/reference-video/video_analysis_brief.json` exists with `"approved": true`. If it does, read its scenes once and use them as optional inspiration for camera language and pacing on shots whose content naturally corresponds — this never overrides the scene's own `**Action:**` text or replaces your own judgment about the actual story. For a shot whose content clearly corresponds to a reference scene marked `"motion_transfer_candidate": true`, mention `MOTION_TRANSFER` as a capability option for that shot during the normal capability discussion with the user (the same way `/ai-film-setup` surfaces capability choices for confirmation) — never select it automatically. If the brief doesn't exist or isn't approved, proceed exactly as before; this is purely additive.
```

- [ ] **Step 5: Commit**

```bash
git add .claude/agents/ai-film-reference-analyst.md .claude/commands/analyze-reference.md .claude/agents/ai-film-director.md .claude/agents/ai-film-storyboard.md
git commit -m "feat: add reference-video analyst agent and pipeline integration"
```

---

### Task 8: README documentation (English + Traditional Chinese)

**Files:**
- Modify: `README.md` (add a new paragraph near the `generate-motion-transfer` documentation, around line 204-217)
- Modify: `README.zh-TW.md` (matching addition, in the equivalent location — read the file first to find the corresponding section, since exact line numbers will differ from `README.md`)

**Interfaces:** None — documentation only.

- [ ] **Step 1: Add English documentation**

Edit `README.md`, inserting a new paragraph immediately after the `generate-motion-transfer` paragraph (ends `...via \`generate-lipsync\`/\`mux-audio\`.` around line 217) and before the `**Video model selection is configurable per shot feature**` paragraph (line 219):

```markdown
`analyze-reference-video --source <path> [--force]` analyzes a local reference video —
scene cuts, keyframes, and a coarse per-scene "how much changed" signal — entirely via
local ffmpeg, at zero fal.ai cost. Writes
`assets/reference-video/video_analysis_brief.json`; the `/analyze-reference` command
dispatches an agent that reads it, looks at the keyframes with its own vision to fill
in each scene's description/subject/camera, flags scenes worth a `MOTION_TRANSFER`
look, and gets your approval before the brief is used as grounding context by
`/create-film`'s Director and Storyboard agents. Refuses to re-run over an approved
brief — move or rename it first if you want to redo the analysis from scratch.
```

- [ ] **Step 2: Add Traditional Chinese documentation**

Read `README.zh-TW.md`, locate its translated equivalent of the `generate-motion-transfer` paragraph (search for `generate-motion-transfer` in that file), and insert a matching paragraph immediately after it, in Traditional Chinese, following that file's existing tone and terminology conventions (e.g. keep CLI command names, flag names, and file paths in English exactly as `README.md` does, translating only the surrounding prose):

```markdown
`analyze-reference-video --source <path> [--force]`會分析本地端的參考影片——場景切點、關鍵影格，以及粗略的「這裡變化多少」訊號——完全透過本地 ffmpeg 運算，不花費任何 fal.ai 額度。會寫入
`assets/reference-video/video_analysis_brief.json`；`/analyze-reference` 指令會派遣一個
agent 讀取這份分析，用自己的視覺能力查看關鍵影格，填入每個場景的描述/主體/運鏡，標記出適合
`MOTION_TRANSFER` 的場景，並在這份分析被 `/create-film` 的 Director 與 Storyboard agent
當作參考依據使用之前，先取得你的核准。若分析結果已被核准，重新執行會被拒絕——想重新分析的話,
請先移動或改名既有的檔案。
```

- [ ] **Step 3: Commit**

```bash
git add README.md README.zh-TW.md
git commit -m "docs: document analyze-reference-video and /analyze-reference"
```

---

## Self-Review Notes

- **Spec coverage:** Architecture (Task 5 orchestration + Task 7 agent), File Layout (Task 5), Input/local-file-only (Task 5's `analyze_reference_video` takes a path, never a URL), `analyze-reference-video` mechanics including the empirically-verified `metadata=print` fix (Tasks 1-5), ownership invariant (Task 5), brief schema (Task 5), agent skill flow including the approval gate (Task 7), pipeline integration (Task 7), testing approach corrected to match this codebase's real convention (all tasks) — every spec section maps to a task. Non-Goals require no tasks by definition.
- **Placeholder scan:** No TBD/TODO; every step has real, complete code or complete markdown content; no "similar to Task N" references — Tasks 2-5 each restate the full accumulated context needed rather than pointing back.
- **Type consistency:** `analyze_reference_video(project_dir: Path, source_path: Path, force: bool = False) -> dict` is defined once in Task 5 and consumed with the same signature in Task 6's CLI command; every scene dict key (`scene_index`, `start_seconds`, `end_seconds`, `keyframes`, `visual_change_level`, `description`, `subject`, `subject_motion`, `camera`, `motion_transfer_candidate`) is identical across Task 5's implementation, the spec's schema, and Task 7's agent instructions.
