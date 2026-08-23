# ai-film

`ai-film` is a standalone, provider-independent CLI and engine for an AI film-production
pipeline. It scaffolds a project directory, tracks each shot's generation lifecycle
(image, video, voice, sfx, music) through a JSON shot store with a cost-gated approval
workflow, and renders the completed shots into a final video with ffmpeg.

There is currently no agent layer that writes shots for you — you hand-write `shot.json`
files (or a future Skill/Agents layer will do this; see Roadmap below). This CLI is the
production engine underneath that layer.

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
  candidates/<shot_id>/        # candidate images for that shot's storyboard, pre-selection
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

The next piece is a Claude Code Skill + Agent layer (`.claude/skills/ai-film/`,
Director/Storyboard/Continuity/Editor agents, `/create-film` and `/ai-film-setup`
commands) that writes `shot.json` files for you from a story prompt — and conducts
the candidate review conversation above on your behalf ("here are 4 options, which
do you like?") — instead of you hand-authoring shots and running the candidate
commands yourself. Per `docs/superpowers/specs/2026-08-18-ai-film-studio-design.md`.
That plan is written but not yet implemented.

Beyond that, per `docs/superpowers/specs/2026-08-22-human-interaction-model-design.md`'s
Future Extensions: video/audio candidate review (the same four commands, applied to
`shot:<id>:video`/`voice`/`sfx`/`music` targets), a whole-film final-cut preview
reusing `review`, and interactive (clickable) browser review if text-based picking
proves too slow in practice.
