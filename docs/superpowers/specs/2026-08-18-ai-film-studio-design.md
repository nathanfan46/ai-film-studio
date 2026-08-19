# AI Film Studio — v1 Design

**Status:** Approved for implementation
**Date:** 2026-08-18

## 1. Overview

AI Film Studio is a Claude Code Skill + Agent system for producing short AI-generated
films end-to-end: script → characters/world → storyboard → continuity check →
image/video/voice/sound generation → edited final cut. The user drives it through
Claude Code (`/create-film "Title"`), but the production engine underneath is a
standalone, provider-independent Python package (`ai_film`) with its own CLI. Claude
Code is the first *interface* to this engine, not the engine itself — the goal is a
system where an equally valid caller could be a script, a CI job, or a future web UI.

### Goals (v1)

- A working end-to-end pipeline: `/create-film` produces a real `final/reel_001.mp4`.
- Provider-independent architecture: swapping image/video/voice backends is a config
  change, never an agent or pipeline rewrite.
- Continuity checking (text/spec-level) to prevent character/world drift across shots.
- Explicit, enforced human approval before any paid generation call.
- Resumable, idempotent, mockable, and testable outside of Claude Code.

### Non-Goals (v1)

- Vision-based (pixel-level) continuity checking — v1 is spec/text-level only.
- Direct provider backends beyond fal.ai (Runway, Google direct, OpenAI direct) — the
  provider interface supports them, but only `FalImageProvider`/`FalVideoProvider`/
  `FalAudioProvider` ship in v1.
- VideoDB as a core dependency — FFmpeg + Remotion are the core editing stack; VideoDB
  is a possible future *optional* backend, not built in v1.
- CI-run smoke tests against real providers — real-provider tests are manual, to avoid
  burning API cost automatically.
- Multi-user / hosted / web UI — v1 is a local, single-user CLI + Claude Code skill.

## 2. Architecture

```
                    Claude Code
                         │
                    AI Film Skill  (SOP / knowledge)
                         │
                Specialized Agents (Director, Character,
                Storyboard, Continuity, Image, Video,
                Voice/Sound, Editor)
                         │
                         ▼
                     ai-film CLI            ← stable public interface
                         │
                   Service Layer            (retries, logging, cost gate)
                         │
               Provider Interfaces          (ImageProvider, VideoProvider, AudioProvider)
                         │
              ┌──────────┼──────────┐
              ▼          ▼          ▼
          FalImage   FalVideo   FalAudio     ← only v1 backends; Mock* for tests
                         │
                    Async Jobs                (submit / poll / result, retry+backoff)
                         │
                  Generated Assets
                         │
                    shot.json                 ← canonical contract, single source of
                         │                       truth for current state
                    Editor / Render
                         │
                     final.mp4

(separately, unconnected to the production path:)
    Claude  →  fal-ai-media MCP  →  ad-hoc creative exploration only
```

**Core principle:** the AI Film Skill and its agents never depend on a specific
provider. Agents call the `ai-film` CLI; the CLI delegates to application services;
services depend on abstract provider interfaces; provider/model selection comes
exclusively from `project/config.json`. fal.ai is the v1 backend implementation, not
an architectural assumption.

