---
description: Start or resume a film — scaffolds the project, then runs the Director, Character, and Storyboard agents in sequence through conversation.
argument-hint: "\"<Title>\" [project-path]"
---

# /create-film

The single entry point for starting or resuming a film with `ai-film-studio`. Scaffolds (or resumes) a project, then walks the whole story -> character -> shot -> locked storyboard image pipeline through conversation, dispatching the three pipeline agents in order.

## Parse arguments

`$ARGUMENTS` is `"<Title>" [project-path]` — the title is required and quoted; the path is optional and defaults to `./<slugified-title>` (lowercase, spaces to hyphens) if omitted. Call the resolved path `PROJECT_PATH` for the rest of these instructions.

## Step 1: Scaffold or resume

Check whether `PROJECT_PATH/config.json` already exists.

- If it doesn't: run `ai-film init "<Title>" --path PROJECT_PATH` and confirm it succeeded.
- If it does: this is a resume — don't re-run `init` (it's safe to re-run since `init_project` only writes `config.json` if absent and directory creation is idempotent, but skip it anyway and tell the user you're resuming the existing project instead, so it's clear nothing was reset).

## Step 2: Run the Director/Story agent

Dispatch the `ai-film-director` subagent with `PROJECT_PATH` as its project root. Wait for it to complete. Its final report ends with a line `CHARACTERS: <name1>, <name2>, ...` — parse that list; these are every unique character name found across all scenes. If the list is empty (a story with no named characters), skip Step 3 entirely and go straight to Step 4.

## Step 3: Run the Character agent, once per unique name

For each name in the parsed `CHARACTERS` list, **one at a time, in order** (never in parallel — each run needs live back-and-forth with the user over the candidate images): dispatch the `ai-film-character` subagent with `PROJECT_PATH` and that one character name. Wait for it to complete before dispatching the next one.

## Step 4: Run the Storyboard/Shot Director agent

Once every character from Step 3 is locked (or Step 3 was skipped because there were no characters), dispatch the `ai-film-storyboard` subagent once, with `PROJECT_PATH` as its project root. It internally handles every scene and shot in one run.

## Step 5: Wrap up

After the Storyboard agent reports back, show the user its summary (shots locked, anything left unresolved) and remind them that `/ai-film-setup` can be re-run anytime to change providers, and that `generate-video`/`generate-voice`/`generate-sfx`/`generate-music`/`render` are manual next steps run directly via the `ai-film` CLI, same as documented in the project's README.
