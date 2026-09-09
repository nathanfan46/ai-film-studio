# ai-film

Read this in other languages: [繁體中文](README.zh-TW.md)

`ai-film` is a standalone, provider-independent CLI and engine for an AI film-production
pipeline. It scaffolds a project directory, tracks each shot's generation lifecycle
(image, video, voice, sfx, music) through a JSON shot store with a cost-gated approval
workflow, and renders the completed shots into a final video with ffmpeg.

`/create-film` (a Claude Code slash command) conducts story, character, location, and
shot creation through conversation and writes `shot.json` for you — see Roadmap below.
This CLI is the production engine underneath that layer, and remains fully usable
directly for anyone who prefers hand-authoring shots.

## 1. Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

All commands below assume `.venv/bin` is on your `PATH`, or call `.venv/bin/ai-film` directly.

## 2. Try it free first, with the mock provider

No API key needed — this proves the whole pipeline works before you spend money.

```bash
ai-film init "The Last Ship" --path ~/my-film
```

Edit `~/my-film/config.json` and change every `"provider": "fal"` to `"provider": "mock"`
under `providers`.

Create a shot at `~/my-film/03_shots/S01_SH01.json`:

```json
{
  "schema_version": "1.0", "id": "S01_SH01", "status": "draft", "duration_seconds": 3,
  "continuity": {"status": "pending", "checked_at": null, "issues": []},
  "action": "a lone engineer walks through a dim corridor",
  "visual": {"style": "cinematic sci-fi", "lighting": "blue emergency light"},
  "camera": {"shot": "wide", "movement": "slow_push_in"},
  "characters": [],
  "generation": {
    "image": {"status": "pending", "attempts": 0},
    "video": {"status": "pending", "attempts": 0},
    "voice": {"status": "not_required"},
    "sfx": {"status": "not_required"},
    "music": {"status": "not_required"}
  }
}
```

Then walk it through the pipeline:

```bash
# 1. Validate the shot against the schema
ai-film validate --path ~/my-film

# 2. Record a continuity check (character/world consistency across shots)
ai-film check-continuity --shot S01_SH01 --status passed --path ~/my-film

# 3. Approve the shot for generation — nothing generates without this (the cost gate)
ai-film approve-generation --scope storyboard --targets S01_SH01 --path ~/my-film

# 4. Generate the storyboard image and the video
ai-film generate-image --shot S01_SH01 --path ~/my-film
ai-film generate-video --shot S01_SH01 --path ~/my-film

# 5. Check overall status
ai-film status --path ~/my-film

# 6. Render the final video (requires ffmpeg: `brew install ffmpeg`)
ai-film render --path ~/my-film
```

Note: `generate-image`/`generate-video` (direct generation, shown above) *do* check the approval recorded here, but against the bare shot id (`S01_SH01`) — the candidate-loop commands in §3 below (`generate-candidates`/`edit-candidate`) are stricter and check the full target string instead, so `approve-generation --targets` must match the `--target` you'll pass to `generate-candidates` character-for-character (a bare shot id like `S01_SH01` does not authorize `shot:S01_SH01:image` — see §3).

`ai-film status --path ~/my-film` should report `S01_SH01  completed`, and
`~/my-film/final/reel_001.mp4` should exist and be playable.

Add more shots the same way (`S01_SH02.json`, etc.), then generate every shot for a
stage at once, bounded by `config.json`'s `generation.max_parallel_jobs`:

```bash
ai-film generate-all --stage image --path ~/my-film
ai-film generate-all --stage video --path ~/my-film
```

For voice/sfx/music (which need a `--prompt`, since not every shot has dialogue or
a sound cue):

```bash
ai-film generate-voice --shot S01_SH01 --path ~/my-film
ai-film generate-sfx --shot S01_SH01 --prompt "distant metal groaning" --path ~/my-film
ai-film generate-music --shot S01_SH01 --prompt "tense ambient drone" --path ~/my-film
```

## 3. Review and refine before committing (the candidate loop)

Instead of generating one final image and hoping it's right, generate several
candidates, look them over in your browser, discuss changes, and lock in the one
you want. This works for character/environment references and for shot storyboard
images.