`99_logs/` is the immutable execution history (every request/response, every attempt,
every failure). `shot.json` is the current state (what's true *now*). The two never
merge into one state machine — logs record what happened, the shot file records where
things stand.

## 3. Repository Layout

```
ai-film-studio/
├── .claude/
│   ├── skills/
│   │   └── ai-film/
│   │       ├── SKILL.md
│   │       └── references/
│   │           ├── shot-schema.md
│   │           └── continuity-checklist.md
│   ├── agents/
│   │   ├── ai-film-director.md
│   │   ├── ai-film-character.md        # Character/Environment/Style Bible
│   │   ├── ai-film-storyboard.md
│   │   ├── ai-film-continuity.md
│   │   ├── ai-film-image.md
│   │   ├── ai-film-video.md
│   │   ├── ai-film-voice-sound.md
│   │   └── ai-film-editor.md
│   └── commands/
│       ├── ai-film-setup.md
│       └── create-film.md
│
├── src/ai_film/
│   ├── cli.py                          # `ai-film` entrypoint (typer)
│   ├── services/
│   │   ├── image_service.py
│   │   ├── video_service.py
│   │   └── audio_service.py            # generate_voice / generate_sfx / generate_music
│   ├── providers/
│   │   ├── base.py                     # Protocols + GenerationJob/JobStatus
│   │   ├── fal/{image,video,audio}.py
│   │   └── mock/{image,video,audio}.py
│   ├── jobs.py                         # submit/wait/get_result, retry+backoff
│   ├── schema.py                       # shot.json JSON Schema + validation
│   └── render.py                       # render manifest + FFmpeg/Remotion invocation
│
├── tests/
│   ├── test_cli.py
│   ├── services/
│   └── providers/
│
├── project/                            # generated per-film output
│   ├── config.json
│   ├── assets/{characters,environments,props,reference-images,fonts}/
│   ├── 00_story/
│   ├── 01_bibles/
│   ├── 02_scenes/
│   ├── 03_shots/                       # SH001.json ... — the shot.json contract
│   ├── 04_storyboard/
│   ├── 05_video/
│   ├── 06_audio/{dialogue,sfx,music}/
│   ├── final/
│   └── 99_logs/                        # immutable execution history
│
├── docs/superpowers/specs/             # this file
├── README.md
├── LICENSE
└── pyproject.toml
```

## 4. The `shot.json` Contract

`schema_version` allows the contract to evolve without breaking older projects.
Full v1 shape:

```json
{
  "schema_version": "1.0",
  "id": "S01_SH07",
  "status": "generating",
  "duration_seconds": 5,

  "characters": [
    { "id": "girl", "reference": "assets/characters/girl/reference.png" }
  ],

  "inputs": {
    "references": ["assets/characters/girl/reference.png"]
  },

  "camera": { "shot": "close_up", "lens": "85mm", "movement": "slow_push_in" },
  "action": "girl steps out of darkness",
  "dialogue": { "text": "你終於來了。", "speaker": "girl" },
  "visual": { "lighting": "blue rim light", "style": "cinematic sci-fi" },

  "continuity": {
    "status": "passed",
    "checked_at": "2026-08-18T10:00:00Z",
    "issues": []
  },

  "generation": {
    "image": {
      "provider": "fal",
      "model": "nano-banana",
      "status": "completed",
      "job": { "provider": "fal", "id": "j1" },
      "inputs": ["assets/characters/girl/reference.png"],
      "artifact": {
        "path": "04_storyboard/SH07.png",
        "size_bytes": 842011,
        "sha256": null
      },
      "attempts": 1,
      "created_at": "...",
      "started_at": "...",
      "completed_at": "..."
    },
    "video": {
      "provider": "fal",
      "model": "veo-3",
      "status": "pending",
      "job": null,
      "inputs": ["04_storyboard/SH07.png", "assets/characters/girl/reference.png"],
      "artifact": null,
      "attempts": 0,
      "created_at": null,
      "started_at": null,
      "completed_at": null
    },
    "voice": {
      "provider": "fal",
      "model": "csm-1b",
      "status": "pending",
      "job": null,
      "inputs": [],
      "artifact": null,
      "attempts": 0
    },
    "sfx": { "status": "not_required" },
    "music": { "status": "not_required" }
  }
}
```

Field notes:
- `status` (top-level) ∈ `draft | ready | generating | completed | failed` — the
  aggregate lifecycle of the shot, rolled up from `continuity` + `generation.*` for
  cheap reporting (`ai-film status`) without recomputing from every substage.
- `continuity.status` ∈ `pending | passed | warning | failed`.
- `generation.<stage>.status` ∈
  `pending | queued | running | completed | failed | not_required`.
  `not_required` means the shot was evaluated and this stage doesn't apply (e.g. no
  music cue for this shot) — distinct from `pending` (not yet attempted) so
  `ai-film status` never has to guess what "missing" means.
