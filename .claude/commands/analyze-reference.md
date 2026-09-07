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

Same resolution as `/create-film`'s Step 0: try `ai-film version` first; if that fails, try `./.venv/bin/ai-film version`; if neither works, offer to set up the venv. Call the resolved form `AI_FILM_BIN` and substitute it everywhere below. You don't need to pass `AI_FILM_BIN` down when dispatching the `ai-film-reference-analyst` subagent in Step 1 — it resolves this exact same value independently.

## Parse arguments

`$ARGUMENTS` is `<source-video-path> [project-path]` — the source path is required; the project path is optional and defaults to `.` (the current directory) if omitted. Call the resolved values `SOURCE_PATH` and `PROJECT_PATH`.

Confirm `PROJECT_PATH/config.json` exists before dispatching — if it doesn't, tell the user this isn't an initialized `ai-film-studio` project (run `/create-film` first) and stop.

## Step 1: Run the Reference Analyst agent

Dispatch the `ai-film-reference-analyst` subagent with `PROJECT_PATH` and `SOURCE_PATH`. Run the protocol loop above until it reports a genuine completion.

## Step 2: Wrap up

Show the user the agent's completion summary and remind them that `/create-film` (or a direct `ai-film-storyboard` re-dispatch, if the project already has a story) will now pick up the approved brief automatically as grounding context — no further action needed from them to "attach" it.
