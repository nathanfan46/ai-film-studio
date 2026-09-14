---
description: Import a ComfyUI workflow and discuss/modify it in natural language via the ai-film-workflow agent.
argument-hint: "<path to workflow JSON>"
---

# /import-workflow

Dispatch the ai-film-workflow agent to import and discuss a ComfyUI workflow.

Usage: `/import-workflow <path to workflow JSON>`

Resolve the file path from the user's message, then dispatch the `ai-film-workflow` agent with that path as its one instruction. This is a standalone, optional tool — it is not part of `/create-film` and does not require an existing ai-film-studio project.
