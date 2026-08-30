---
description: Start or resume a film — scaffolds the project, then runs the Director, Character, Environment, Storyboard, and Media agents in sequence through conversation.
argument-hint: "\"<Title>\" [project-path]"
---

# /create-film

The single entry point for starting or resuming a film with `ai-film-studio`. Scaffolds (or resumes) a project, then walks the whole story -> character -> location -> shot -> locked storyboard image -> reviewed video/voice pipeline through conversation, dispatching the five pipeline agents in order and relaying your answers to them per the protocol below.

## The human-in-the-loop protocol (you are the orchestrator side of this)

The `ai-film-director`, `ai-film-character`, `ai-film-environment`, `ai-film-storyboard`, and `ai-film-media` subagents you dispatch below have no live channel to the user themselves — only you do, since you're running in this actual conversation. Whenever one of them needs a real answer, its final report ends with, verbatim:

```
NEEDS_INPUT:
id: <some id>
type: clarification | selection | cost_approval | confirmation
question: <the question>
```

Whenever a dispatched subagent's report ends this way, you must:

1. Parse the `id`, `type`, and `question`.
2. Ask the user that exact question, for real, in this conversation (a plain message is fine; use your judgment on whether a multiple-choice-style tool fits better for a `selection`/`cost_approval`/`confirmation` question — the point is a genuine answer from the user, not a proxy for it).
3. Once you have their real answer, **resume the SAME subagent dispatch you already have running** (the same instance/session, never a fresh dispatch — losing that instance loses everything it already worked out) with a message containing: use whatever mechanism your platform gives you for continuing a specific, already-dispatched subagent (sending it another message so it resumes from its own transcript, not launching a new one). If your platform genuinely gives you no way to continue an existing subagent instance, stop and tell the user the pipeline can't safely proceed here — do not fall back to a fresh dispatch, since a fresh instance has none of the prior context and could re-derive a different (or contradictory) answer to the same question.

```
HUMAN_RESPONSE:
id: <the exact id from the NEEDS_INPUT you just relayed>
answer: <the user's answer>
```

or, if `type` was `cost_approval`:

```
HUMAN_RESPONSE:
id: <the exact id from the NEEDS_INPUT you just relayed>
approved: true | false
message: <the user's stated reason, only if approved is false>
```

4. Repeat this detect → ask → resume cycle for as long as the subagent's report keeps ending in `NEEDS_INPUT` — a single phase (Director, one Character run, or the Storyboard run) can take many round trips. A subagent's report that does **not** end in `NEEDS_INPUT` means that phase has genuinely finished (or hit a natural stopping point like re-entry finding nothing to do) — that's your signal to move to the next step below, not another round trip.

**Protocol errors — treat these as real errors, not something to guess past:** if a subagent's report doesn't parse as either a genuine completion or a well-formed `NEEDS_INPUT` block (missing `id`/`type`/`question`, or free-form "I need more info" prose instead of the literal block), tell the user the agent didn't follow the input protocol correctly and stop rather than inventing an answer or silently retrying.

**Cost approval is never assumed on the agent's behalf.** For any `type: cost_approval` `NEEDS_INPUT`, you relay the real question and wait for the user's real answer before resuming — never resume with `approved: true` unless the user actually said so in this conversation.

## Step 0: Preflight — resolve a working `ai-film` binary

Do this before anything else; every later step needs a binary that actually works. Do not require the user to manually activate a venv if you can avoid it — resolve around it instead. Only ever use one of the two fixed forms below (never a machine-specific absolute path) — this repo ships `.claude/settings.json` pre-authorizing the non-spend `ai-film` subcommands under these two forms (`init`, `version`, `validate`, `status`, `models`, `check-continuity`, `review`, `select-candidate`, `review-media`, `add-feedback`, `resolve-feedback`). It deliberately does **not** pre-authorize `approve-generation`, `generate-candidates`, `edit-candidate`, `generate-video`, `generate-voice`, `generate-sfx`, `generate-music`, or `apply-audio-offset` — those still show a Bash permission prompt every time, on top of (not instead of) the `NEEDS_INPUT`/`HUMAN_RESPONSE` cost-approval protocol below. That's intentional defense in depth for anything that can spend real money; don't try to route around it or suggest the user add those to their allow-list. Run each check as a single, plain command — never chain it with `; echo ...` or any other trailing command to inspect the exit code; your Bash tool already reports success/failure and any error output directly in its own result.

1. Run `ai-film version` by itself. If it succeeds and prints a version, you're done: set `AI_FILM_BIN` to the literal string `ai-film` and skip straight to `## Parse arguments` below (do not confuse this with the numbered items in this checklist — there is no "step 1" among them, only 1/2/3 here).
2. If that fails — command not found, *or* found but erroring (e.g. a stale/broken shim like `ModuleNotFoundError: No module named 'ai_film'`, which means something else on `PATH` shadowed the real one) — run `./.venv/bin/ai-film version` by itself, relative to the current working directory (this is where `claude` was launched from, which per the README is meant to be the `ai-film-studio` repo checkout itself). If that succeeds, set `AI_FILM_BIN` to the literal string `./.venv/bin/ai-film` (relative, exactly as written — do not expand it to an absolute path) and use it for every `ai-film` invocation for the rest of this run. Do not ask the user to `source .venv/bin/activate`.
3. If neither works, check whether `./pyproject.toml` exists (confirms you're in the right repo, just not set up yet). If it does, tell the user no working `ai-film` install was found and ask whether you should set one up now (`python3 -m venv .venv` then `.venv/bin/pip install -e ".[dev]"`, matching the README's install step). If they say yes, run it, then retry step 2 above. If `./pyproject.toml` doesn't exist either, this isn't the `ai-film-studio` repo checkout at all — tell the user to run `claude` from inside it instead, and stop; there's nothing to self-heal here.