- `generation.<stage>.job` is `{provider, id}` — job IDs are provider-scoped, never
  assumed globally unique or meaningful across providers.
- `generation.<stage>.inputs` lists the artifact paths that stage actually depends on
  (e.g. video depends on the storyboard image *and* character references). The
  top-level `inputs` block only holds dependencies known at storyboard-creation time
  (reference assets); artifacts produced by earlier pipeline stages (like the
  storyboard image) are declared on the generation stage that consumes them, once
  they exist. A stage must validate its declared `inputs` exist on disk before
  calling `submit()`.
- `duration_seconds` (top-level) is the creative target; the actual rendered length
  lives on the artifact — see below.
- `generation.<stage>.artifact` is `{path, size_bytes, sha256}` once completed
  (`sha256` may be `null` in v1 — full checksum verification is a fast-follow, not a
  v1 blocker) plus `duration_seconds` on video/audio artifacts specifically, to
  distinguish target vs. actual:
  `"artifact": {"path": "...", "size_bytes": ..., "sha256": null, "duration_seconds": 4.8}`.
- Every `shot.json` write records the *actual* provider/model used, so the project
  stays historically accurate even after `config.json` changes later (see §7).

## 5. Provider Abstraction

```python
class ImageProvider(Protocol):
    def submit(self, request: ImageGenerationRequest) -> GenerationJob: ...
    def poll(self, job: GenerationJob) -> JobStatus: ...
    def get_result(self, job: GenerationJob) -> ImageGenerationResult: ...
```

Same shape for `VideoProvider`. `AudioProvider` follows the identical async
`submit → poll → get_result` lifecycle, but splits `submit` into three explicit
methods — `submit_voice()`, `submit_sfx()`, `submit_music()` — rather than one
generic `submit(request)` with ambiguous parameters, since the three workloads (TTS,
sound-effect synthesis, music generation) have genuinely different request shapes.
The methods differ; the lifecycle does not — `AudioProvider` is not an exception to
the async-job principle:

```python
class AudioProvider(Protocol):
    def submit_voice(self, request: VoiceGenerationRequest) -> GenerationJob: ...
    def submit_sfx(self, request: SfxGenerationRequest) -> GenerationJob: ...
    def submit_music(self, request: MusicGenerationRequest) -> GenerationJob: ...
    def poll(self, job: GenerationJob) -> JobStatus: ...
    def get_result(self, job: GenerationJob) -> AudioGenerationResult: ...
```

