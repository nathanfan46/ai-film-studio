# Media Review Layer — Design

**Status:** Draft, pending user review
**Date:** 2026-08-27

## 1. Overview

The `ai_film` engine can generate video (`generate-video`), dialogue voice
(`generate-voice`), sound effects (`generate-sfx`), and music
(`generate-music`) per shot, and the human-interaction-model spec already
gave images a full review loop (candidate storage, a static HTML gallery,
edit/regenerate, lock-in). Video and audio have none of that: each
`generate-*` call writes exactly one artifact into `shot.json`, a human can
watch/listen to it only by opening the file manually, and there is no
record of what feedback was given or whether it was addressed. Every
regeneration silently overwrites the previous artifact.

This spec closes that gap — but not by porting the image candidate system
(N divergent candidates, pick-one-and-lock) onto video/audio wholesale.
That system fits *character/environment design*, where you generate several
different takes and choose your favorite. Shot video/audio review is a
different shape: dailies review. You watch v1, say what's wrong, get v2,
say what's wrong with that, and so on — a **sequential revision** history,
not a set of simultaneous alternatives. This spec builds the narrower thing
that shape actually needs: version history on the existing single-artifact
model, a feedback log, a static review page to watch/listen from, and one
cheap non-generative fix path (audio offset) so a timing complaint doesn't
have to trigger a paid regeneration.

This spec was explicitly scoped down from a broader proposal during design
review. Deliberately **out** of this spec: an editable/draggable timeline
UI, waveform-based audio editing, Whisper transcription, a rigid enforced
feedback taxonomy, and any server/API. All of those remain real options —
see §10 — but none of them are needed to answer the actual want driving
this work: *watch the shot, hear the audio, say what's wrong in chat, get
a fix.*

### Relationship to prior specs

The human-interaction-model spec built the image candidate loop. The
agent-layer spec built the Director/Character/Storyboard agents on top of
it, and explicitly named this exact gap in its own Non-Goals and §10 Future
Extensions: *"Video candidate review/refinement, once the engine gains
video candidate storage and an edit/regenerate loop for clips."* This spec
is that follow-on — at the **engine** level only, exactly mirroring how
agent-layer-design.md followed human-interaction-model-design.md. It
deliberately uses "revision history," not "candidate storage" — see above
for why the shape differs from what that older sentence assumed. A future
**Media Review Agent** spec (prompt/agent layer, zero engine changes) would
sit on top of this one exactly the way agent-layer-design.md sits on top of
human-interaction-model-design.md; it is not part of this spec (§10).

### Goals

- Every regeneration of a shot's `image`/`video`/`voice`/`sfx`/`music` stage
  keeps the artifact it replaces, as a numbered version, instead of
  discarding it.
- A per-shot feedback log that records what a human said was wrong,
  timeline-aware from day one: a point in time, a time range, or a
  track-relative note (e.g. "voice starts early"), tagged to which track it's
  about.
- A static, no-server HTML review page per shot — `<video>`/`<audio>`
  elements a human actually plays, a timeline ruler plotting the feedback
  log's entries against real timestamps, a live time readout wired to
  playback, and per-track version history — reusing the same
  build-a-static-file-and-open-it pattern `review_gallery.py` already
  established for images.
- One cheap, non-generative fix: nudge an audio track's start time via
  `ffmpeg`, for the common "it's a beat early/late" complaint, without
  spending on a full regeneration.
- CLI commands so an agent (or a human) can record feedback, resolve it,
  apply the cheap audio fix, and rebuild/reopen the review page — all
  reachable without new provider calls beyond what already exists.

### Non-Goals (this spec)

- **No editable/draggable timeline.** Tracks and the timeline ruler are
  playback and reference only. Nothing in this spec lets a human drag a
  clip, trim, or scrub-and-commit from the browser.
- **No divergent candidate exploration for video/audio.** One current
  artifact per stage per shot, plus its history — never N simultaneous
  options to choose between (that's what images already have, and it isn't
  what dailies review needs).
- **No Whisper or any transcription.** Feedback timestamps come from the
  human describing roughly when something happens; the engine does not
  align audio to text.
- **No enforced feedback taxonomy.** `target` is one of five plain values
  (§3.2) so the review page can group and color entries — it is not a
  classifier, a validation ontology, or something the engine infers on its
  own. What note goes under what target is a judgment call the human or
  agent makes when calling `add-feedback`.
