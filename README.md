# ai-film

`ai-film` is a standalone, provider-independent CLI and engine for an AI film-production
pipeline. It scaffolds a project directory, tracks each shot's generation lifecycle
(image, video, voice, sfx, music) through a JSON shot store with a cost-gated approval
workflow, and renders the completed shots into a final video with ffmpeg.

`/create-film` (a Claude Code slash command) conducts story, character, and shot
creation through conversation and writes `shot.json` for you — see Roadmap below.
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

Video/audio candidates, an interactive (clickable) review UI, and a whole-film
preview aren't built yet — see Roadmap below.

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
`generate-voice`, `generate-sfx`, `generate-music`, `generate-all`, `check-continuity`,
`approve-generation`, `render`, `generate-candidates`, `review`, `select-candidate`,
`edit-candidate`.

## Project layout

`ai-film init` scaffolds:

```
config.json                 # provider/model selection, approval state, generation settings
assets/                     # reference images (characters, environments, props, fonts)
  characters/<name>/          # candidates.json + candidates/*.png + reference.png once locked
  environments/<name>/        # same layout as characters/
00_story/ 01_bibles/ 02_scenes/
03_shots/                    # SH*.json — the shot.json contract, hand-authored today
04_storyboard/                # generated images
  candidates/<shot_id>/candidates/  # candidate images for that shot's storyboard, pre-selection — note the doubled "candidates/" (target_dir already includes one level; candidate generation adds its own subdirectory on top)
05_video/                     # generated video clips
06_audio/{dialogue,sfx,music}/
final/                        # rendered reel_001.mp4 lands here
99_logs/                      # per-shot generation attempt logs + approval records
```

## Known limitations (v1)

- **Reference-image conditioning doesn't work against real fal.ai yet.** `generate-image`,
  `generate-video`, and `generate-candidates` (for shot targets) all pass character
  reference paths through correctly, but the fal providers send local file paths where
  the API expects uploaded URLs — an upload step hasn't been implemented, so this only
  works with the mock provider today.
- **`render` drops audio.** Voice/sfx/music generate and save to disk correctly, but
  the render manifest doesn't include them yet — the final video is video-only.
- **No crash-safety for in-flight jobs.** A killed process mid-generation loses track
  of the paid job it just submitted; a re-run will resubmit and pay again.
- **No backoff or timeout on provider polling** — a stuck job can hang a `generate-*`
  or `generate-candidates`/`edit-candidate` command indefinitely.
- **Candidates are image-only.** Video/audio candidate review, a whole-film preview,
  and interactive (clickable) browser review aren't built yet — see Roadmap below.

These are documented gaps from this project's own final reviews, not surprises you'll
discover — see `docs/superpowers/plans/2026-08-18-ai-film-studio-core-engine.md` and
`docs/superpowers/plans/2026-08-22-human-interaction-model.md` for the full
implementation history.

## Roadmap

The Claude Code Agent layer is implemented: `/ai-film-setup` configures providers,
`/create-film "Title"` scaffolds a project and walks the whole story -> character ->
shot -> reviewed-storyboard-image pipeline through conversation, dispatching the
`ai-film-director`, `ai-film-character`, and `ai-film-storyboard` subagents in turn.
See `docs/superpowers/specs/2026-08-23-agent-layer-design.md` and
`docs/superpowers/plans/2026-08-23-agent-layer.md` for the design and implementation
history.

**To use it:** the `/ai-film-setup` and `/create-film` commands and their three
agents live in this repo's own `.claude/commands/` and `.claude/agents/` — Claude
Code only discovers project-local commands/agents when you run `claude` from a
directory whose `.claude/` contains them. Run `claude` from this repo checkout to
use them (the film project itself doesn't have to live here — `/create-film "Title"
[path]` takes the destination path as an argument, defaulting to `./<slugified-
title>` inside wherever you ran `claude` from). To use these commands from a
different working directory or a separate film-only repo, copy or symlink
`.claude/commands/ai-film-setup.md`, `.claude/commands/create-film.md`, and the
three files under `.claude/agents/` into that directory's own `.claude/` (or into
`~/.claude/commands/` and `~/.claude/agents/` to make them available everywhere).

Per that spec's Future Extensions: a dedicated Continuity agent if shot volume ever
justifies a second independent-context pass, video/audio candidate review once the
engine gains video candidate storage and an edit/regenerate loop for clips, voice/
sfx/music generation agents and an Editor agent for final assembly, and a
programmatic cost-estimation engine in `ai_film` replacing the static per-model
prompt-knowledge table the agents use today.