From here on, every instruction in this file and in the five dispatched agents' own instructions that says `ai-film <command>` means `AI_FILM_BIN <command>` — substitute the resolved value. You don't need to pass `AI_FILM_BIN` down when dispatching the `ai-film-character`, `ai-film-environment`, `ai-film-storyboard`, and `ai-film-media` subagents in Steps 3-6 below — each one runs this exact same resolution independently (they inherit the same working directory you're running in, so they'll resolve the same value).

This command does not itself check `FAL_KEY` or provider configuration — if the user hasn't run `/ai-film-setup` yet, mention it's available, but don't block on it here (the mock provider works with zero configuration, so a fresh project is still usable without it).

## Parse arguments

`$ARGUMENTS` is `"<Title>" [project-path]` — the title is required and quoted; the path is optional and defaults to `./<slugified-title>` (lowercase, spaces to hyphens) if omitted. Call the resolved path `PROJECT_PATH` for the rest of these instructions.

## Step 1: Scaffold or resume

Check whether `PROJECT_PATH/config.json` already exists.

- If it doesn't: run `AI_FILM_BIN init "<Title>" --path PROJECT_PATH` and confirm it succeeded.
- If it does: this is a resume — don't re-run `init` (it's safe to re-run since `init_project` only writes `config.json` if absent and directory creation is idempotent, but skip it anyway and tell the user you're resuming the existing project instead, so it's clear nothing was reset).

## Step 2: Run the Director/Story agent

Dispatch the `ai-film-director` subagent with `PROJECT_PATH` as its project root. Run the protocol loop above until it reports a genuine completion. Its final completion report ends with two lines, `CHARACTERS: <name1>, <name2>, ...` and `LOCATIONS: <name1>, <name2>, ...` — parse both lists; these are every unique character name and every unique location name found across all scenes. If `CHARACTERS:` is empty (nothing after the colon — a story with no named characters), skip Step 3 entirely and go to Step 4. If `LOCATIONS:` is empty, skip Step 4 entirely and go to Step 5.

## Step 3: Run the Character agent, once per unique name

For each name in the parsed `CHARACTERS` list, **one at a time, in order** (never in parallel — each run's protocol loop needs your real, in-order attention): dispatch the `ai-film-character` subagent with `PROJECT_PATH` and that one character name. Run the protocol loop above until it reports a genuine completion, then move to the next name.

## Step 4: Run the Environment agent, once per unique location name

For each name in the parsed `LOCATIONS` list, **one at a time, in order** (never in parallel — each run's protocol loop needs your real, in-order attention): dispatch the `ai-film-environment` subagent with `PROJECT_PATH` and that one location name. Run the protocol loop above until it reports a genuine completion, then move to the next name.

## Step 5: Run the Storyboard/Shot Director agent

Once every character from Step 3 is locked (or Step 3 was skipped because there were no characters) and every location from Step 4 is locked (or Step 4 was skipped because no scene named a location), dispatch the `ai-film-storyboard` subagent once, with `PROJECT_PATH` as its project root. It internally handles every scene and shot in one run — run the protocol loop above until it reports a genuine completion.

## Step 6: Run the Media agent, once per shot with a locked image

Compute `IN_SCOPE_SHOT_IDS` — every shot ID under `03_shots/*.json` whose `generation.image.artifact` is not `null` (every shot the Storyboard phase locked, this run or an earlier one):

```bash
python3 -c "
import json, glob
ids = []
for path in sorted(p for p in glob.glob('PROJECT_PATH/03_shots/*.json') if not p.endswith('.feedback.json')):
    shot = json.load(open(path))
    if shot['generation']['image'].get('artifact'):
        ids.append(shot['id'])
print(','.join(ids))
"
```

(substitute the real `PROJECT_PATH` for the literal text above; this reads plain JSON with the standard library only, no `ai_film` import needed, so it works with a bare `python3` regardless of which form `AI_FILM_BIN` resolved to in Step 0). This list is fixed **once**, before dispatching the first shot below — do not recompute it partway through this step, even if a later shot's dispatch changes what's on disk.

If the list is empty (no shot has a locked image yet — shouldn't happen after Step 5 completes normally, but possible if Step 5 was skipped or every shot failed continuity), skip this step entirely and go to Step 7.

For each shot ID in that list, **one at a time, in order** (never in parallel — each run's protocol loop needs your real, in-order attention): dispatch the `ai-film-media` subagent with `PROJECT_PATH`, that one shot ID, and the complete `IN_SCOPE_SHOT_IDS` list. Run the protocol loop above until it reports a genuine completion, then move to the next shot ID.

## Step 7: Wrap up

After every shot's Media agent run reports completion, show the user a summary (how many shots were reviewed and confirmed, any left with open feedback because the human asked to pause) and remind them that `/ai-film-setup` can be re-run anytime to change providers, and that `render` is the one remaining manual step, run directly via the `ai-film` CLI, same as documented in the project's README.