```bash
# 1. Approve the target for generation (bibles scope, for characters/environments)
ai-film approve-generation --scope bibles --targets character:girl --path ~/my-film

# 2. Generate 4 candidates in one cost-gated call
ai-film generate-candidates --target character:girl --count 4 \
  --prompt "a girl, cinematic sci-fi style, black jacket" --path ~/my-film

# 3. Open a gallery of all candidates in your browser
ai-film review --target character:girl --path ~/my-film

# 4. Not quite right? Edit a specific candidate with an instruction —
#    a true image edit if the provider supports it, otherwise a fresh
#    regeneration with the instruction merged into the original prompt
ai-film edit-candidate --target character:girl --id 002 \
  --instruction "make the jacket shorter" --path ~/my-film

# 5. Review again — the new candidate shows its lineage ("edit of 002")
ai-film review --target character:girl --path ~/my-film

# 6. Lock in the one you want. This copies the file to assets/characters/girl/reference.png
ai-film select-candidate --target character:girl --id 005 --path ~/my-film
```

Changed your mind? Just run `select-candidate` again with a different `--id` —
`selected` is always the *current* pick, not a permanent lock.

The same four commands work for shot storyboard images — use `--target
shot:S01_SH01:image` instead of `character:girl`. If you omit `--prompt` for a
shot target, it's derived from the shot's `action`/`visual`/`camera` fields (same
as `generate-image`); for `character:`/`env:` targets `--prompt` is required, since
there's no shot to derive one from. `select-candidate` on a shot target writes into
that shot's `generation.image.artifact` in `shot.json`, exactly like `generate-image`
would — the rest of the pipeline (`status`, `render`) doesn't know or care whether
an image came from the candidate loop or a direct `generate-image` call.

Environments use the same pattern with `--target env:<name>`.

Video/audio candidates and a whole-film preview aren't built yet — see Roadmap below.

## 4. Switch to real generation

```bash
export FAL_KEY="your-fal-api-key"
```

Set `config.json`'s providers back to `"fal"`. See what's available per capability:

```bash
ai-film models --capability image
ai-film models --capability video
```

Everything else (`validate`, `check-continuity`, `approve-generation`, `generate-*`,
`status`, `render`) works identically — only the config's `provider` value changes.

## Command reference

Run `ai-film --help` or `ai-film <command> --help` for the full list and flags. Full
command set: `init`, `models`, `status`, `validate`, `generate-image`, `generate-video`,
`generate-voice`, `generate-lipsync`, `generate-sfx`, `generate-music`, `generate-all`,
`check-continuity`, `approve-generation`, `render`, `generate-candidates`, `review`,
`select-candidate`, `edit-candidate`, `add-feedback`, `resolve-feedback`,
`apply-audio-offset`, `review-media`.

`generate-lipsync` runs an audio-driven lip-sync pass over a shot's already-generated
video and voice, superseding the video artifact with the synced result (same
version/history bookkeeping as any other video regeneration — `render` and everything
else downstream needs no changes to pick it up). It requires both stages already
`completed`; there's no `--force` flag since it always supersedes by design. Also note:
`generate-video` automatically sizes a dialogue shot's video to its voice's actual
measured duration (once the voice is generated) instead of the shot's static
`duration_seconds` — generate voice before video for a dialogue shot if you want them
to end up the same length.

