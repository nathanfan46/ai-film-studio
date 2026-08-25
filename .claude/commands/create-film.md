---
description: Start or resume a film — scaffolds the project, then runs the Director, Character, and Storyboard agents in sequence through conversation.
argument-hint: "\"<Title>\" [project-path]"
---

# /create-film

The single entry point for starting or resuming a film with `ai-film-studio`. Scaffolds (or resumes) a project, then walks the whole story -> character -> shot -> locked storyboard image pipeline through conversation, dispatching the three pipeline agents in order and relaying your answers to them per the protocol below.

## The human-in-the-loop protocol (you are the orchestrator side of this)

The `ai-film-director`, `ai-film-character`, and `ai-film-storyboard` subagents you dispatch below have no live channel to the user themselves — only you do, since you're running in this actual conversation. Whenever one of them needs a real answer, its final report ends with, verbatim:

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

## Step 0: Preflight

Confirm `ai-film` resolves on `PATH` (e.g. `which ai-film`, or `ai-film version`) before doing anything else — if it doesn't, tell the user to activate the project's venv (`.venv/bin` on `PATH`, per the README) and stop; every later step assumes this works. This command does not itself check `FAL_KEY` or provider configuration — if the user hasn't run `/ai-film-setup` yet, mention it's available, but don't block on it here (the mock provider works with zero configuration, so a fresh project is still usable without it).

## Parse arguments

`$ARGUMENTS` is `"<Title>" [project-path]` — the title is required and quoted; the path is optional and defaults to `./<slugified-title>` (lowercase, spaces to hyphens) if omitted. Call the resolved path `PROJECT_PATH` for the rest of these instructions.

## Step 1: Scaffold or resume

Check whether `PROJECT_PATH/config.json` already exists.

- If it doesn't: run `ai-film init "<Title>" --path PROJECT_PATH` and confirm it succeeded.
- If it does: this is a resume — don't re-run `init` (it's safe to re-run since `init_project` only writes `config.json` if absent and directory creation is idempotent, but skip it anyway and tell the user you're resuming the existing project instead, so it's clear nothing was reset).

## Step 2: Run the Director/Story agent

Dispatch the `ai-film-director` subagent with `PROJECT_PATH` as its project root. Run the protocol loop above until it reports a genuine completion. Its final completion report ends with a line `CHARACTERS: <name1>, <name2>, ...` — parse that list; these are every unique character name found across all scenes. If the list is empty (`CHARACTERS:` with nothing after the colon — a story with no named characters), skip Step 3 entirely and go straight to Step 4.

## Step 3: Run the Character agent, once per unique name

For each name in the parsed `CHARACTERS` list, **one at a time, in order** (never in parallel — each run's protocol loop needs your real, in-order attention): dispatch the `ai-film-character` subagent with `PROJECT_PATH` and that one character name. Run the protocol loop above until it reports a genuine completion, then move to the next name.

## Step 4: Run the Storyboard/Shot Director agent

Once every character from Step 3 is locked (or Step 3 was skipped because there were no characters), dispatch the `ai-film-storyboard` subagent once, with `PROJECT_PATH` as its project root. It internally handles every scene and shot in one run — run the protocol loop above until it reports a genuine completion.

## Step 5: Wrap up

After the Storyboard agent's completion report, show the user its summary (shots locked, anything left unresolved) and remind them that `/ai-film-setup` can be re-run anytime to change providers, and that `generate-video`/`generate-voice`/`generate-sfx`/`generate-music`/`render` are manual next steps run directly via the `ai-film` CLI, same as documented in the project's README.