- **No server, API, or new runtime dependency.** The review page is a
  static file opened via `file://`, exactly like `review_gallery.py`
  today. `ffmpeg` is the one external tool this spec relies on, and it's
  already a hard dependency of `render.py`.
- **No Media Review Agent.** Turning a human's chat message into a
  well-formed `add-feedback` call, and turning a feedback log into a
  concrete regeneration/fix decision, is prompt-layer work for a future
  spec (§10) — this spec only builds the engine primitives that agent
  would call.
- **No changes to `render.py`'s manifest/preflight.** Final-cut audio
  assembly stays manual and untouched, same as today.

## 2. Architecture

```
                         generate-video / generate-voice /
                         generate-sfx / generate-music / generate-image
                                        │
                                        ▼
                          run_generation_stage()  (extended, §4.1)
                                        │  on regeneration: pushes the
                                        │  outgoing artifact into history,
                                        │  bumps version
                                        ▼
                              shot.json  generation.<stage>
                              { version, artifact, history: [...] }

                         add-feedback --shot X --target video
                              --at 3.29 --note "..."
                                        │
                                        ▼
                              feedback_store.py  (new, §4.2)
                                        │  writes/reads
                                        ▼
                              03_shots/<id>.feedback.json

              apply-audio-offset --shot X --track voice --offset-ms 400
                                        │
                                        ▼
                              audio_fix.py  (new, §4.3)
                                        │  shells out to ffmpeg,
                                        │  then calls the same
                                        │  history/version bump as above
                                        ▼
                              shot.json  generation.voice  (new version)

                         review-media --shot X
                                        │
                                        ▼
                              media_review.py  (new, §5)
                                        │  reads shot.json + feedback.json
                                        ▼
                              07_review/<id>.html  +  waveform PNGs
                                        │
                                        ▼
                                 open_in_browser()  (reused, unchanged)
```

`review_gallery.py`, `candidate_store.py`, `approval.py`, and the cost gate
in `generation_service.py` are **unchanged** except for the one additive
extension in §4.1. No existing CLI command's signature changes; only new
commands are added.

## 3. Data Model

### 3.1 `shot.json` — extended generation stage

Every stage under `generation` (`image`, `video`, `voice`, `sfx`, `music`)
gains two new, optional fields. `schema.py`'s `GENERATION_STAGE_SCHEMA`
adds them as non-required properties, so every shot.json written before
this spec remains valid without migration:

```json
{
  "status": "completed",
  "provider": "fal",
  "model": "veo-3",
  "version": 3,
  "job": {"provider": "fal", "id": "..."},
  "inputs": [],
  "artifact": {
    "path": "05_video/S02_SH03.mp4",
    "size_bytes": 812344,
    "sha256": null,
    "duration_seconds": 6.0
  },
  "history": [
    {
      "version": 1,
      "provider": "fal",
      "model": "veo-3",
      "artifact": {"path": "05_video/history/S02_SH03_v1.mp4", "size_bytes": 790112, "sha256": null, "duration_seconds": 6.0},
      "completed_at": "2026-08-25T09:10:00Z",
      "superseded_at": "2026-08-26T14:02:00Z",
      "superseded_reason": "regenerate"
    },
    {
      "version": 2,
      "provider": "fal",
      "model": "veo-3",
      "artifact": {"path": "05_video/history/S02_SH03_v2.mp4", "size_bytes": 801200, "sha256": null, "duration_seconds": 6.0},
      "completed_at": "2026-08-26T14:02:00Z",
      "superseded_at": "2026-08-27T09:14:00Z",
      "superseded_reason": "regenerate"
    }
  ],
  "attempts": 1,
  "created_at": "2026-08-25T09:00:00Z",
  "started_at": "2026-08-25T09:00:05Z",
  "completed_at": "2026-08-27T09:14:00Z"
}
```

`version` starts at `1` the first time a stage completes and increments by
1 every time that stage's artifact is replaced — whether by a full
regeneration (`generate-* --force`) or by the cheap audio-offset fix
(§4.3). `superseded_reason` is one of `"regenerate"` or `"audio_offset"`,
recorded by whichever code path performed the replacement. A stage with no
`history` key (or an absent `version`) means it has never been
regenerated — treat that as `version: 1` implicitly; the builder in §5
handles this without requiring a migration pass over old shot files.