`generate-video --continue-from-previous` starts a shot's video from the previous
shot's last frame (extracted via ffmpeg) and pairs it with this shot's own locked
storyboard image as an end frame — true dual-keyframe continuity, for a shot that
should visibly continue the previous one's action instead of resetting on the cut.
Only models in `MODELS_WITH_END_IMAGE_URL` (currently `h3-max` and `seedance-2.5`,
verified against each model's own fal.ai OpenAPI schema) actually honor the end frame;
other models still get the
extracted last frame as their sole starting reference. It degrades gracefully — with
no predecessor shot, no predecessor video yet, or no locked storyboard image yet, it
falls back to normal single-image generation and prints why, never errors.

`shot.json` may declare an optional `format` object (`{"resolution": "1280x720", "fps":
24}`) — an explicit per-shot override of the project's production format. Most shots
don't need one: `config.json`'s `render.resolution`/`render.fps` (default `1280x720`/`24`)
is the project default every shot without its own `format` inherits, and it's also what
`render` unconditionally normalizes every clip to at final-render time regardless of any
shot's own target or what a provider actually returned. `render.strict_format` (default
`false`) makes a generation call fail if the actual artifact's aspect ratio deviates from
its target by more than 2% — resolution and fps differences never fail generation in
either mode, since no fal model used by this project can hit an exact target pixel size
or frame rate (verified against each model's own schema); `render` is what actually
enforces the exact final size.

`generate-motion-transfer --shot <id> [--force]` generates a shot's video by retargeting
a driving reference video's motion onto the shot's locked character reference image
(`kling-video/v2.6/motion-control`), instead of prompt-driven text-to-video — useful when
a text description can't reliably reproduce a specific, precise, counted choreography
(fal.ai's own text-to-video models tend to collapse repetitive micro-gestures into a
generic approximation). Reads both inputs from the shot's own `shot.json` fields, no CLI
flags: `driving_video.path` (`{"driving_video": {"path": "05_video/reference_clips/
dance.mp4"}}`, project-relative like every other reference path) and `characters[0]
.reference` (the shot's first character's locked reference image — motion-transfer is
scoped to a single driving character). Writes into the same `generation.video` artifact
slot `generate-video` uses, with the same idempotent/`--force` semantics — `render`,
`generate-lipsync`, and `mux-audio` all pick it up automatically with no changes needed
there. The generated clip is always silent (`keep_original_sound` is forced off) — attach
dialogue/sfx/music afterward the normal way, via `generate-lipsync`/`mux-audio`.

`analyze-reference-video --source <path> [--force]` analyzes a local reference video —
scene cuts, keyframes, and a coarse per-scene "how much changed" signal — entirely via
local ffmpeg, at zero fal.ai cost. Writes
`assets/reference-video/video_analysis_brief.json`; the `/analyze-reference` command
dispatches an agent that reads it, looks at the keyframes with its own vision to fill
in each scene's description/subject/camera, flags scenes worth a `MOTION_TRANSFER`
look, and gets your approval before the brief is used as grounding context by
`/create-film`'s Director and Storyboard agents. Refuses to re-run over an approved
brief — move or rename it first if you want to redo the analysis from scratch.

`save-template --from <draft.json> --id <id> [--force]` validates a draft template
against the schema and saves it to `templates/<id>/`, copying any referenced keyframe
images alongside it — `/analyze-reference`'s agent offers to write this draft
automatically after you approve its analysis. `list-templates` and
`show-template --id <id>` enumerate and inspect what's saved. `export-template --id
<id> --output <file.zip>` / `import-template --from <file.zip> [--id <override>]
[--force]` bundle a template as a portable zip — handing someone a template shares a
camera/pacing recipe, not compute; they still need their own `ai-film-studio` install
and `FAL_KEY` to generate anything with it. A project opts into a saved template via
`config.json`'s top-level `"template": "<id>"` key (set during `/ai-film-setup` or by
hand) — the Director and Storyboard agents then use its `shot_patterns` as optional
grounding when drafting shots, never overriding an explicit story requirement.

`check-stale [--path <project>]` reports every shot whose generated storyboard
image was built against a character or environment reference file that has
since changed — e.g. you re-locked Mara's reference image after already
generating shots with the old one. Currently only tracks shots whose image was
generated via `generate-image` (or `generate-all --stage image`) — shots locked
through the candidate loop (`generate-candidates`/`select-candidate`) aren't
tracked yet, so they won't be flagged even if their reference has changed.
Tracked references can include more than character/environment images — a shot
can be flagged stale if an earlier shot's storyboard or the scene's continuity
reference changed. Read-only: it only reports, it never queues or triggers
regeneration. Re-run the normal `generate-image --force` (and the candidate
loop, if you want to review before committing) on whatever it flags.

**Video model selection is configurable per shot feature**, via `config.json`'s
`providers.video.model_by_feature`, so you don't have to flip the project-wide
default model back and forth for shots that need a different model's capabilities:

```json
"video": {
  "model": "veo-3",
  "model_by_feature": {
    "dialogue": "hailuo-2.3",
    "continue_from_previous": "h3-max"
  }
}
```

Recognized tags: `"dialogue"` / `"silent"` (whether the shot has spoken lines — some
models invent their own uncontrollable talking motion on silent shots) and
`"continue_from_previous"` (this call passed `--continue-from-previous` — pick a
model that actually supports dual-keyframe generation only for those calls). The
most specific matching tag wins (`continue_from_previous` is checked before
`dialogue`/`silent`); a shot with no matching tag configured falls back to
`providers.video.model`. See `_shot_features`/`_video_model` in `src/ai_film/cli.py`
if you're adding a new tag.

**`seedance-2.5`** (ByteDance, via fal.ai) generates up to 30 seconds natively in one
call, versus the few-second clips every other video model here is limited to. Useful
for a beat that's genuinely one continuous physical action (a character answers a
ringing phone, say) — writing it as one longer shot instead of splitting it into
several short ones avoids a real, observed failure mode where each independently
generated shot re-invents the transition (the phone ends shot A already at her ear,
then shot B has her pick it up again from scratch), since nothing carries true motion
continuity across an artificial cut the way one continuous generation does. It does
not help multi-camera coverage of the same action (a fight scene cut between a wide
shot and a close-up) — every independent generation call still has no memory of any
other one, so the same choreography still needs to actually match across the cut by
some other means, not just a longer single clip.

The last four are the media review layer, for reviewing generated video/audio and
fixing cheap timing issues without a provider call:

```bash
# Record a piece of review feedback for a shot's video/voice/sfx/music/sync,
# with an optional point-in-time (--at) or range (--range-start/--range-end)
ai-film add-feedback --shot S01_SH01 --target video --note "too dark" --at 3.3 --path ~/my-film

# Mark a feedback entry as resolved, with an optional note on how it was addressed
ai-film resolve-feedback --shot S01_SH01 --id fb001 --resolution "regenerated" --path ~/my-film

# Nudge an audio track's (voice/sfx/music) start time via ffmpeg — no provider spend
ai-film apply-audio-offset --shot S01_SH01 --track voice --offset-ms 400 --path ~/my-film

# Build (or rebuild) the video/audio review page for a shot and open it in your browser
ai-film review-media --shot S01_SH01 --path ~/my-film
```

## Project layout

`ai-film init` scaffolds:

```
config.json                 # provider/model selection, approval state, generation settings,
                             #   render.resolution/render.fps/render.strict_format (production format)
assets/                     # reference images (characters, environments, props, fonts)
  characters/<name>/          # candidates.json + candidates/*.png + reference.png once locked
  environments/<name>/        # same layout as characters/
00_story/ 01_bibles/ 02_scenes/
03_shots/                    # SH*.json — the shot.json contract, hand-authored today
04_storyboard/                # generated images
  candidates/<shot_id>/candidates/  # candidate images for that shot's storyboard, pre-selection — note the doubled "candidates/" (target_dir already includes one level; candidate generation adds its own subdirectory on top)
05_video/                     # generated video clips
06_audio/{dialogue,sfx,music}/
07_review/                    # static per-shot review pages (<shot_id>.html) built by `review-media`, plus generated waveform PNGs
final/                        # rendered reel_001.mp4 lands here
99_logs/                      # per-shot generation attempt logs + approval records
```

Once a shot's image/video/voice/sfx/music artifact has been regenerated (`--force`)
or timing-fixed (`apply-audio-offset`) at least once, a `history/` subdirectory
appears next to that stage's output directory (e.g. `05_video/history/`,
`06_audio/dialogue/history/`), holding the superseded version(s) — old artifact
files are moved there, never deleted, and `shot.json`'s `generation.<stage>.history`
records each one.

## Known limitations (v1)

- **`render` includes only audio already embedded in each shot's video clip.**
  Standalone `06_audio/` sfx/music tracks, and voice on any shot that never went
  through `generate-lipsync`, are still not mixed into the final reel.
- **No crash-safety for in-flight jobs.** A killed process mid-generation loses track
  of the paid job it just submitted; a re-run will resubmit and pay again.
- **No backoff or timeout on provider polling** — a stuck job can hang a `generate-*`
  or `generate-candidates`/`edit-candidate` command indefinitely.
- **Candidates are image-only.** Video/audio candidate review and a whole-film preview
  aren't built yet — see Roadmap below.
- **`generate-all --stage image` doesn't chain same-scene continuity.**
  Every shot's reference list is built before any shot generates, so the
  same-scene previous-shot-image chaining that `generate-image`/
  `generate-candidates` apply when run per shot in scene order doesn't
  apply in batch mode. Use per-shot generation in scene order if
  continuity chaining across a batch matters.

These are documented gaps from this project's own final reviews, not surprises you'll
discover — see `docs/superpowers/plans/2026-08-18-ai-film-studio-core-engine.md` and
`docs/superpowers/plans/2026-08-22-human-interaction-model.md` for the full
implementation history.

## Roadmap

The Claude Code Agent layer is implemented: `/ai-film-setup` configures providers,
`/create-film "Title"` scaffolds a project and walks the whole story -> character ->
location -> shot -> reviewed-storyboard-image -> reviewed-video pipeline through
conversation, dispatching the `ai-film-director`, `ai-film-character`,
`ai-film-environment`, `ai-film-storyboard`, and `ai-film-media` subagents in turn.
Locations get the same locked-reference-image treatment characters do — the
Environment agent runs once per unique location the Director's scenes name, before
Storyboard writes any shots, and every shot's generation conditions on its scene's
locked location the same way it already conditions on its characters. The Media
agent generates each shot's video and, if it has dialogue, its voice first — sizing
the video to the voice's actual measured length rather than a static guess, then
running a lip-sync pass (`generate-lipsync`) so the video's mouth movement is
audio-driven instead of coincidental. It opens the static review page and applies
fixes — a cheap audio-offset nudge, a targeted `shot.json` field edit plus
regeneration (which re-triggers the voice/video/lipsync chain as needed), or a
clarifying question — until you confirm the shot; sfx/music generate only when you
explicitly ask for them on a shot. See
`docs/superpowers/specs/2026-08-23-agent-layer-design.md`,
`docs/superpowers/plans/2026-08-23-agent-layer.md`,
`docs/superpowers/specs/2026-08-28-media-agent-design.md`,
`docs/superpowers/plans/2026-08-28-media-agent.md`,
`docs/superpowers/specs/2026-08-30-environment-locking-design.md`, and
`docs/superpowers/plans/2026-08-30-environment-locking.md` for the design and
implementation history.

**To use it:** the `/ai-film-setup`, `/create-film`, and `/analyze-reference`
commands and their six agents live in this repo's own `.claude/commands/` and
`.claude/agents/` — Claude Code only discovers project-local commands/agents
when you run `claude` from a directory whose `.claude/` contains them. Run
`claude` from this repo checkout to use them (the film project itself doesn't
have to live here — `/create-film "Title" [path]` takes the destination path as
an argument, defaulting to `./<slugified-title>` inside wherever you ran
`claude` from). To use these commands from a different working directory or a
separate film-only repo, copy or symlink `.claude/commands/ai-film-setup.md`,
`.claude/commands/create-film.md`, `.claude/commands/analyze-reference.md`, and
the six files under `.claude/agents/` into that directory's own `.claude/` (or
into `~/.claude/commands/` and `~/.claude/agents/` to make them available
everywhere).

Per those specs' Future Extensions: a dedicated Continuity agent if shot volume ever
justifies a second independent-context pass; a standalone entry point for revisiting
media review on an already-built film without going through `/create-film` again;
divergent (N-way) video candidate exploration, distinct from the sequential
generate-review-fix loop the Media agent already does today; automatic sfx/music
suggestion rather than only generating them on explicit request; an Editor agent for
final assembly; and a programmatic cost-estimation engine in `ai_film` replacing the
static per-model prompt-knowledge tables the agents use today.
