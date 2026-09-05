# Reference Video Analysis — Design

## Goal

Let a user hand the studio a reference video ("make something with this
shot structure / this pacing / this character motion") and get, without
spending any fal.ai budget, a structured breakdown of its scenes — which
ones are static shots, which ones involve real subject motion — as
grounding context for the existing story/storyboard flow. Where a scene
shows real character motion worth reproducing precisely, the breakdown
flags it as a `MOTION_TRANSFER` candidate for the human to decide on
during storyboarding.

## Motivation

Investigated because `OpenMontage` (a separate, much larger open-source
project — see `/Users/nathan/Projects/OpenMontage`) ships a working
version of this. Reading its actual source (not its README) turned up
one directly reusable idea and several that don't fit this codebase:

- `tools/analysis/video_analyzer.py` — a **zero-API-key, local** tool:
  ffmpeg for scene cuts, keyframes, and duration; OpenCV optical flow to
  classify each scene's motion. Produces a JSON brief with vision-only
  fields left blank.
- `skills/meta/video-reference-analyst.md` — the agent layer that reads
  that brief, looks at the keyframes with its own vision to fill the
  blanks, and drives a conversation (creative proposals, cost tables,
  mandatory sample production, multi-stage pipeline handoff).

The clean idea worth keeping: **deterministic local extraction, then
agent vision for interpretation** — the same split this codebase already
uses everywhere else (a `providers/fal/*` call does the deterministic
work; a `.claude/agents/*.md` skill drives the conversation around it).
Everything downstream of that split in OpenMontage (creative
differentiation proposals, provider cost comparisons, mandatory sample
clips, a 12-stage pipeline manifest) solves a broader problem — "produce
an entire video from one sentence" — that this project doesn't have.
Adopting it wholesale would mean adopting OpenMontage's scope, not its
idea.

## Non-Goals (explicitly deferred or rejected)

| Item | Decision | Why |
|---|---|---|
| URL download (YouTube/TikTok/etc.) via yt-dlp | Deferred | New heavy dependency + platform-ToS fragility, for a feature the user already offered to self-serve (download the file themselves, point `--source` at it). |
| OpenCV optical-flow motion classification | Rejected in favor of an ffmpeg-only heuristic | This project has exactly 4 dependencies (`typer`, `jsonschema`, `requests`, `python-dotenv`) and already does every other video operation by shelling out to ffmpeg (`providers/fal/video.py`). Adding `opencv-python`+`numpy` for one feature breaks that pattern for a signal this design only needs as a coarse "look closer here" flag, not a scientific measurement. |
| Backlot (production dashboard) | Deferred to its own future spec | Architecturally unrelated (a standalone read-only web server vs. this feature's ffmpeg CLI + agent skill). Bundling them would mean this spec covers two independent subsystems. The user's own request emphasized reference-video analysis specifically. |
| Multi-variant creative proposals, cost-comparison tables, mandatory sample-first production, a formal pipeline manifest | Rejected | Solves OpenMontage's broader "one sentence → full video" problem. This project already has its own approval gates (candidate loop, cost gate before generation) — this feature's job is to seed the *existing* story/storyboard flow with grounding, not replace it. |
| Automatic capability selection (motion signal → auto-pick MOTION_TRANSFER) | Rejected | An ffmpeg pixel-delta signal cannot tell character motion from a camera whip-pan or background crowd movement. The signal is a recommendation surfaced during storyboarding for the human to accept or reject — never an automatic decision. See "Motion signal is advisory, not authoritative" below. |

## Architecture

Two layers, matching the codebase's existing engine/agent split:

```
assets/reference-video/source.<ext>  (user-provided, local file)
              │
              ▼
   analyze-reference-video   ← new CLI command, local ffmpeg only, $0 cost
              │
              ▼
   assets/reference-video/video_analysis_brief.json   (scenes, keyframes,
              │                                         visual_change_level)
              ▼
   ai-film-reference-analyst   ← new agent skill, reads brief + views
              │                  keyframes, fills in description/camera/
              │                  subject/motion, decides motion_transfer_
              │                  candidate per scene
              ▼
   Human approval (presented breakdown, same approve/revise loop as
              │    every other candidate/storyboard decision in this project)
              ▼
   Approved brief becomes grounding context for ai-film-director /
   ai-film-storyboard — those agents read it when present and use it to
   seed scene/shot content and to suggest MOTION_TRANSFER for shots
   whose analysis says so, same as any other capability choice: proposed,
   never silently applied.
```

## File Layout

Reference-video assets live alongside the project's existing
user-supplied assets, following the precedent already set by
`assets/reference-images/` and `assets/fonts/` (see `project.py`'s
`_PROJECT_DIRS`):

```
assets/reference-video/
  source.<ext>                  # copy of the user's file, original extension kept
  keyframes/
    scene00_start.jpg
    scene00_mid.jpg
    scene01_start.jpg
    ...
  video_analysis_brief.json     # engine output, then agent-enriched in place
```

`analyze-reference-video` copies the source file into this directory
(never reads it in place from an arbitrary path) so the project directory
stays self-contained and portable — the same reason character/environment
reference images are copied into `assets/reference-images/` rather than
referenced by absolute path.

## Input: local file only (v1)

```
ai-film analyze-reference-video --source /path/to/clip.mp4 --path <project>
```

`--source` must be an existing local video file. No URL support in v1.
If the user wants to analyze a YouTube/TikTok/etc. video, they download
it themselves first (browser extension, `yt-dlp` run by hand, whatever
they already use) and point `--source` at the resulting file — which is
exactly what the user offered when this was discussed. This keeps the
core install free of a download dependency and avoids the whole class of
platform-download breakage (rate limits, signed URLs, ToS changes) that
has nothing to do with this project's actual job. If URL support is ever
wanted, it is a self-contained follow-up: an optional
`[project.optional-dependencies] reference-video = ["yt-dlp>=..."]` extra
and a thin wrapper in front of the same `--source` flow — not a redesign.

## `analyze-reference-video`: what it actually does

New module `src/ai_film/reference_analysis.py`, following the existing
pattern of standalone local-tool modules (`render.py`,
`video_diagnostics.py`, `video_fix.py`) — no provider abstraction, no
`Capability` enum entry, because there is no provider to choose between;
this is pure local ffmpeg, same as `render`.

1. **Copy** the source file into `assets/reference-video/source.<ext>`.
2. **Probe** duration via `ffprobe` (same pattern as
   `_probe_audio_duration` / `_get_duration` elsewhere in this codebase).
3. **Scene detection**, via ffmpeg's own scene-change filter:
   ```
   ffmpeg -i source.mp4 -vf "select='gt(scene,0.35)',showinfo" -f null - 2>&1
   ```
   `showinfo` logs one line per selected frame with its timestamp; those
   timestamps are the scene-cut boundaries. `0.35` is the same
   threshold ffmpeg's own documentation recommends as a general-purpose
   default.
4. **Motion signal, reusing the same filter's raw score** — the `scene`
   expression computes a per-frame change score (0.0-1.0) between
   consecutive frames *before* thresholding it into a cut decision, and
   attaches it as frame metadata (`lavfi.scene_score`). `showinfo` does
   **not** print this value (verified empirically — confirmed by
   building a synthetic 2-scene test clip and checking real ffmpeg
   9.0.1 output); it must be read via the `metadata` filter's print
   mode instead:
   ```
   ffmpeg -i source.mp4 -vf "select='gte(scene,0)',metadata=print:key=lavfi.scene_score" -f null -
   ```
   This prints one two-line block per frame:
   ```
   frame:20   pts:20480   pts_time:2
   lavfi.scene_score=0.813104
   ```
   (`pts_time` and `scene_score` are on separate lines — pair them by
   position when parsing.) The frame at each scene's own start
   boundary reflects the cut itself (confirmed empirically to spike
   near 1.0 for a hard cut) and must be excluded from that scene's own
   average; average the remaining *interior* frames' scores to get a
   per-scene mean change score:
   - `< 0.02` → `visual_change_level: "low"` (static image / locked-off
     shot with negligible movement)
   - `0.02 – 0.08` → `visual_change_level: "medium"` (pan/zoom/push,
     ambient movement — the OpenMontage precedent's "animated_still")
   - `> 0.08` → `visual_change_level: "high"` (something is moving a lot
     — could be a character, could be a camera whip-pan or crowd; the
     signal cannot tell)

   These thresholds are heuristic starting points, not measured
   constants — call this out in code as a comment and expect them to be
   revisited once real reference footage has been run through it.
5. **Keyframe extraction**: for each scene, extract the frame at
   `start + 0.1s` and, for scenes longer than 3s, the midpoint too —
   same timestamp logic as OpenMontage's `_compute_keyframe_timestamps`,
   via `ffmpeg -ss <t> -i source.mp4 -frames:v 1 keyframes/sceneNN_start.jpg`.
6. **Write** `video_analysis_brief.json` (schema below).

No transcript/captions extraction in v1 — this project's shots don't
currently model dialogue timing from an external source, and adding
`faster-whisper` for a field nothing downstream reads yet is exactly the
kind of speculative addition this design is trying to avoid. Add it
later, as its own small change, if a real project needs it.

**Audio/BGM is a deliberate future extension point, not a permanent
exclusion.** A reference video's pacing and camera language are often
tied to its music (a hero-orbit reveal timed to a beat drop). Nothing in
this schema or the template concept (see
`docs/superpowers/specs/2026-09-04-template-library-design.md`) blocks
adding an audio-characteristics field later — it's left out of v1 only
because no existing shot.json or project field consumes it yet, and
adding one speculatively, before anything downstream reads it, would be
exactly the mistake this design is otherwise avoiding.

### Motion signal is advisory, not authoritative

`visual_change_level: "high"` means "ffmpeg detected a lot of pixel
change here" — nothing more. It is deliberately not called
`motion_type: "motion_clip"` (OpenMontage's naming), because that name
implies a conclusion ("this is character motion") the signal cannot
support. A whip-pan across a static room produces the same high score as
a character performing choreography. The engine layer's only job is to
tell the agent layer *where to look closely*; the agent layer's vision,
looking at the actual keyframes, is what decides whether the motion is a
character worth reproducing via `MOTION_TRANSFER`. This distinction is
load-bearing — see the rejected "automatic capability selection" row
above.

## `video_analysis_brief.json` schema

```json
{
  "schema_version": "1.0",
  "approved": false,
  "source": {
    "original_filename": "example.mp4",
    "path": "assets/reference-video/source.mp4",
    "duration_seconds": 42.6,
    "resolution": "1920x1080",
    "fps": 30.0
  },
  "scenes": [
    {
      "scene_index": 0,
      "start_seconds": 0.0,
      "end_seconds": 2.8,
      "keyframes": [
        "assets/reference-video/keyframes/scene00_start.jpg"
      ],
      "visual_change_level": "low",
      "description": null,
      "subject": null,
      "subject_motion": null,
      "camera": null,
      "motion_transfer_candidate": null
    }
  ],
  "analysis_meta": {
    "generated_at": "2026-09-04T12:00:00Z",
    "scene_threshold": 0.35,
    "change_level_thresholds": {"low_max": 0.02, "medium_max": 0.08}
  }
}
```

`description`, `subject`, `subject_motion`, `camera`, and
`motion_transfer_candidate` start `null` (engine output) and are filled
in by the agent after it views the keyframes — mirroring OpenMontage's
brief, which leaves vision-only fields blank for the same reason. Field
names deliberately reuse this project's own shot vocabulary
(`camera`, `action`→`subject_motion`, `characters`→`subject`) rather than
inventing new terms, since this brief's purpose is to seed those exact
`shot.json` fields later.

`approved` starts `false` (engine-written) and is set `true` only by the
agent, at the point the human accepts the enriched breakdown (Step 5 of
the agent flow below). It exists to make the ownership boundary between
the two writers explicit and enforceable, not just documented in prose:

**Ownership invariant: the engine may regenerate raw analysis, but must
never silently destroy an approved semantic interpretation.**
`analyze-reference-video` refuses to run (with or without `--force`) if
`video_analysis_brief.json` exists and its `approved` field is `true` —
it prints an error telling the human to move or rename the existing
brief first if they really want to re-run raw extraction from scratch
(e.g. after the analyzer itself improves). Without `--force`, it also
refuses whenever the file exists at all, `approved` or not, matching
this project's normal idempotency convention. `--force` only ever
overwrites an *unapproved* (still `false`) brief — a half-finished
analysis nobody has reviewed yet, which is safe to discard and redo.

No formal JSON Schema file for this artifact in v1 (unlike `shot.json`'s
`SHOT_SCHEMA`) — it's an intermediate working document the agent reads
and rewrites during one conversation, not a long-lived contract other
tooling depends on. Add schema validation later if it starts being
consumed by more than the one agent skill.

## Agent skill: `ai-film-reference-analyst`

New file `.claude/agents/ai-film-reference-analyst.md`, dispatched by a
new slash command `/analyze-reference` (not part of `/create-film`'s
existing per-shot dispatch loop — this is a standalone, optional entry
point a user invokes before or during story development, whenever they
have a reference clip in hand).

Flow:

1. Run `ai-film analyze-reference-video --source <path> --path <project>`.
2. Read the resulting brief. For each scene, look at its keyframe
   image(s) with vision and fill in `description`, `subject`,
   `subject_motion`, `camera`. Mark any field that doesn't apply as
   explicit `"N/A"` rather than leaving it ambiguous — silent omission
   produces guesswork downstream, same lesson OpenMontage's skill file
   states explicitly for its own 5-aspect breakdown.
3. For scenes with `visual_change_level: "high"`, look specifically at
   whether the keyframes show a *character* performing an action (not
   camera movement, not background crowd/particle motion). Set
   `motion_transfer_candidate: true` only when the motion is a specific,
   nameable character action a `MOTION_TRANSFER` driving video could
   reasonably reproduce (a dance move, a martial-arts sequence, a
   specific gesture) — set `false` otherwise, with the reasoning visible
   in `description`.
4. Present the enriched breakdown to the human as a simple flow diagram
   (scene-by-scene: what happens, camera, and — if flagged — "candidate
   for motion transfer, because ___"). This is the "draw the flow /
   diagram" the user asked for.
5. **Human approval gate.** Nothing proceeds past this point without
   explicit approval — same principle as this project's existing
   candidate-loop and cost-gate approvals. The human can accept, ask for
   re-analysis of a specific scene, or reject `motion_transfer_candidate`
   flags they disagree with.
6. On approval, write the enriched brief back to
   `assets/reference-video/video_analysis_brief.json` in place, setting
   `"approved": true`. This agent does **not** create or modify
   `03_shots/*.json` itself — that stays the job of
   `ai-film-director`/`ai-film-storyboard`, avoiding a second code path
   that creates shots.

## Integration with the existing pipeline

`ai-film-director` and `ai-film-storyboard` gain one small addition
each: at the start of their existing brainstorm/breakdown steps, check
whether `assets/reference-video/video_analysis_brief.json` exists. If it
does, read it and use its scenes as grounding/inspiration for scene and
shot content (pacing, camera choices, action descriptions) — and, for any
shot whose content clearly corresponds to a scene marked
`motion_transfer_candidate: true`, suggest `MOTION_TRANSFER` as that
shot's capability during the normal storyboard capability walkthrough,
the same way `/ai-film-setup` already surfaces capability choices for
human confirmation. This is a suggestion surfaced at an existing decision
point, not a new automatic behavior — consistent with
`docs/superpowers/specs/2026-09-03-motion-transfer-design.md`'s own
"human-directed only" invariant for `generate-motion-transfer`.

If no brief exists, both agents behave exactly as they do today — this
feature is purely additive.

## Testing

- `reference_analysis.py`: unit tests around scene-boundary parsing
  (feed canned `ffmpeg`/`showinfo` stderr output, verify scene list),
  change-level bucketing (feed canned scene-scores, verify thresholds),
  and brief-JSON shape — following this codebase's existing pattern of
  not shelling out to real ffmpeg in unit tests (see how
  `video_diagnostics.py`/`video_fix.py` tests mock subprocess calls).
- One integration-style test that runs the real CLI command against a
  tiny (~2s, 2-scene) fixture video checked into `tests/fixtures/`,
  asserting the brief file and keyframe files are actually created —
  mirroring how this project already tests other ffmpeg-touching code
  paths end-to-end where a small fixture is cheap enough to check in.
- CLI command tests (`test_cli_reference_commands.py`): argument
  validation, missing-source-file error, and that the copy into
  `assets/reference-video/source.<ext>` happens correctly.

## Open Items For The Plan (not resolved here, left for `writing-plans`)

- Exact `showinfo`/`signalstats` output parsing (regex or ffmpeg's
  `-print_format json` where supported) — an implementation detail, not
  a design decision.
- `--force`/re-run behavior is now decided (see the ownership invariant
  above) — no longer an open item.