Old artifact files move rather than get deleted: on replacement, the file
at the outgoing `artifact.path` is renamed into a sibling `history/`
directory next to the stage's normal output directory (`05_video/history/`,
`06_audio/dialogue/history/`, `06_audio/sfx/history/`,
`06_audio/music/history/`, `04_storyboard/history/`), suffixed
`_v<N>` before the extension. Nothing is ever deleted by this spec.

### 3.2 Feedback log — `03_shots/<id>.feedback.json`

One file per shot, sibling to `03_shots/<id>.json`, absent until the first
`add-feedback` call (mirrors `candidates.json`'s "absent means empty"
convention in `candidate_store.py`):

```json
{
  "shot_id": "S02_SH03",
  "entries": [
    {
      "id": "FB-001",
      "target": "video",
      "at": 3.29,
      "range": null,
      "note": "Mara's expression is too calm here — she should look like she's bracing herself.",
      "status": "open",
      "created_at": "2026-08-27T09:20:00Z",
      "resolved_at": null,
      "resolution": null
    },
    {
      "id": "FB-002",
      "target": "voice",
      "at": null,
      "range": null,
      "note": "Voice starts about 400ms too early relative to her mouth movement.",
      "status": "resolved",
      "created_at": "2026-08-26T15:00:00Z",
      "resolved_at": "2026-08-26T15:04:00Z",
      "resolution": "applied +400ms audio offset (v3)"
    }
  ]
}
```

`target` is one of exactly five values: `"video"`, `"voice"`, `"sfx"`,
`"music"`, `"sync"` — `"sync"` for cross-track timing complaints that
aren't really "a problem with the video" or "a problem with the audio" on
their own (like FB-002 above; note it still carries `at: null` since the
complaint is about relative offset, not an absolute point in the clip).
Exactly one of `at` (a single timestamp in seconds) or `range` (`{"start":
..., "end": ...}`, both in seconds) may be set; both `null` is valid (a
general note with no specific moment, e.g. "the overall pacing feels
slow"). `status` is `"open"` or `"resolved"`; `resolution` is a free-text
note, set only when resolving.

## 4. New Engine Modules

### 4.1 `run_generation_stage` — history/version extension

In `services/generation_service.py`, `run_generation_stage` currently
overwrites `shot["generation"][stage]` outright on every successful run.
Change: before building the new stage dict, if `stage_data` (the *existing*
value, captured at the top of the function) has a `status` of `"completed"`
and an `artifact`, move its file into that stage's `history/`
subdirectory (§3.1 naming), and carry forward `stage_data.get("history",
[])` plus one new entry summarizing what was just superseded. The new
stage dict's `version` is `stage_data.get("version", 0) + 1` (so the very
first completion produces `version: 1`, matching §3.1). This is the only
change to existing engine code in this spec; it is purely additive to the
returned/stored dict shape, so no existing caller (`generate_image`,
`generate_video`, `generate_voice`, `generate_sfx`, `generate_music`, all
five of which already call this shared function) needs any change, and no
existing shot.json that predates this field needs a migration.

### 4.2 `feedback_store.py` (new file, `src/ai_film/feedback_store.py`)

Mirrors `candidate_store.py`'s style: plain functions over a JSON file,
atomic writes via tempfile + `os.replace`.

```python
VALID_TARGETS = ("video", "voice", "sfx", "music", "sync")

def feedback_path(project_dir: Path, shot_id: str) -> Path: ...
def load_feedback(project_dir: Path, shot_id: str) -> dict: ...
    # {"shot_id": shot_id, "entries": []} if the file doesn't exist yet
def save_feedback(project_dir: Path, shot_id: str, data: dict) -> None: ...
    # atomic write, same tempfile+os.replace pattern as save_candidate_set

def add_feedback_entry(
    project_dir: Path,
    shot_id: str,
    target: str,
    note: str,
    at: float | None = None,
    range_start: float | None = None,
    range_end: float | None = None,
) -> dict:
    # raises ValueError if target not in VALID_TARGETS
    # raises ValueError if both `at` and a range are given
    # raises ValueError if exactly one of range_start/range_end is given
    # raises ValueError if any given timestamp is negative
    # assigns id "FB-{n:03d}", n = 1 + count of existing entries
    # returns the new entry dict

def resolve_feedback_entry(
    project_dir: Path,
    shot_id: str,
    feedback_id: str,
    resolution: str | None = None,
) -> dict:
    # raises ValueError if feedback_id not found
    # sets status="resolved", resolved_at=now, resolution=resolution
    # returns the updated entry dict
```

