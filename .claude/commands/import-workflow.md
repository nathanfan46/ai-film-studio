---
description: Import a ComfyUI workflow and discuss/modify it in natural language via the ai-film-workflow agent.
argument-hint: "<path to workflow JSON>"
---

# /import-workflow

Dispatch the ai-film-workflow agent to import and discuss a ComfyUI workflow.

Usage: `/import-workflow <path to workflow JSON>`

Resolve the file path from the user's message, then dispatch the `ai-film-workflow` agent with that path as its one instruction. This is a standalone, optional tool — it is not part of `/create-film` and does not require an existing ai-film-studio project.

## The human-in-the-loop protocol (you are the orchestrator side of this)

The `ai-film-workflow` subagent you dispatch above has no live channel to the user — only you do, since you're running in this actual conversation. Whenever it needs a real answer (confirming its understanding of the workflow, disambiguating which node a request refers to, confirming a `set-workflow-raw` edit, deciding how to proceed after a partial failure, choosing what to change next, or confirming an export is final), its final report ends with, verbatim:

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
