---
description: Configure ai-film-studio providers/models and verify FAL_KEY is set. Prerequisite for /create-film — rerun anytime to change providers.
argument-hint: "[project-path]"
---

# /ai-film-setup

Walks through provider/model selection for every generation capability and writes the picks into `config.json`. This is a prerequisite command, not part of the per-film pipeline — run it once before the first `/create-film`, and rerun it anytime to change providers.

## Step 0: Preflight — resolve a working `ai-film` binary

Do this before anything else. Do not require the user to manually activate a venv if you can avoid it. Only ever use one of the two fixed forms below (never a machine-specific absolute path) — this repo ships `.claude/settings.json` pre-authorizing exactly these two, so a working run shouldn't need any Bash permission prompts for them at all. Run each check as a single, plain command — never chain it with `; echo ...` or any other trailing command to inspect the exit code; your Bash tool already reports success/failure and any error output directly in its own result.

1. Run `ai-film version` by itself. If it succeeds, set `AI_FILM_BIN` to the literal string `ai-film` and continue.
2. If that fails — not found, or found but erroring (e.g. `ModuleNotFoundError: No module named 'ai_film'`, meaning something else on `PATH` shadowed the real one) — run `./.venv/bin/ai-film version` by itself, relative to the current working directory (where `claude` was launched from — the `ai-film-studio` repo checkout itself, per the README). If that works, set `AI_FILM_BIN` to the literal string `./.venv/bin/ai-film` (relative, exactly as written — do not expand it to an absolute path). Do not ask the user to `source .venv/bin/activate` manually.
3. If neither works and `./pyproject.toml` exists (right repo, just not set up), ask the user whether to bootstrap it now (`python3 -m venv .venv` then `.venv/bin/pip install -e ".[dev]"`); if they agree, run it and retry step 2. If `./pyproject.toml` doesn't exist either, tell the user to run `claude` from inside the `ai-film-studio` repo checkout, and stop.

Every `ai-film <command>` instruction below means `AI_FILM_BIN <command>` — substitute the resolved value.

## Target project

`$ARGUMENTS` is an optional path to the project directory. If empty, use the current working directory. Call this `PROJECT_PATH` for the rest of these instructions. Confirm `PROJECT_PATH/config.json` exists before continuing — if it doesn't, tell the user to run `AI_FILM_BIN init "<title>" --path PROJECT_PATH` (or `/create-film "<title>"`, which does this for them) first, and stop.

## Step 1: Check FAL_KEY

Run:

```bash
[ -n "$FAL_KEY" ] && echo "FAL_KEY is set" || echo "FAL_KEY is NOT set"
```

If not set, tell the user real generation will fail until they run `export FAL_KEY="your-fal-api-key"` in their shell (get a key from fal.ai). This isn't a hard stop — the mock provider works with no key, for testing the pipeline for free — but flag it clearly so they know why generation would fail if they later switch off mock.

## Step 2: Walk through each capability

For each capability in this exact order — `image`, `video`, `voice`, `sfx`, `music` — do:

1. Run `AI_FILM_BIN models --capability <capability>` and show the output verbatim (each line is `<provider>/<model>  <display name>`). Note: unlike every other `ai-film` subcommand, `models` takes no `--path` — it lists a static provider catalog, not project-specific data — passing `--path` here fails with `No such option: --path`.
2. Also mention `mock` is always available for that capability (for free, offline testing) even though it won't appear in the `ai-film models` catalog output (that command only lists real fal.ai models).
3. Ask the user to pick a provider+model for this capability, or say "keep current" to leave it unchanged. Show the current pick from `PROJECT_PATH/config.json`'s `providers.<capability>` first so "keep current" is a real option.

## Step 3: Write the picks

Read `PROJECT_PATH/config.json`. For each capability the user changed, update `providers.<capability>.provider` and `providers.<capability>.model` in place — leave `providers.<capability>.parameters` untouched (an empty object `{}` by default; only touch it if the user explicitly asks to set provider parameters). Write the file back with 2-space indent and a trailing newline, matching the file `ai-film init` originally wrote — do not reorder existing top-level keys.

Confirm back to the user what changed, e.g.:

```
providers.image: fal/nano-banana -> fal/nano-banana-pro
providers.video: unchanged (fal/veo-3)
```

## Step 4: Re-affirm the cost gate

Tell the user, briefly: nothing generates automatically just because a provider is configured here — every `generate-*`/`generate-candidates`/`edit-candidate` call still requires `ai-film approve-generation` first (the `/create-film` agents handle that for you, showing a cost estimate before asking).
