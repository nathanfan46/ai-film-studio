---
description: Import a ComfyUI workflow and discuss/modify it in natural language via the ai-film-workflow agent.
argument-hint: "<path to workflow JSON>"
---

# /import-workflow

Dispatch the ai-film-workflow agent to import and discuss a ComfyUI workflow.

Usage: `/import-workflow <path to workflow JSON>`

This is a standalone, optional tool — it is not part of `/create-film` and does not require an existing ai-film-studio project.

## The human-in-the-loop protocol (you are the orchestrator side of this)

The `ai-film-workflow` subagent you dispatch below has no live channel to the user — only you do, since you're running in this actual conversation. Whenever it needs a real answer (confirming its understanding of the workflow, disambiguating which node a request refers to, confirming a `set-workflow-raw` edit, deciding how to proceed after a partial failure, choosing what to change next, or confirming an export is final), its final report ends with, verbatim:

```
NEEDS_INPUT:
id: <some id>
type: clarification | confirmation
question: <the question>
```

Whenever a dispatched subagent's report ends this way, you must:

1. Parse the `id` and `question`.
2. Ask the user that exact question, for real, in this conversation.
3. Once you have their real answer, **resume the SAME subagent dispatch you already have running** (the same instance/session, never a fresh dispatch — losing that instance loses the workflow id and every change discussed so far) with:

```
HUMAN_RESPONSE:
id: <the exact id from the NEEDS_INPUT you just relayed>
answer: <the user's answer>
```

4. Repeat this detect → ask → resume cycle for as long as the subagent's report keeps ending in `NEEDS_INPUT` — a single import/discuss/export session can take many round trips (the agent asks again after every mutation to find out what's next). A subagent's report that does **not** end in `NEEDS_INPUT` means the session has genuinely finished (the human confirmed a final export) — that's your signal to show the user the completion summary, not another round trip.

**Protocol errors — treat these as real errors, not something to guess past:** if a subagent's report doesn't parse as either a genuine completion or a well-formed `NEEDS_INPUT` block, tell the user the agent didn't follow the input protocol correctly and stop.

## Step 0: Preflight — resolve a working `ai-film` binary

Do this before dispatching the agent — there's no point starting a conversation that's guaranteed to fail on its first CLI call. Only ever use one of the two fixed forms below (never a machine-specific absolute path). Run each check as a single, plain command — never chain it with `; echo ...` or any other trailing command to inspect the exit code; your Bash tool already reports success/failure and any error output directly in its own result.

1. Run `ai-film version` by itself. If it succeeds, a working install exists — continue to `## Parse arguments` below (you don't need to remember which form worked; the `ai-film-workflow` agent you dispatch in Step 1 resolves this exact same value independently).
2. If that fails — command not found, or found but erroring (e.g. a stale/broken shim like `ModuleNotFoundError: No module named 'ai_film'`, meaning something else on `PATH` shadowed the real one) — run `./.venv/bin/ai-film version` by itself, relative to the current working directory. If that succeeds, continue to `## Parse arguments` below.
3. If neither works, check whether `./pyproject.toml` exists (confirms this is the right repo checkout, just not set up yet). If it does, tell the user no working `ai-film` install was found and ask whether you should set one up now (`python3 -m venv .venv` then `.venv/bin/pip install -e ".[dev]"`, matching the README's install step). If they say yes, run it, then retry step 2. If `./pyproject.toml` doesn't exist either, this isn't the `ai-film-studio` repo checkout at all — tell the user to run `claude` from inside it instead, and stop; there's nothing to self-heal here.

Unlike `/analyze-reference` or `/create-film`, there's no existing `ai-film-studio` project to check for here — `import-workflow`/`export-workflow` are not project-scoped, so this preflight is just the binary check above.

## Parse arguments

`$ARGUMENTS` is `<path to workflow JSON>` — the file path is required. Call the resolved value `FILE_PATH`.

## Step 1: Run the ai-film-workflow agent

Dispatch the `ai-film-workflow` subagent with `FILE_PATH` as its one instruction. Run the protocol loop above until it reports a genuine completion. You don't need to pass a resolved `AI_FILM_BIN` down — the agent resolves this exact same value independently, same as `ai-film-reference-analyst` does.

## Step 2: Wrap up

Show the user the agent's completion summary — what was imported, what was changed across the session, and where the exported workflow ended up.