### 4.3 `audio_fix.py` (new file, `src/ai_film/audio_fix.py`)

The one cheap, non-generative fix this spec provides. No cost gate applies
— it never calls a paid provider, so `is_approved` is not checked.

```python
AUDIO_STAGES = ("voice", "sfx", "music")

def apply_audio_offset(
    project_dir: Path,
    shot_path: Path,
    track: str,
    offset_ms: float,
) -> dict:
    # raises ValueError if track not in AUDIO_STAGES
    # raises ValueError if the shot's generation[track].status != "completed"
    #   (nothing to offset)
    # raises RuntimeError if ffmpeg is not on PATH (shutil.which, same
    #   check render.py already uses)
    #
    # Positive offset_ms delays the track (fixes "starts too early"):
    #   ffmpeg -y -i <in> -af "adelay=<offset_ms>:all=1" <out>
    # Negative offset_ms advances the track (fixes "starts too late"),
    # trimming |offset_ms| from the head:
    #   ffmpeg -y -ss <abs(offset_ms)/1000> -i <in> <out>
    #
    # Writes the new file into the stage's normal output directory
    # (06_audio/<dialogue|sfx|music>/<shot_id>.wav), moves the outgoing
    # file into history/ and bumps version exactly like §4.1
    # (superseded_reason: "audio_offset"), and returns the updated
    # generation[track] dict.
```

`track="voice"` maps to `06_audio/dialogue/`, matching the existing
directory the CLI's `generate-voice` already writes into (see
`generate_voice_cmd` in `cli.py`) — `sfx`/`music` map to their own existing
directories directly.

## 5. The Static Review Page

### 5.1 `media_review.py` (new file, `src/ai_film/media_review.py`)

```python
def build_media_review(project_dir: Path, shot_id: str) -> Path:
    # raises ValueError if 03_shots/<shot_id>.json doesn't exist
    # loads shot.json and (if present) <shot_id>.feedback.json
    # writes 07_review/<shot_id>.html (project_dir / "07_review", a new
    #   top-level directory added to project.py's PROJECT_DIRS, created by
    #   `ai-film init` from that point on and mkdir'd here for existing
    #   projects same as review_gallery.py already does for its own dir)
    # if ffmpeg is on PATH, also writes one waveform PNG per audio stage
    #   that has a completed artifact, via ffmpeg's showwavespic filter:
    #     ffmpeg -y -i <track> -filter_complex \
    #       "showwavespic=s=640x60:colors=#6fbdb0" -frames:v 1 <out>.png
    #   into 07_review/<shot_id>_<track>_waveform.png; if ffmpeg is
    #   missing, the page renders without waveform images (an audio row
    #   with just the <audio> element and no visual), never an error
    # returns the path to the written HTML file
```

Reuses `review_gallery.open_in_browser` unchanged (it takes any HTML
path and cache-busts it) — no new browser-opening logic.

### 5.2 Page content and behavior

This section is the acceptance spec for the HTML `build_media_review`
produces — refining the visual mockup built during design review into
concrete, buildable behavior. Two deliberate departures from that mockup,
both because the real data model is more precise than the mockup assumed:

- **Per-track version indicators, not one shared version-tab strip.**
  `video`/`voice`/`sfx`/`music` each version independently (§3.1) — fixing
  a voice-timing issue doesn't touch video's version number. Each track
  row shows its own `v<N>` next to its name, with a "N earlier version(s)"
  disclosure that lists prior versions as plain playable rows (their own
  `<audio>`/`<video>` element pointed at the `history[]` path) — not a
  single unified tab strip implying one lockstep version across the whole
  shot.