v1 implementations: `FalImageProvider`, `FalVideoProvider`, `FalAudioProvider` (calling
fal.ai's REST/SDK directly with `FAL_KEY` — no MCP dependency), and `Mock*Provider`
for every interface, used by all unit tests (no network calls, no cost).

Generation is always modeled as an async job (`submit` → poll → `get_result`), never a
blocking call — required for video generations that can take minutes, and for
retries/cancellation/parallelism/resumability to work uniformly across stages.

## 6. Cost Gate (hard execution boundary)

Paid generation is refused by the **service layer**, not merely discouraged by agent
instructions — an agent cannot accidentally or intentionally bypass it. `config.json`
is organized into separate top-level sections so provider choice, runtime tuning, and
approval state never get muddled together:

```json
{
  "providers": {
    "image": {"provider": "fal", "model": "nano-banana", "parameters": {}},
    "video": {"provider": "fal", "model": "veo-3", "parameters": {}}
  },
  "generation": {
    "max_attempts": 3,
    "max_parallel_jobs": 3,
    "poll_interval_seconds": 5
  },
  "render": {},
  "generation_approval": {
    "approved": false,
    "approved_at": null,
    "scope": "current_storyboard",
    "estimated_cost": null
  }
}
```

Flow: once Storyboard + Continuity pass, the Director agent computes a cost estimate
across all pending shots and calls `ai-film approve-generation --scope current_storyboard`
(prompting the user for confirmation first). This writes `generation_approval.approved
= true` with a timestamp and cost snapshot into `config.json`. Any `ai-film generate-*`
call checks this flag in the service layer and refuses with a clear error if approval
is missing or the scope doesn't cover the requested shots. Approval is scoped, not
global — adding new shots later requires re-approval for the new scope.

**`config.json` is user-editable and therefore not tamper-proof** — someone could hand-edit
`"approved": true`. v1 does not need cryptographic signing, but `approve-generation`
must also append an immutable record to `99_logs/approvals/<timestamp>_generation_approved.json`
(scope, cost estimate, shot IDs covered, timestamp). `config.json` reflects *current*
approval state; `99_logs/approvals/` is the proof that an approval event actually
happened — the same current-state/immutable-history split as `shot.json` vs.
`99_logs/` in general (§2).

## 7. Model Switching Semantics

Changing `project/config.json` must never silently invalidate completed artifacts.
Concretely:

- `ai-film generate-image --shot SH003` only regenerates shots whose
  `generation.image.status` is not `completed`.
- Already-`completed` shots are left untouched even if `config.json` now points at a
  different model — the provider/model actually used stays recorded in that shot's
  `generation.image` block.
- To force regeneration of a completed shot under a new model:
  `ai-film generate-image --shot SH003 --force`.

This makes `/ai-film-setup` safely rerunnable mid-project: switching to a cheaper or
better model only affects shots generated *after* the switch, and the project remains
an accurate historical record of what was actually generated with what.

## 8. Agent Pipeline

The approval checkpoint is an explicit stage in the pipeline, not something each
generation agent independently decides to respect:

```
Director → Bibles → Storyboard → Continuity
                                      │
                              ──────────────
                              💰 COST GATE
                        (ai-film approve-generation)
                              ──────────────
                                      │
                    Image / Video / Voice / Sfx / Music
                                      │
                                   Editor
                                      │
                                  final.mp4
```

No agent downstream of Continuity is permitted to call `generate-*` until
`generation_approval.approved` is true for its shots' scope — enforced by the service
layer (§6), not by agent instructions.

| Agent | Reads | Writes | Role |
|---|---|---|---|
| Director | user prompt | `00_story/`, scene list | logline, 3-act structure, scene breakdown; computes cost estimate and drives the approval checkpoint |
| Character/Environment/Style | `00_story/` | `01_bibles/`, `assets/*/reference.png` | character/environment/style bibles + reference images (via Image provider) |
| Storyboard | `00_story/`, `01_bibles/` | `03_shots/SH*.json` | drafts the shot list — the contract |
| Continuity | `03_shots/*.json`, `01_bibles/` | `shot.json["continuity"]` | text/spec-level consistency check across all shots |
| *(approval checkpoint — see above)* | | | |
| Image | `shot.json` (approved) | `04_storyboard/`, `generation.image` | calls `ai-film generate-image` |
| Video | `shot.json` (approved) | `05_video/`, `generation.video` | calls `ai-film generate-video` |
| Voice/Sound | `shot.json` (approved) | `06_audio/`, `generation.voice`/`sfx`/`music` | calls `ai-film generate-voice`/`-sfx`/`-music` |
| Editor | all `shot.json`, `05_video/`, `06_audio/` | render manifest, `final/reel_001.mp4` | builds manifest, invokes `ai-film render` |

## 9. Commands

**Claude Code slash commands:**
- `/ai-film-setup` — per-capability provider/model picker (v1: fal.ai catalog only),
  validates required env vars, writes `config.json`. Rerunnable anytime.
- `/create-film "Title"` — runs `ai-film init`, then hands off to the Director agent.

**`ai-film` CLI subcommands:**
`init`, `generate-image`, `generate-video`, `generate-voice`, `generate-sfx`,
`generate-music`, `check-continuity`, `approve-generation`, `render`, `status`,
`validate` (schema check).

## 10. Error Handling, Retries, Concurrency

- Every provider call goes through the service layer: submit → poll with backoff → on
  failure, log the request/response to `99_logs/`, increment `attempts`, retry up to
  `max_attempts` (default 3, config-controlled).
- Logs are organized per shot, per attempt, so debugging a single shot doesn't
  require scanning a flat log stream:
  ```
  99_logs/
  ├── SH007/
  │   ├── 20260818T210000_image_attempt01.json
  │   └── 20260818T210130_image_attempt02.json
  └── approvals/
      └── 20260818T205000_generation_approved.json
  ```
- **Logs must redact secrets before persistence** — API keys, `Authorization`
  headers, signed/temporary download URLs, and any other credential-shaped values are
  replaced with `"[REDACTED]"` before a request/response payload is written to disk.
  This applies uniformly across all provider backends, not case-by-case.
- Exhausted retries set that stage's `status` to `failed` — the pipeline stops or
  flags rather than assembling a final video silently missing shots.
- `ai-film status` reports pending/failed shots across the project for triage: retry,
  switch that shot's provider, or manual intervention.
- All generation calls are idempotent by default (skip `completed` shots) and safely
  resumable after an interrupted run.
- Concurrency is bounded by `max_parallel_jobs` to stay under provider rate limits.

## 11. Rendering

The Editor agent produces a **render manifest** before invoking FFmpeg/Remotion:

```json
{
  "shots": [
    {"id": "S01_SH01", "video": "05_video/SH01.mp4", "duration": 4},
    {"id": "S01_SH02", "video": "05_video/SH02.mp4", "duration": 3}
  ],
  "audio": [],
  "captions": []
}
```

`ai-film render` is deterministic given the same manifest + artifacts — no timeline
`start` offsets are stored on individual shots in v1; the editor computes the timeline
from shot order. This keeps the render step debuggable in isolation from generation.

## 12. Testing Strategy

- Unit tests for services/CLI run entirely against `Mock*Provider` — no network calls,
  no cost, safe for CI.
- `shot.json` has a JSON Schema (versioned via `schema_version`); `ai-film validate`
  and a pytest check both enforce it.
- One real-provider smoke test exists but is manually triggered, never run in CI, to
  avoid automatically incurring cost.

**v1 acceptance checklist** (the definition of "v1 works"):
```
✓ project initialized
✓ shot.json validates against schema
✓ continuity check passes
✓ cost gate blocks generation until approved, then unblocks after approval
✓ approval event recorded immutably under 99_logs/approvals/
✓ provider job submits
✓ job completes
✓ artifact exists on disk
✓ render succeeds from manifest
✓ final/reel_001.mp4 exists
✓ ai-film status reports all shots completed
```

## 13. Open Source Considerations

- `fal-ai-media` MCP is repositioned as a creative-exploration sandbox only
  ("generate me 3 spaceship concepts") — never a production dependency. The
  production pipeline has zero MCP dependency; it runs as a standalone Python
  package usable outside Claude Code entirely (script, CI job, future web UI).
- `pyproject.toml`, `tests/`, `LICENSE`, `README.md` at the repo root from day one.

## 14. Future Extensions (explicitly out of scope for v1)

- `RunwayImageProvider` / `RunwayVideoProvider`, `GoogleImageProvider` /
  `GoogleVideoProvider` (direct APIs, bypassing fal.ai) — same `ImageProvider`/
  `VideoProvider` interfaces, new backend modules only.
- VideoDB as an optional alternate editing backend alongside FFmpeg/Remotion.
- Vision-based continuity checking (comparing actual generated frames, not just specs).
- Timeline-level shot placement (`timing.start`) for non-linear editing.
- `04_storyboard/SH001.png` may later become `04_storyboard/SH001/frame.png` to hold
  multiple related images per shot (storyboard draft, keyframe, reference variants).
  The flat structure is sufficient for v1 and requires no `shot.json` schema change
  to migrate later, since `generation.image.artifact.path` is already an explicit
  path rather than an inferred convention.
- Full `sha256` artifact integrity verification (currently optional/`null` in v1).