- **Timeline markers are generated from real feedback entries**, not
  hand-placed illustrative ones: one flag per feedback entry that has an
  `at` or a `range`, positioned by `at / duration_seconds` (or the
  midpoint of `range` for range entries, with a wider highlighted band
  along the axis spanning `range.start`–`range.end`), colored by `status`
  (open vs. resolved — semantic color, not the page's accent color),
  labeled with the timestamp and a short prefix of `note`. Entries with
  neither `at` nor `range` (general notes) are listed underneath the
  ruler instead of plotted on it.

Required elements, all static HTML plus small inline `<script>` blocks (no
build step, no external JS, matching `review_gallery.py`'s zero-dependency
approach):

1. **Heading.** Shot id, one-line action summary (from `shot["action"]`),
   a status pill, and the explicit line **"Review only — changes are made
   by the agent, not by editing here."**
2. **Video.** A real `<video controls>` pointed at the current video
   artifact (relative path from `07_review/` back through the project
   root, e.g. `../05_video/S02_SH03.mp4`), or a placeholder panel reading
   "video not generated for this shot" if `generation.video.status !=
   "completed"`. A live time readout (`current / duration`, tabular-nums
   monospace) wired to the video's `timeupdate` event.
3. **Timeline ruler.** Ticks at 1-second intervals up to
   `duration_seconds`, a moving playhead cursor synced to the video's
   `timeupdate` event, and the feedback flags from §5.2 above. Clicking
   the ruler seeks the video (`video.currentTime = clickFraction *
   duration`) — this is the one interactive affordance beyond play/pause,
   and it's playback navigation, not editing.
4. **Media review tracks.** One row per `voice`/`music`/`sfx`: a real
   `<audio controls>` pointed at the current artifact (or "not generated
   for this shot" if absent), the waveform PNG as a background image if
   one was generated, the per-track version indicator from §5.2, and a
   label reinforcing playback-only framing (matches the mockup's "Playback
   only — describe timing issues in chat" line).
5. **How to give feedback.** Static instructional copy plus three worked
   examples in the three timestamp shapes the data model supports: a point
   (`3.3s`), a range (`3.3–4.1s`), and a relative offset (`~400ms`) — same
   content as the reviewed mockup.
6. **Open/resolved feedback list.** Every entry from the feedback log,
   grouped by status, each showing its `id`, `target`, timestamp (or
   "general"), and `note` — so a human re-opening the page later sees
   exactly what's already been said, not just what's newly plotted on the
   ruler.

## 6. CLI Commands

All added to `src/ai_film/cli.py`, following the existing command
conventions exactly (typer options, `--path` defaulting to
`DEFAULT_PROJECT_PATH`, `ValueError`/`RuntimeError` caught and surfaced as
`typer.echo(..., err=True)` + `typer.Exit(code=1)`, matching every existing
command's error-handling shape):

```
ai-film add-feedback --shot <id> --target video|voice|sfx|music|sync
    --note "<text>" [--at <seconds>] [--range-start <seconds> --range-end <seconds>]
    [--path <project>]
    # calls feedback_store.add_feedback_entry; echoes the new entry's id

ai-film resolve-feedback --shot <id> --id <FB-NNN>
    [--resolution "<text>"] [--path <project>]
    # calls feedback_store.resolve_feedback_entry; echoes confirmation

ai-film apply-audio-offset --shot <id> --track voice|sfx|music
    --offset-ms <float> [--path <project>]
    # calls audio_fix.apply_audio_offset; echoes the new version number

ai-film review-media --shot <id> [--path <project>]
    # calls media_review.build_media_review, then open_in_browser
    # (identical pattern to the existing `review` command)
```

No existing command's flags or behavior change.

## 7. Cost / Approval Handling

Unchanged from the existing engine, and this is worth stating explicitly
because it means **no cost-gate code changes are needed anywhere in this
spec**: `run_generation_stage`'s approval check (`is_approved(project_dir,
scope, shot_id)`, scope from `_SCOPE_BY_STAGE`) is called exactly as it is
today for every `--force` regeneration this spec triggers — §4.1 only
changes what happens *after* that check passes (moving the outgoing
artifact into `history/`, bumping `version`), never the check itself.
Whether a given shot's existing approval record actually covers a
particular stage's `--force` call is existing behavior this spec neither
changes nor needs to resolve — if that call was already reachable before
this spec, it remains exactly as reachable after it, with no new gate code
required. `apply-audio-offset` (§4.3) needs no approval at all, since it
never calls a provider.

## 8. Error Handling and Edge Cases

- **Regenerating a stage with no prior artifact.** `stage_data.get("status")
  != "completed"` at the top of `run_generation_stage` — nothing to move
  into history, `version` starts at 1 as today's behavior implicitly does.
- **`apply-audio-offset` on a stage that's never completed.** Raises
  `ValueError` before touching ffmpeg or the filesystem — same
  fail-before-side-effect pattern `run_generation_stage` already uses for
  its own cost-gate check.
- **`ffmpeg` missing.** `apply_audio_offset` raises `RuntimeError` (same
  message style as `render.py`'s existing check); `build_media_review`
  degrades gracefully instead — a review page missing waveform images is
  still useful, a failed cheap-fix silently producing no file would not be.
- **`review-media` on a shot with nothing generated yet.** Not an error —
  every track renders its "not generated for this shot" placeholder; the
  page is still valid to open (useful early, before any generation has
  run, to confirm the shot's basic metadata).
- **Resolving a feedback id that doesn't exist, or targeting an invalid
  `target`.** `ValueError`, surfaced by the CLI exactly like every other
  validation error in this codebase (`select_candidate`'s "no candidate
  with id" is the direct precedent).
- **Both `--at` and a `--range-start`/`--range-end` pair given to
  `add-feedback`.** `ValueError` — the data model (§3.2) only allows one
  or neither, never both.

## 9. Testing Strategy

Standard `pytest` coverage, following this codebase's existing test style
(`tests/test_candidate_store.py`, `tests/test_review_gallery.py` are the
direct precedents for the new store and page-builder modules
respectively):

- `feedback_store.py`: add/load/save round-trip, id increment, target
  validation, at/range mutual-exclusivity validation, resolve updates the
  right entry and leaves others untouched, atomic write survives a
  simulated crash mid-write (same pattern `candidate_store.py`'s tests
  presumably already exercise for `save_candidate_set`).
- `run_generation_stage` extension: first completion produces `version:
  1` and no `history`; a forced regeneration produces `version: 2`, a
  one-entry `history` list, and the outgoing file physically moved into
  `history/`; existing tests for `generate_image`/`generate_video`/etc.
  continue to pass unmodified (the change is additive to the returned
  dict, not a breaking shape change).
- `audio_fix.py`: offset application against a short synthetic wav
  (generated in the test via `ffmpeg -f lavfi -i anullsrc=... -t 1
  fixture.wav`, avoiding a checked-in binary fixture), asserting the
  output file exists, the stage's version bumped, and the old file
  landed in `history/`; a stage-not-completed shot raises before any
  ffmpeg call (verifiable without ffmpeg installed, by asserting no
  subprocess was invoked).
- `media_review.py`: the built HTML contains the video/audio source
  paths, contains one timeline flag per feedback entry with an `at` or
  `range`, omits flags for general notes (asserting those appear in the
  list section instead), and renders "not generated for this shot" for
  any stage without a completed artifact — direct structural assertions
  on the returned HTML string, same style as
  `test_review_gallery.py`'s existing assertions (`"grid-template-columns"
  in content`, etc.).
- CLI: one test per new command verifying it's wired to the right service
  function and surfaces `ValueError`/`RuntimeError` as exit code 1 with
  the message on stderr — matching the existing `cli.py` test coverage
  pattern for `select-candidate`/`edit-candidate`.

## 10. Future Extensions (explicitly out of scope here)

- **A Media Review Agent** (prompt/agent layer, zero further engine
  changes) that reads a human's chat feedback, calls `add-feedback` with a
  well-chosen `target`/`at`/`range`, decides whether the fix is a cheap
  `apply-audio-offset` or a targeted `--force` regeneration (possibly
  after adjusting `shot.json` fields like `dialogue.text` or `visual`
  first), and calls `resolve-feedback` once done — the direct analogue of
  how agent-layer-design.md sits on top of human-interaction-model-design.md.
- **An editable/interactive timeline** (drag, trim, scrub-and-commit),
  once real usage across many shots surfaces a concrete case the
  chat-plus-static-page loop can't handle well.
- **Whisper-based transcription**, to auto-suggest timestamps instead of
  relying on the human's own estimate.
- **A formal feedback taxonomy/classifier**, if `target`'s five plain
  values stop being expressive enough for real usage patterns.
- **Divergent (N-way) exploration for video**, if a real use case emerges
  where trying several different takes of a shot's video — not
  sequentially fixing one — turns out to be what's actually wanted.
- **Audio assembly into `render.py`'s final cut.** Voice/sfx/music tracks
  remain excluded from `build_manifest`'s output; wiring them in is a
  separate concern from reviewing them.
