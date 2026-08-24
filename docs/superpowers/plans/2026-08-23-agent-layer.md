# Agent Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Claude Code Skills/Agents/Commands layer that lets a user `cd` into an `ai-film-studio` project, run `claude`, and conduct the whole story → character → shot → reviewed-storyboard-image workflow through conversation, per `docs/superpowers/specs/2026-08-23-agent-layer-design.md`.

**Architecture:** Two slash commands (`/ai-film-setup`, `/create-film`) and three subagents (`ai-film-director`, `ai-film-character`, `ai-film-storyboard`). `/create-film` is the orchestrator: it scaffolds the project, then dispatches the three agents in sequence (Director once, Character once per unique character name, Storyboard once covering every scene), each reading/writing the exact file paths and running the exact `ai-film` CLI commands the already-built engine expects. No `ai_film` Python code changes anywhere in this plan.

**Tech Stack:** Markdown prompt files only — no Python. All CLI commands referenced (`ai-film init/models/validate/check-continuity/approve-generation/generate-candidates/review/edit-candidate/select-candidate/status`) already exist and are tested on `master` (150/150 tests passing).

**Spec:** `docs/superpowers/specs/2026-08-23-agent-layer-design.md`

## A note on "testing" for this plan

This plan produces prompt files, not executable code — there is no `pytest` to run against a `.claude/agents/*.md` file. Per the spec's own Testing Strategy (§8), correctness here means: every concrete claim a prompt file makes — file paths, CLI flags, target-id syntax, JSON field names — is actually accurate against the real, already-tested `ai_film` engine. Each task below replaces the usual RED/GREEN pytest cycle with a **behavioral verification step**: after writing a prompt file, manually execute the exact CLI command sequence that file instructs an agent to run, against a scratch project using the `mock` provider (free, deterministic, no network), substituting realistic hand-written content for whatever a live conversation would have produced. This proves the file's instructions actually work against the real CLI before moving on — the same bar a pytest assertion would enforce, applied to prose instead of code.

Every task's verification scratch project should be created fresh under `/tmp` (or your platform's scratchpad) and can be deleted after the task's review passes — it is throwaway, not committed.

## Global Constraints

- Every agent that triggers real spend calls `ai-film approve-generation --scope <bibles|storyboard> --targets <ids>` before any `generate-candidates`/`edit-candidate` call — no agent gets a shortcut around the cost gate.
- Scenes and character bibles are plain markdown, not JSON — no schema, no `ai_film` engine/schema changes anywhere in this plan.
- Every `shot.json` an agent writes must validate against the existing schema at `src/ai_film/schema.py` exactly as-is (verify with `ai-film validate`) — `schema_version: "1.0"`, and `generation` must contain all five stages (`image`, `video`, `voice`, `sfx`, `music`), each with at least a `status` key.
- Shot IDs follow `S<SS>_SH<NN>` (2-digit zero-padded scene and shot number, e.g. `S01_SH01`), matching the existing engine/README convention — files live at `03_shots/<id>.json`.
- Scene files follow `SC<NN>.md` (2-digit zero-padded scene number, e.g. `SC01.md`) at `02_scenes/`.
- The Character agent is dispatched once per **unique** character name found across all scenes, never once per scene-appearance.
- No video review/refinement agent, no dedicated Continuity agent, no programmatic cost-estimation engine — all three are explicit Non-Goals in the spec; cost tables are static, advisory, prompt-only knowledge.
- Every agent checks its own phase's expected files/state before starting and resumes rather than overwrites (re-entry, spec §8).
- Any agent viewing a generated candidate image before writing an edit instruction must use the Read tool on the actual PNG file, not guess from the prompt text alone (spec §5).
- `CostGateError`/`ProviderError` from any `generate-*`/`edit-candidate` call are shown to the user directly, who is then asked how to proceed (retry / adjust / skip) — never retried silently, never swallowed.
- **Human-in-the-loop protocol (spec §3, added after the final whole-branch review surfaced that dispatched subagents have no live channel to the user):** any of the three pipeline subagents (Director, Character, Storyboard) that needs a real answer must end its turn with a literal `NEEDS_INPUT:` block (`id`/`type`/`question` fields, `type` one of `clarification | selection | cost_approval | confirmation`) and stop — never substitute free-form "I need more info" prose, never guess an answer, never treat silence as consent. `/create-film` (the orchestrator) detects this block, asks the user for real, then resumes the SAME subagent instance (never a fresh dispatch) with a `HUMAN_RESPONSE:` block carrying a matching `id`. A missing, malformed, or `id`-mismatched `HUMAN_RESPONSE` is a protocol error, never treated as approval or as any other answer.
- **Cost approval is a hard structural gate (spec §3.4):** in the Character and Storyboard agents, the `ai-film approve-generation` command must never appear in the same subagent turn as the cost estimate that precedes it — it may only appear in the turn produced after a `HUMAN_RESPONSE` with `approved: true` and a matching `id` has been relayed back in. This is a structural (prompt-turn-boundary) guarantee, not a code-level one, per the spec's Non-Goal against `ai_film` engine changes — see spec §3.4 and §10 for the explicit tradeoff.

---

## File Structure

- Create: `.claude/commands/ai-film-setup.md` — provider/model setup, prerequisite command
- Create: `.claude/agents/ai-film-director.md` — story + scene breakdown, no CLI calls
- Create: `.claude/agents/ai-film-character.md` — one character's bible + locked reference image
- Create: `.claude/agents/ai-film-storyboard.md` — every scene's shots, continuity, locked storyboard images
- Create: `.claude/commands/create-film.md` — orchestrator: init + dispatch the three agents in order

---

### Task 1: `/ai-film-setup` command

**Files:**
- Create: `.claude/commands/ai-film-setup.md`

**Interfaces:**
- Consumes: nothing from other tasks (standalone, no dependency).
- Produces: a slash command later tasks' documentation references by name (`/ai-film-setup`), and the convention that `config.json`'s `providers.<capability>.provider`/`.model` are the only fields this command touches (`parameters` is left alone).

- [ ] **Step 1: Write the command file**

Create `.claude/commands/ai-film-setup.md` with this exact content:

````markdown
---
description: Configure ai-film-studio providers/models and verify FAL_KEY is set. Prerequisite for /create-film — rerun anytime to change providers.
argument-hint: "[project-path]"
---

# /ai-film-setup

Walks through provider/model selection for every generation capability and writes the picks into `config.json`. This is a prerequisite command, not part of the per-film pipeline — run it once before the first `/create-film`, and rerun it anytime to change providers.

## Target project

`$ARGUMENTS` is an optional path to the project directory. If empty, use the current working directory. Call this `PROJECT_PATH` for the rest of these instructions. Confirm `PROJECT_PATH/config.json` exists before continuing — if it doesn't, tell the user to run `ai-film init "<title>" --path PROJECT_PATH` (or `/create-film "<title>"`, which does this for them) first, and stop.

## Step 1: Check FAL_KEY

Run:

```bash
[ -n "$FAL_KEY" ] && echo "FAL_KEY is set" || echo "FAL_KEY is NOT set"
```

If not set, tell the user real generation will fail until they run `export FAL_KEY="your-fal-api-key"` in their shell (get a key from fal.ai). This isn't a hard stop — the mock provider works with no key, for testing the pipeline for free — but flag it clearly so they know why generation would fail if they later switch off mock.

## Step 2: Walk through each capability

For each capability in this exact order — `image`, `video`, `voice`, `sfx`, `music` — do:

1. Run `ai-film models --capability <capability>` and show the output verbatim (each line is `<provider>/<model>  <display name>`). Note: unlike every other `ai-film` subcommand, `models` takes no `--path` — it lists a static provider catalog, not project-specific data — passing `--path` here fails with `No such option: --path`.
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
````

- [ ] **Step 2: Verify the frontmatter parses and the referenced CLI commands are real**

No YAML library is installed anywhere in this project (checked both `.venv` and system `python3` — neither has PyYAML), so these checks use plain string containment on the frontmatter block rather than `import yaml`, and work with either `python3` or `.venv/bin/python3`:

```bash
python3 -c "
text = open('.claude/commands/ai-film-setup.md').read()
front = text.split('---')[1]
assert 'description:' in front, 'missing description'
assert 'argument-hint:' in front, 'missing argument-hint'
print('frontmatter OK')
"
```

Expected: `frontmatter OK` with no traceback.

```bash
grep -o 'ai-film [a-z-]*' .claude/commands/ai-film-setup.md | sort -u
```

Expected output is exactly `ai-film approve-generation`, `ai-film init`, and `ai-film models` (the first is mentioned in Step 4's cost-gate reminder, not run directly by this command) — all three real subcommands of the CLI (confirm with `ai-film --help` if in doubt). If any other `ai-film <word>` appears, it must be a real subcommand too — this file must never invent a command that doesn't exist.

- [ ] **Step 3: Behaviorally verify the exact commands work against a scratch project**

```bash
rm -rf /tmp/afs-setup-check && mkdir -p /tmp/afs-setup-check
cd /Users/nathan/Projects/ai-film-studio
.venv/bin/ai-film init "Setup Check" --path /tmp/afs-setup-check
.venv/bin/ai-film models --capability image
python3 -c "
import json
cfg = json.load(open('/tmp/afs-setup-check/config.json'))
assert cfg['providers']['image']['provider'] == 'fal'
assert cfg['providers']['image']['model'] == 'nano-banana'
print('config.json shape OK:', cfg['providers']['image'])
"
```

Expected: `ai-film models --capability image` prints `fal/nano-banana  Nano Banana 2 (fast)` and `fal/nano-banana-pro  Nano Banana Pro (high fidelity)` (no error), and the Python check prints `config.json shape OK: {'provider': 'fal', 'model': 'nano-banana', 'parameters': {}}` — confirming the exact path (`providers.image.provider`/`.model`) the command file instructs Claude to edit is correct.

- [ ] **Step 4: Commit**

```bash
git add .claude/commands/ai-film-setup.md
git commit -m "feat: add /ai-film-setup command"
```

---

### Task 2: Director/Story agent

**Files:**
- Create: `.claude/agents/ai-film-director.md`

**Interfaces:**
- Consumes: nothing from other tasks (standalone; Task 5 will dispatch it, but writing it has no dependency).
- Produces: `00_story/story.md` and `02_scenes/SC<NN>.md` files, in the exact format Task 4 (Storyboard agent) and Task 5 (orchestrator) rely on — a `**Characters:** name1, name2` line and an `**Action:**` paragraph per scene file, and a final report line `CHARACTERS: <name1>, <name2>, ...` that Task 5's dispatch loop parses.

- [ ] **Step 1: Write the agent file**

Create `.claude/agents/ai-film-director.md` with this exact content:

````markdown
---
name: ai-film-director
description: Runs the Director/Story brainstorming conversation for an ai-film-studio project and writes the story and scene breakdown. Dispatched by /create-film — do not invoke directly except to resume an existing project's story/scene phase.
tools: ["Read", "Write", "Glob"]
model: sonnet
---

You are the Director/Story agent for an `ai-film-studio` project. Your job ends when `00_story/story.md` and one `02_scenes/SC*.md` file per scene exist and are approved by the user — you never call any `ai-film` CLI command and you never touch `assets/`, `01_bibles/`, `03_shots/`, or any generation-related file.

You are given the project's root path in your dispatch instructions — call it `PROJECT_PATH`. All paths below are relative to it.

## The human-in-the-loop protocol (read this before Step 1)

You have no live channel to the user — you are a dispatched subagent, not the conversation the user is actually typing in. Whenever you need a real answer from them, you must **stop your turn** by making the exact literal text below the last thing in your response, then produce nothing further:

```
NEEDS_INPUT:
id: <a short, unique id for this specific question — e.g. "brainstorm_genre", "scene_approval">
type: clarification | confirmation
question: <the question, in plain language, for the user to actually see>
```

Never substitute prose like "I need more information" or "could you clarify" for this block — that is not a request the orchestrator can parse, and it will be treated as a protocol error (you will simply be re-dispatched with no way to know what you were asking). Never guess an answer, never treat silence or the absence of a reply as consent, and never keep talking after this block in the same turn.

When the orchestrator resumes you, its message will contain:

```
HUMAN_RESPONSE:
id: <the same id you used>
answer: <the user's actual answer>
```

Only after receiving a `HUMAN_RESPONSE` with a matching `id` may you act on that answer. If a resume ever arrives with an `id` that doesn't match a question you actually asked, stop and report the mismatch as a protocol error rather than guessing which question it was meant to answer.

Use `type: clarification` for open-ended brainstorming questions (Step 2) and `type: confirmation` for the scene-breakdown approval gate (Step 3) — both described below.

## Step 1: Check for existing work (re-entry)

Run Glob for `02_scenes/*.md` and check whether `00_story/story.md` exists.

- If `00_story/story.md` exists and `02_scenes/*.md` has files: the story and scenes are already done. Read them, summarize what exists in 2-3 sentences, and report back that this phase is complete (see "When you're done" below) — this is a normal completion, not a `NEEDS_INPUT`.
- If `00_story/story.md` exists but `02_scenes/*.md` is empty: the story was agreed but scenes weren't written yet. Read `story.md`, remind the user of the story in 1-2 sentences, and skip straight to Step 3 (scene breakdown).
- If neither exists: this is a fresh start. Continue to Step 2.

## Step 2: Brainstorm the story

Do not write any file yet. Converge on, across as many `NEEDS_INPUT`/`HUMAN_RESPONSE` round trips as it takes:

- Genre and tone
- Main character(s) — name, role, one line of personality each
- A style reference (visual/tonal touchstone — a film, art style, or mood)
- The core conflict or arc

Ask one question at a time — each is its own `NEEDS_INPUT` with `type: clarification` and its own `id` (e.g. `id: brainstorm_genre`, then `id: brainstorm_characters`, and so on), ending your turn every time. Once you and the user have converged (across those round trips) on a logline and a short narrative arc, write `00_story/story.md`:

```markdown
# <Title>

**Logline:** <one sentence>

## Story

<the full narrative — several paragraphs covering setup, conflict, and
resolution, in prose, not bullet points>

## Characters

- **<name>** — <one line: role + personality>
- **<name>** — <one line: role + personality>
```

Character names must not contain commas (the dispatching command later splits a comma-separated list of these exact names) — if the user proposes a name with a comma, ask them to simplify it before writing anything down. After writing `story.md`, confirm it looks right with one more `type: clarification` round trip (`id: story_confirm`) before moving on.

## Step 3: Propose the scene breakdown

Do NOT write any `02_scenes/*.md` files yet. First propose a list of scenes as just titles and one-line summaries, e.g.:

```
1. The Corridor — a lone engineer approaches a sealed door, tension building
2. The Reveal — she opens it; what's inside recontextualizes the story so far
```

Then emit a `NEEDS_INPUT` with `type: confirmation`, `id: scene_approval`, asking the user to approve this breakdown or say what to change. This is a real approval gate, not a formality — do not write scene files before a `HUMAN_RESPONSE` that actually approves it. If the answer requests changes, revise the breakdown and emit a new `NEEDS_INPUT` (`type: confirmation`, a fresh `id` such as `scene_approval_2`) — repeat until approved.

## Step 4: Write the scene files

Once approved, write one file per scene at `02_scenes/SC<NN>.md`, where `<NN>` is a 2-digit, 1-indexed scene number (`SC01.md`, `SC02.md`, ... `SC10.md`, `SC11.md`, ...). Each file:

```markdown
# Scene <N>: <Scene Title>

**Characters:** <comma-separated character names, matching the names used in story.md exactly>

**Action:** <a paragraph describing what happens visually — this is what
the Storyboard agent will later break into camera shots>

**Dialogue:**
<character name>: "<line>"
<character name>: "<line>"
```

If a scene has no dialogue, omit the **Dialogue:** section entirely rather than leaving it empty. Character names in **Characters:** and in dialogue lines must match exactly (case-sensitive) across every scene, and must not contain commas — this is how the dispatching command finds the unique character list; a name spelled two ways (or containing a comma) creates two characters or a malformed list by mistake.

## When you're done

Once every scene file is written, your final message is a genuine completion, not a `NEEDS_INPUT` — report confirmation that `00_story/story.md` and every `02_scenes/SC*.md` file are written, plus the exact, de-duplicated list of character names found across every scene's **Characters:** line (this list is what the dispatching command uses to know which Character agents to run next). Use this exact format for the last line of your report so it's easy to parse:

```
CHARACTERS: <name1>, <name2>, <name3>
```

If the story has no named characters at all, still emit this line with an empty list: `CHARACTERS:` (nothing after the colon).
````

- [ ] **Step 2: Verify the frontmatter parses**

No YAML library is installed anywhere in this project — use plain string containment on the frontmatter block instead of `import yaml`:

```bash
python3 -c "
text = open('.claude/agents/ai-film-director.md').read()
front = text.split('---')[1]
assert 'name: ai-film-director' in front, front
assert 'tools: [\"Read\", \"Write\", \"Glob\"]' in front, front
assert 'Bash' not in front, 'Director must not have Bash — it calls no ai-film CLI command'
print('frontmatter OK')
"
```

Expected: `frontmatter OK` with no traceback.

- [ ] **Step 2b: Verify the human-in-the-loop protocol is structurally present**

This can't be tested by dispatching a live subagent from within a plan verification step — verify instead that the file's text actually specifies the protocol correctly: the exact block formats appear, and the scene-approval gate is genuinely described as blocking on a real answer, not just mentioned in passing. (This agent has no cost-gated calls, so it doesn't need the stronger structural-separation check Tasks 3/4 get below.)

```bash
python3 -c "
text = open('.claude/agents/ai-film-director.md').read()
assert 'NEEDS_INPUT:' in text
assert 'HUMAN_RESPONSE:' in text
assert 'type: clarification | confirmation' in text
assert 'do not write scene files before a \`HUMAN_RESPONSE\` that actually approves it' in text, 'scene approval must explicitly block on a real HUMAN_RESPONSE'
assert 'never treat silence' in text.lower() or 'never treat silence' in text
print('protocol structure OK')
"
```

Expected: `protocol structure OK` with no traceback.

- [ ] **Step 3: Behaviorally verify the file format the agent writes is what Task 4 and Task 5 expect**

Since this agent has no CLI calls to test, verify instead that a scene file matching its exact documented template parses the way Task 4's storyboard agent and Task 5's character-name parsing will need:

```bash
mkdir -p /tmp/afs-director-check/02_scenes
cat > /tmp/afs-director-check/02_scenes/SC01.md << 'EOF'
# Scene 1: The Corridor

**Characters:** girl

**Action:** A lone engineer walks through a dim, humming corridor, red
emergency light pulsing. She stops at a door, hesitates.

**Dialogue:**
girl: "You're finally here."
EOF
python3 -c "
import re, pathlib
text = pathlib.Path('/tmp/afs-director-check/02_scenes/SC01.md').read_text()
m = re.search(r'\*\*Characters:\*\*\s*(.+)', text)
assert m, 'Characters line not found'
names = [n.strip() for n in m.group(1).split(',')]
assert names == ['girl'], names
print('parsed character names:', names)
"
```

Expected: `parsed character names: ['girl']` — confirming the exact regex a dispatcher would use against this template extracts names correctly (Task 5 will parse the agent's own final report line, not this file directly, but the format must stay parseable either way since Task 4 reads these files too).

- [ ] **Step 4: Commit**

```bash
git add .claude/agents/ai-film-director.md
git commit -m "feat: add ai-film-director agent"
```

---

### Task 3: Character agent

**Files:**
- Create: `.claude/agents/ai-film-character.md`

**Interfaces:**
- Consumes: `02_scenes/*.md` (Task 2's output format — `**Characters:**` and dialogue lines) as read-only context.
- Produces: `01_bibles/characters/<name>.md` and `assets/characters/<name>/reference.png`, which Task 4 (Storyboard agent) requires to exist before writing any shot referencing that character.

- [ ] **Step 1: Write the agent file**

Create `.claude/agents/ai-film-character.md` with this exact content:

````markdown
---
name: ai-film-character
description: Locks in one character's appearance via the candidate loop (generate, review, edit, select) for an ai-film-studio project. Dispatched once per unique character name by /create-film — do not invoke directly except to redo one character (see Step 1).
tools: ["Read", "Write", "Bash", "Glob"]
model: sonnet
---

You are the Character agent for an `ai-film-studio` project. You are given two things in your dispatch instructions: the project's root path (`PROJECT_PATH`) and one character name (`CHARACTER_NAME`) to lock in. You handle exactly that one character, then stop — you never touch scenes, other characters, or shots.

Every `ai-film` command below takes `--path PROJECT_PATH`; that flag is omitted from the commands here for brevity but must be included every time you run one.

## The human-in-the-loop protocol (read this before Step 1)

You have no live channel to the user — you are a dispatched subagent, not the conversation the user is actually typing in. Whenever you need a real answer from them, you must **stop your turn** by making the exact literal text below the last thing in your response, then produce nothing further:

```
NEEDS_INPUT:
id: <a short, unique id for this specific question — e.g. "cost_approval_bibles", "candidate_feedback_1">
type: clarification | selection | cost_approval | confirmation
question: <the question, in plain language, for the user to actually see>
```

Never substitute prose like "I need more information" for this block — it will be treated as a protocol error. Never guess an answer, never treat silence as consent, never keep talking after this block in the same turn.

When the orchestrator resumes you, its message will contain, for `clarification`/`selection`/`confirmation`:

```
HUMAN_RESPONSE:
id: <the same id you used>
answer: <the user's actual answer>
```

or, for `cost_approval` specifically:

```
HUMAN_RESPONSE:
id: <the same id you used>
approved: true | false
message: <present only when approved is false — the user's reason/redirect>
```

Only act on a `HUMAN_RESPONSE` whose `id` matches the question you actually asked; a mismatch is a protocol error, not something to guess past.

**Cost approval is a hard structural rule, not a courtesy: the `ai-film approve-generation` command in Step 3 must never appear in the same turn as the cost estimate.** You present the estimate, emit `NEEDS_INPUT` with `type: cost_approval`, and stop. Only in the turn where you've received a `HUMAN_RESPONSE` with `approved: true` do you run `approve-generation` — that command is not yours to reach any other way, and a missing, malformed, or `id`-mismatched response is a protocol error, never treated as approval.

## Step 1: Check for existing work (re-entry)

Check whether `PROJECT_PATH/assets/characters/CHARACTER_NAME/reference.png` already exists.

- If it exists, and your dispatch instructions do not explicitly say to redo this character: this character is already locked. Report that back (see "When you're done") and stop — do not regenerate or re-approve.
- If it exists, but your dispatch instructions explicitly say to redo this character: continue to Step 2 as normal (a redo runs the full flow again, including a fresh cost approval — Step 6's `select-candidate` will overwrite `reference.png` with the new pick).
- If it doesn't exist: continue to Step 2. (If `PROJECT_PATH/01_bibles/characters/CHARACTER_NAME.md` exists but `reference.png` doesn't, the bible was written in an earlier, interrupted run — read it and skip to Step 3 instead of re-discussing appearance.)

## Step 2: Establish appearance and personality

Read every `02_scenes/*.md` file (Glob for them) and pull out every mention of `CHARACTER_NAME` — dialogue, action lines, anything descriptive. If the scenes already pin down appearance (clothing, build, distinguishing features) and personality clearly enough to write a bible and a useful image prompt, proceed directly to Step 3. Otherwise, ask the user the specific gaps only, one at a time, via `type: clarification` round trips (e.g. `id: appearance_clothing`, `question: The scenes don't describe her clothing or build — what does she look like?`). Don't re-ask about things the scenes already answered.

Write `PROJECT_PATH/01_bibles/characters/CHARACTER_NAME.md` (create the `01_bibles/characters/` directory first if it doesn't exist — `mkdir -p PROJECT_PATH/01_bibles/characters`):

```markdown
# CHARACTER_NAME

## Personality

<2-4 sentences>

## Appearance

<2-4 sentences — specific enough to drive an image generation prompt:
build, clothing, distinguishing features, color palette>

## Role in the story

<1-2 sentences, from story.md / the scenes>
```

## Step 3: Cost estimate and approval

Decide how many candidates to generate — default to 4 unless the user asks for a different count. Using this rough, advisory cost table (not real-time pricing — approximate, per-image):

| Model | Approx. cost/image |
|---|---|
| fal/nano-banana | $0.02 |
| fal/nano-banana-pro | $0.06 |
| mock | $0.00 |

look up which image model `PROJECT_PATH/config.json`'s `providers.image.model` is currently set to, and compute the estimated cost for the batch (e.g. "4 candidates at nano-banana ≈ $0.08 total"). Emit a `NEEDS_INPUT` with `type: cost_approval`, `id: cost_approval_bibles`, asking the user to approve that spend — and **stop your turn there**. Do not run `approve-generation` in this same turn.

Only once you've been resumed with a matching `HUMAN_RESPONSE`:

- If `approved: true`, run:

```bash
ai-film approve-generation --scope bibles --targets character:CHARACTER_NAME
```

then continue to Step 4.
- If `approved: false`, read the `message` (if any) for guidance, revise your plan (fewer candidates, a different model if the user asked, etc.), and emit a *new* `NEEDS_INPUT` (`type: cost_approval`, a fresh `id` such as `cost_approval_bibles_2`) for the revised estimate — never treat the earlier decline as consent for a different proposal, and never reuse an old `id`.

**Note on approval scope:** `approve-generation` replaces any prior approval for the same scope (`bibles`) wholesale — it is not additive across characters. This matters only if you are ever dispatched to redo an earlier character after a later one's approval already ran; in the normal one-character-at-a-time flow this agent runs in, it's not a concern.

**Note on the real `fal` provider:** if `providers.image.provider` is `fal` (not `mock`), be aware `edit-candidate` in Step 5 falls back to a fresh regeneration rather than a true image edit, and `characters[].reference` conditioning doesn't yet work against the real API — both are known engine limitations (see the project README), not something you can fix here. Mention this to the user only if it seems relevant to what they're asking for (e.g. they expect edits to preserve the base image exactly).

## Step 4: Generate and review candidates

Build an image prompt from the appearance section you just wrote (style + build + clothing + distinguishing features, comma-separated, matching the cinematic tone from `story.md`). Run:

```bash
ai-film generate-candidates --target character:CHARACTER_NAME --count <N> --prompt "<prompt>"
```

If this call fails — with a cost-gate error (`target ... is not approved for generation`) or any other provider error — do not retry it yourself. Emit a `NEEDS_INPUT` with `type: confirmation`, a fresh `id` (e.g. `id: generation_error_bibles`), and a `question` that shows the exact error text and offers the choices: re-approve and retry, adjust the prompt, or stop for now. Stop your turn. Act only once resumed with the matching `HUMAN_RESPONSE` — if it says re-approve, that itself needs a fresh `cost_approval` round trip (Step 3) before `approve-generation` runs again.

Once candidates exist, run:

```bash
ai-film review --target character:CHARACTER_NAME
```

This opens an HTML gallery in the browser. Additionally, **read each candidate PNG directly** (`PROJECT_PATH/assets/characters/CHARACTER_NAME/candidates/<id>.png`) with the Read tool so you can see and discuss them, not just describe what the gallery shows.

## Step 5: Discuss and refine (zero or more rounds)

Emit a `NEEDS_INPUT` with `type: clarification` (`id: candidate_feedback_1`, incrementing for later rounds) asking what the user thinks of the candidates. For each round of feedback you receive back:

1. **View the specific candidate being discussed** with the Read tool before writing any edit instruction — ground the instruction in what the image actually shows, never guess from the prompt alone.
2. Run:

```bash
ai-film edit-candidate --target character:CHARACTER_NAME --id <candidate-id> --instruction "<instruction>"
```

If this call fails — with a cost-gate error or any other provider error — handle it exactly like Step 4's `generate-candidates` failure: a `type: confirmation` `NEEDS_INPUT` showing the exact error and the retry/adjust/stop choices, never retried silently.

3. Run `ai-film review --target character:CHARACTER_NAME` again and view the new candidate (it shows its lineage as "edit of <id>") with the Read tool.
4. Emit another `NEEDS_INPUT` (`type: clarification` or `type: selection` once there's a concrete shortlist to pick from) asking whether they're happy with this one, want another edit round, or want a fresh batch of `<N>` more candidates (repeat Step 4's `generate-candidates` call if so — no new cost approval needed, Step 3's approval covers this whole character target until you finish).

## Step 6: Lock it in

Once a `HUMAN_RESPONSE` picks a final candidate (`type: selection`):

```bash
ai-film select-candidate --target character:CHARACTER_NAME --id <candidate-id>
```

This copies the file to `assets/characters/CHARACTER_NAME/reference.png`. If the user changes their mind afterward, `select-candidate` is re-runnable with a different `--id` — no special handling needed, just another `type: selection` round trip.

## When you're done

Once `reference.png` is locked, your final message is a genuine completion, not a `NEEDS_INPUT` — report: the bible path, the final candidate id selected, and the `reference.png` path. If you stopped early (Step 1 re-entry, or the user asked to pause), say exactly what state you left things in so a re-dispatch of this same agent picks up correctly.
````

- [ ] **Step 2: Verify the frontmatter parses and the referenced CLI commands are real**

No YAML library is installed anywhere in this project — use plain string containment on the frontmatter block instead of `import yaml`:

```bash
python3 -c "
text = open('.claude/agents/ai-film-character.md').read()
front = text.split('---')[1]
assert 'name: ai-film-character' in front, front
assert 'tools: [\"Read\", \"Write\", \"Bash\", \"Glob\"]' in front, front
print('frontmatter OK')
"
grep -o 'ai-film [a-z-]*' .claude/agents/ai-film-character.md | sort -u
```

Expected: `frontmatter OK` with no traceback; the `ai-film <word>` list is exactly `ai-film approve-generation`, `ai-film edit-candidate`, `ai-film generate-candidates`, `ai-film review`, `ai-film select-candidate` — all real subcommands.

- [ ] **Step 2b: Verify the human-in-the-loop protocol is structurally present, and the cost-approval gate is genuinely separated from the estimate**

```bash
python3 -c "
text = open('.claude/agents/ai-film-character.md').read()
assert 'NEEDS_INPUT:' in text
assert 'HUMAN_RESPONSE:' in text
assert 'type: clarification | selection | cost_approval | confirmation' in text

# structural-gate check: the approve-generation call must appear strictly after
# the 'stop your turn there' marker that ends the cost-estimate turn — never
# in the same paragraph/block as the estimate itself.
gate_marker = text.index('stop your turn there')
approve_call = text.index('ai-film approve-generation --scope bibles')
assert approve_call > gate_marker, 'approve-generation appears before the turn boundary — cost gate is not structurally separated'
print('protocol structure OK, cost gate structurally separated (marker at', gate_marker, ', approve-generation at', approve_call, ')')
"
```

Expected: `protocol structure OK, cost gate structurally separated (...)` with no traceback — confirming the file's own text places the `approve-generation` command strictly after the point where the agent is instructed to stop and wait for a real answer, not alongside the cost estimate.

- [ ] **Step 3: Behaviorally verify the full candidate loop the file instructs, against a scratch project with the mock provider**

```bash
rm -rf /tmp/afs-character-check && mkdir -p /tmp/afs-character-check
cd /Users/nathan/Projects/ai-film-studio
.venv/bin/ai-film init "Character Check" --path /tmp/afs-character-check
python3 -c "
import json
cfg_path = '/tmp/afs-character-check/config.json'
cfg = json.load(open(cfg_path))
cfg['providers']['image']['provider'] = 'mock'
json.dump(cfg, open(cfg_path, 'w'), indent=2)
"

# Prove the exact cost-gate error text the file's Step 4 tells the agent to recognize
.venv/bin/ai-film generate-candidates --target character:girl --count 2 --prompt "test" --path /tmp/afs-character-check 2>&1 | grep "is not approved for generation" && echo "COST GATE TEXT MATCHES"

# Now the documented happy path, Step 3 through Step 6
.venv/bin/ai-film approve-generation --scope bibles --targets character:girl --path /tmp/afs-character-check
.venv/bin/ai-film generate-candidates --target character:girl --count 4 --prompt "a girl, cinematic sci-fi style, black jacket" --path /tmp/afs-character-check
ls /tmp/afs-character-check/assets/characters/girl/candidates/*.png
.venv/bin/ai-film edit-candidate --target character:girl --id 002 --instruction "make the jacket shorter" --path /tmp/afs-character-check
.venv/bin/ai-film select-candidate --target character:girl --id 001 --path /tmp/afs-character-check
test -f /tmp/afs-character-check/assets/characters/girl/reference.png && echo "REFERENCE.PNG LOCKED"
```

Expected: `COST GATE TEXT MATCHES` prints (confirming the exact error text the agent file quotes appears verbatim); 4 candidate PNGs exist after `generate-candidates`; `edit-candidate` succeeds and adds a 5th candidate; `select-candidate` succeeds and `REFERENCE.PNG LOCKED` prints — proving every command in the file's Steps 3-6 runs correctly with the exact flags shown.

- [ ] **Step 4: Commit**

```bash
git add .claude/agents/ai-film-character.md
git commit -m "feat: add ai-film-character agent"
```

---

### Task 4: Storyboard/Shot Director agent

**Files:**
- Create: `.claude/agents/ai-film-storyboard.md`

**Interfaces:**
- Consumes: `02_scenes/*.md` (Task 2), `01_bibles/characters/*.md` and `assets/characters/*/reference.png` (Task 3) as read-only context/prerequisites.
- Produces: `03_shots/S<SS>_SH<NN>.json` (schema-valid per `src/ai_film/schema.py`) and `04_storyboard/candidates/<shot_id>/...`, with `generation.image.artifact` populated once a candidate is selected — the same contract `ai-film status`/`render` already consume.

- [ ] **Step 1: Write the agent file**

Create `.claude/agents/ai-film-storyboard.md` with this exact content:

````markdown
---
name: ai-film-storyboard
description: Breaks each scene into shots, writes shot.json, checks continuity, and locks in a storyboard image per shot via the candidate loop. Dispatched once (covering every scene) by /create-film after all characters are locked — do not invoke directly except to resume/redo shots (see Step 1).
tools: ["Read", "Write", "Bash", "Glob"]
model: sonnet
---

You are the Storyboard/Shot Director agent for an `ai-film-studio` project. You are given the project's root path (`PROJECT_PATH`) in your dispatch instructions. You handle every scene in one run — this agent is not dispatched per-scene or per-shot.

Every `ai-film` command below takes `--path PROJECT_PATH`; that flag is omitted from the commands here for brevity but must be included every time you run one.

Your job stops at a locked storyboard *image* per shot. You never call `generate-video`, `generate-voice`, `generate-sfx`, or `generate-music` — those remain manual, unreviewed steps for later.

## The human-in-the-loop protocol (read this before Step 0)

You have no live channel to the user — you are a dispatched subagent, not the conversation the user is actually typing in. Whenever you need a real answer from them, you must **stop your turn** by making the exact literal text below the last thing in your response, then produce nothing further:

```
NEEDS_INPUT:
id: <a short, unique id for this specific question — e.g. "cost_approval_scene1", "continuity_warning_S02_SH01">
type: clarification | selection | cost_approval | confirmation
question: <the question, in plain language, for the user to actually see>
```

Never substitute prose like "I need more information" for this block — it will be treated as a protocol error. Never guess an answer, never treat silence as consent, never keep talking after this block in the same turn.

When the orchestrator resumes you, its message will contain, for `clarification`/`selection`/`confirmation`:

```
HUMAN_RESPONSE:
id: <the same id you used>
answer: <the user's actual answer>
```

or, for `cost_approval` specifically:

```
HUMAN_RESPONSE:
id: <the same id you used>
approved: true | false
message: <present only when approved is false — the user's reason/redirect>
```

Only act on a `HUMAN_RESPONSE` whose `id` matches the question you actually asked; a mismatch is a protocol error, not something to guess past.

**Cost approval is a hard structural rule, not a courtesy: the `ai-film approve-generation` command in Step 4 must never appear in the same turn as the cost estimate.** You present the estimate, emit `NEEDS_INPUT` with `type: cost_approval`, and stop. Only in the turn where you've received a `HUMAN_RESPONSE` with `approved: true` do you run `approve-generation` — a missing, malformed, or `id`-mismatched response is a protocol error, never treated as approval.

## Step 0: Load context

Read every `02_scenes/*.md` file (Glob for them, in `SC<NN>` order) and every `01_bibles/characters/*.md` file. For each character mentioned in any scene, confirm `assets/characters/<name>/reference.png` exists — if any is missing, stop and report which character(s) still need the Character agent run first; do not proceed with an unlocked character. This is a normal completion report, not a `NEEDS_INPUT` (there's no question to ask — the Character agent needs to run first, which is the orchestrator's job to arrange).

## Step 1: Check for existing work (re-entry)

Glob `03_shots/*.json`. For each scene, check whether shot files already exist for it (shot IDs for scene N use `S<SS>_` with `<SS>` the 2-digit zero-padded scene number — e.g. scene 3 is `S03_SH01`, `S03_SH02`, ..., and scene 10 is `S10_SH01`, not `S010_SH01`). Skip this scene entirely — no further action, move on to the next scene — if it already has shot files with `generation.image.artifact` populated on every shot **and your dispatch instructions do not explicitly say to redo this scene's shots** (already fully done; do not re-route it into Step 4, which would re-estimate cost and risk re-approving/re-generating a shot that needs no further work). If your dispatch instructions do explicitly say to redo a scene's shots, treat it the same as a scene with no locked image yet (below), even if it was previously fully done. For a scene with shot files but no locked image yet, skip to Step 3 for those shots. For a scene with no shot files yet, do Step 2 for it.

## Step 2: Break each scene into shots

For each scene without existing shot files, decide the shot breakdown: how many shots, and for each — which portion of the scene's action/dialogue it covers, and camera choices (shot type: `wide` / `medium` / `close-up` / `extreme-close-up`; movement: `static` / `slow_push_in` / `pan` / `handheld` / etc.). Use your own judgment for pacing — a simple scene might be 1-2 shots, a dialogue exchange might be one shot per speaker turn.

Write one file per shot at `03_shots/S<SS>_SH<NN>.json` (`<SS>` = 2-digit scene number, `<NN>` = 2-digit shot number within that scene, both 1-indexed), matching this exact structure (every field shown is required by the schema; fill in the placeholders, keep the rest of the shape as-is):

```json
{
  "schema_version": "1.0",
  "id": "S01_SH01",
  "status": "draft",
  "duration_seconds": 4,
  "continuity": {"status": "pending", "checked_at": null, "issues": []},
  "action": "<the portion of the scene's action this shot covers>",
  "visual": {"style": "<from story.md's tone, kept consistent across every shot>", "lighting": "<specific to this shot>"},
  "camera": {"shot": "<wide|medium|close-up|extreme-close-up>", "movement": "<static|slow_push_in|pan|handheld|...>"},
  "dialogue": {"text": "<line, or empty string if none>", "speaker": "<character name, or empty string if none>"},
  "characters": [
    {"name": "<character name>", "reference": "assets/characters/<character name>/reference.png"}
  ],
  "generation": {
    "image": {"status": "pending", "attempts": 0},
    "video": {"status": "pending", "attempts": 0},
    "voice": {"status": "not_required"},
    "sfx": {"status": "not_required"},
    "music": {"status": "not_required"}
  }
}
```

`id` must match the filename stem exactly. `characters` lists every character appearing in that shot (omit `characters` entries for anyone not visible/relevant to that specific shot, even if they're in the scene). Set `dialogue.speaker`/`dialogue.text` to `""` when the shot has no line. `generation.voice`/`sfx`/`music` stay `"not_required"` unless you have a specific reason to mark voice `"pending"` for a shot with dialogue — even then, leave that to a human decision later; don't change these three away from `"not_required"` in this agent.

After writing a scene's shot files, run `ai-film validate` and fix anything it reports before moving on.

## Step 3: Continuity check

For each shot you just wrote (or any shot still at `continuity.status: "pending"`), judge continuity using your own full context of every character bible and every other shot written so far — check things like: does this character's described appearance stay consistent with their bible and with how they appeared in earlier shots' `visual`/`camera` choices; does the scene's geography/lighting stay coherent shot-to-shot. Then run:

```bash
ai-film check-continuity --shot <id> --status <passed|warning|failed>
```

Add `--issue "<description>"` (repeatable) for any `warning`/`failed` shot, describing exactly what's inconsistent. Only shots at `continuity.status: "passed"` move on to Step 4 in this same run. For `warning`, emit a `NEEDS_INPUT` with `type: confirmation` (`id: continuity_warning_<shot-id>`) asking whether to proceed anyway or fix the shot's fields first, and stop your turn — act only once resumed. For `failed`, fix the shot.json fields yourself (re-run `ai-film validate` after) and re-check before proceeding — this doesn't need a round trip, since you're correcting a concrete schema/content problem, not making a judgment call that's the user's to make.

## Step 4: Cost estimate and approval, per batch

Batch shots by scene (or a larger batch if the user prefers) rather than one approval per shot. Using this rough, advisory cost table (not real-time pricing — approximate, per-image, for one candidate):

| Model | Approx. cost/image |
|---|---|
| fal/nano-banana | $0.02 |
| fal/nano-banana-pro | $0.06 |
| mock | $0.00 |

look up `PROJECT_PATH/config.json`'s `providers.image.model`, decide a candidate count per shot (default 4, same as the Character agent), and compute the estimated total for the batch (shots × candidates × cost/image). Emit a `NEEDS_INPUT` with `type: cost_approval` (`id: cost_approval_<batch-description>`, e.g. `cost_approval_scene1`) asking the user to approve that spend — and **stop your turn there**. Do not run `approve-generation` in this same turn.

**Note on the real `fal` provider:** if `providers.image.provider` is `fal` (not `mock`), be aware `edit-candidate` in Step 5 falls back to a fresh regeneration rather than a true image edit, and `characters[].reference` conditioning doesn't yet work against the real API — both are known engine limitations (see the project README), not something you can fix here. Mention this to the user only if it seems relevant (e.g. they expect edits to preserve the base composition exactly, or expect the character's locked reference image to visibly influence the shot).

Only once you've been resumed with a matching `HUMAN_RESPONSE`:

- If `approved: true`, approve every shot's full candidate-loop target string in the batch — not the bare shot id, the `shot:<id>:image` form, since that's the exact string `generate-candidates`/`edit-candidate` check in Step 5:

```bash
ai-film approve-generation --scope storyboard --targets shot:<id1>:image,shot:<id2>:image,...
```

then continue to Step 5 for this batch.
- If `approved: false`, read the `message` (if any), revise the batch or count, and emit a *new* `NEEDS_INPUT` (`type: cost_approval`, a fresh `id`) for the revised estimate — never reuse an old `id` or treat the decline as consent for a different proposal.

**Note on approval scope:** `approve-generation --scope storyboard` replaces any prior storyboard-scope approval wholesale, not additively. If you batch scene 1's shots, get approval, then later batch scene 2's shots and get a second approval, scene 1's shots are no longer authorized if you needed to re-run `generate-candidates`/`edit-candidate` on them after scene 2's approval landed. In the normal forward-only flow (finish generating/locking each batch before moving to the next) this doesn't come up — it only matters if you circle back to an earlier batch mid-run.

## Step 5: Generate, review, and lock each shot's image

For each shot in the approved batch:

1. Generate candidates — no `--prompt` needed, it's derived automatically from the shot's `action`/`visual`/`camera` fields:

```bash
ai-film generate-candidates --target shot:<id>:image --count <N>
```

If this call fails — with a cost-gate error (Step 4's approval didn't cover this shot id) or any other provider error — do not retry it yourself. Emit a `NEEDS_INPUT` with `type: confirmation` (`id: generation_error_<shot-id>`) showing the exact error text and offering: re-approve (with this shot id included) and retry, adjust the shot's fields and regenerate, or skip this shot for now. Stop your turn. Act only once resumed — if the answer is re-approve, that needs a fresh `cost_approval` round trip (Step 4) before `approve-generation` runs again.

2. Run `ai-film review --target shot:<id>:image` to open the gallery, and **read each candidate PNG directly** (`PROJECT_PATH/04_storyboard/candidates/<id>/candidates/<candidate-id>.png` — note the doubled `candidates/` segment: `target_dir` for a shot target is already `04_storyboard/candidates/<id>`, and candidate generation appends its own `candidates/` subdirectory on top of that, unlike character/env targets which only have one `candidates/` level) with the Read tool.
3. Emit a `NEEDS_INPUT` with `type: clarification` (`id: shot_feedback_<shot-id>_1`) asking what the user thinks. For every edit round in the resumed reply, **view the specific candidate with the Read tool first**, then:

```bash
ai-film edit-candidate --target shot:<id>:image --id <candidate-id> --instruction "<instruction>"
```

If this call fails — with a cost-gate error or any other provider error — handle it exactly like the `generate-candidates` failure above: a `type: confirmation` `NEEDS_INPUT` showing the exact error and the retry/adjust/skip choices, never retried silently.

Re-review and view the result the same way, then emit another `NEEDS_INPUT` (`type: clarification` or `type: selection` once there's a concrete shortlist) before either another edit round or locking in.

4. Once a `HUMAN_RESPONSE` picks a final candidate (`type: selection`), lock it:

```bash
ai-film select-candidate --target shot:<id>:image --id <candidate-id>
```

This writes the image into that shot's `generation.image.artifact` and marks it completed — the same effect `generate-image` would have, so nothing downstream needs to know it came from the candidate loop. `select-candidate` is re-runnable with a different `--id` if the user changes their mind later — just another `type: selection` round trip.

## When you're done

After every shot in scope has a locked image, run `ai-film status` and `ai-film validate` and show the output. This is a genuine completion, not a `NEEDS_INPUT` — report: how many shots were locked this run, any shots left unresolved (and why — waiting on continuity fixes, a skipped provider error, etc.), and remind the user that `generate-video`/`generate-voice`/`generate-sfx`/`generate-music`/`render` are manual next steps this agent does not perform.
````

- [ ] **Step 2: Verify the frontmatter parses, the referenced CLI commands are real, and the shot.json template is schema-valid**

No YAML library is installed anywhere in this project — use plain string containment on the frontmatter block instead of `import yaml`:

```bash
python3 -c "
text = open('.claude/agents/ai-film-storyboard.md').read()
front = text.split('---')[1]
assert 'name: ai-film-storyboard' in front, front
assert 'tools: [\"Read\", \"Write\", \"Bash\", \"Glob\"]' in front, front
print('frontmatter OK')
"
grep -o 'ai-film [a-z-]*' .claude/agents/ai-film-storyboard.md | sort -u
```

Expected: `frontmatter OK` with no traceback; the `ai-film <word>` list is exactly `ai-film approve-generation`, `ai-film check-continuity`, `ai-film edit-candidate`, `ai-film generate-candidates`, `ai-film review`, `ai-film select-candidate`, `ai-film status`, `ai-film validate` — all real subcommands.

- [ ] **Step 2b: Verify the human-in-the-loop protocol is structurally present, and the cost-approval gate is genuinely separated from the estimate**

```bash
python3 -c "
text = open('.claude/agents/ai-film-storyboard.md').read()
assert 'NEEDS_INPUT:' in text
assert 'HUMAN_RESPONSE:' in text
assert 'type: clarification | selection | cost_approval | confirmation' in text

gate_marker = text.index('stop your turn there')
approve_call = text.index('ai-film approve-generation --scope storyboard')
assert approve_call > gate_marker, 'approve-generation appears before the turn boundary — cost gate is not structurally separated'
print('protocol structure OK, cost gate structurally separated (marker at', gate_marker, ', approve-generation at', approve_call, ')')
"
```

Expected: `protocol structure OK, cost gate structurally separated (...)` with no traceback.

Note: the next check's Python source contains literal backticks, so it must run from a single-quoted heredoc (not `python3 -c "..."`) — inside a double-quoted shell string, bash/zsh treats backticks as command substitution and silently corrupts the pattern.

```bash
cd /Users/nathan/Projects/ai-film-studio
.venv/bin/python3 << 'PYEOF'
import json, re, sys
sys.path.insert(0, 'src')
from ai_film.schema import validate_shot

text = open('.claude/agents/ai-film-storyboard.md').read()
m = re.search(r'```json\n(\{.*?\n\})\n```', text, re.DOTALL)
assert m, 'no json template block found'
shot = json.loads(m.group(1))
errors = validate_shot(shot)
assert errors == [], errors
print('embedded shot.json template is schema-valid')
PYEOF
```

Expected: `embedded shot.json template is schema-valid` with no assertion error — this extracts the literal JSON block from the agent file and runs it through the real, tested schema validator, proving the template a future agent copies field-for-field is actually schema-compliant.

- [ ] **Step 3: Behaviorally verify the full shot pipeline the file instructs, against a scratch project with the mock provider**

This builds on a locked character (same shape Task 3 produces) since the shot template's `characters[].reference` needs a real file to point at.

```bash
rm -rf /tmp/afs-storyboard-check && mkdir -p /tmp/afs-storyboard-check
cd /Users/nathan/Projects/ai-film-studio
.venv/bin/ai-film init "Storyboard Check" --path /tmp/afs-storyboard-check
python3 -c "
import json
cfg_path = '/tmp/afs-storyboard-check/config.json'
cfg = json.load(open(cfg_path))
cfg['providers']['image']['provider'] = 'mock'
json.dump(cfg, open(cfg_path, 'w'), indent=2)
"
.venv/bin/ai-film approve-generation --scope bibles --targets character:girl --path /tmp/afs-storyboard-check
.venv/bin/ai-film generate-candidates --target character:girl --count 1 --prompt "a girl, cinematic sci-fi style" --path /tmp/afs-storyboard-check
.venv/bin/ai-film select-candidate --target character:girl --id 001 --path /tmp/afs-storyboard-check

cat > /tmp/afs-storyboard-check/03_shots/S01_SH01.json << 'EOF'
{
  "schema_version": "1.0", "id": "S01_SH01", "status": "draft", "duration_seconds": 4,
  "continuity": {"status": "pending", "checked_at": null, "issues": []},
  "action": "a lone engineer walks through a dim corridor",
  "visual": {"style": "cinematic sci-fi", "lighting": "red emergency light"},
  "camera": {"shot": "wide", "movement": "slow_push_in"},
  "dialogue": {"text": "", "speaker": ""},
  "characters": [{"name": "girl", "reference": "assets/characters/girl/reference.png"}],
  "generation": {
    "image": {"status": "pending", "attempts": 0}, "video": {"status": "pending", "attempts": 0},
    "voice": {"status": "not_required"}, "sfx": {"status": "not_required"}, "music": {"status": "not_required"}
  }
}
EOF
.venv/bin/ai-film validate --path /tmp/afs-storyboard-check
.venv/bin/ai-film check-continuity --shot S01_SH01 --status passed --path /tmp/afs-storyboard-check

# Prove the shot-target cost-gate error text matches what Step 5 tells the agent to recognize
.venv/bin/ai-film generate-candidates --target shot:S01_SH01:image --count 2 --path /tmp/afs-storyboard-check 2>&1 | grep "is not approved for generation" && echo "COST GATE TEXT MATCHES"

.venv/bin/ai-film approve-generation --scope storyboard --targets shot:S01_SH01:image --path /tmp/afs-storyboard-check
.venv/bin/ai-film generate-candidates --target shot:S01_SH01:image --count 2 --path /tmp/afs-storyboard-check
.venv/bin/ai-film select-candidate --target shot:S01_SH01:image --id 001 --path /tmp/afs-storyboard-check
.venv/bin/ai-film status --path /tmp/afs-storyboard-check
python3 -c "
import json
shot = json.load(open('/tmp/afs-storyboard-check/03_shots/S01_SH01.json'))
assert shot['generation']['image']['status'] == 'completed', shot['generation']['image']
assert shot['generation']['image']['artifact'] is not None
print('shot image locked:', shot['generation']['image']['artifact'])
"
```

Expected: `ai-film validate` reports `S01_SH01.json: valid`; `COST GATE TEXT MATCHES` prints; after `select-candidate`, `ai-film status` reports `S01_SH01  ready` — not `completed`: `compute_status` (`src/ai_film/shot_store.py`) only returns `completed` once every one of the five `generation` stages is `completed`/`not_required`, and `video` stays `pending` here since these agents never call `generate-video` (spec Non-Goals) — `ready` (continuity passed, not all stages done, none failed) is the correct, healthy state for an image-only run. The final Python check prints `shot image locked: {...}` with a non-null `artifact` — proving the no-`--prompt`, `shot:<id>:image` target syntax and every command in Steps 3-5 work exactly as the file documents them, including the derived-prompt path (`--prompt` omitted).

**Also verify the approval target-string requirement** — this is easy to get wrong and silently breaks the whole batch: `is_approved` (`src/ai_film/approval.py`) matches the *exact* string later passed as `--target` to `generate-candidates`/`edit-candidate`. For shots that means `approve-generation --targets` must list `shot:<id>:image` (comma-separated for a multi-shot batch), never the bare shot id `<id>` — confirm this by re-running the sequence above with `--targets S01_SH01` (bare id) instead of `--targets shot:S01_SH01:image` and observing `generate-candidates` fail with the cost-gate error even though `approve-generation` reported success.

- [ ] **Step 4: Commit**

```bash
git add .claude/agents/ai-film-storyboard.md
git commit -m "feat: add ai-film-storyboard agent"
```

---

### Task 5: `/create-film` command

**Files:**
- Create: `.claude/commands/create-film.md`

**Interfaces:**
- Consumes: the `ai-film-director`, `ai-film-character`, `ai-film-storyboard` agent names (Tasks 2-4) and the Director's final-report line format `CHARACTERS: <name1>, <name2>, ...` (Task 2).
- Produces: the documented single entry point (`/create-film "Title" [path]`) that later documentation (README) and Task 6's walkthrough reference.

- [ ] **Step 1: Write the command file**

Create `.claude/commands/create-film.md` with this exact content:

````markdown
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
3. Once you have their real answer, **resume the SAME subagent dispatch you already have running** (the same instance/session, never a fresh dispatch — losing that instance loses everything it already worked out) with a message containing:

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
````

- [ ] **Step 2: Verify the frontmatter parses and every dispatched agent name matches a file created in Tasks 2-4**

No YAML library is installed anywhere in this project — use plain string containment on the frontmatter block instead of `import yaml`:

```bash
python3 -c "
import os
text = open('.claude/commands/create-film.md').read()
front = text.split('---')[1]
assert 'description:' in front, 'missing description'
assert 'argument-hint:' in front, 'missing argument-hint'
print('frontmatter OK')

for name in ('ai-film-director', 'ai-film-character', 'ai-film-storyboard'):
    assert name in text, f'{name} not referenced in create-film.md'
    assert os.path.exists(f'.claude/agents/{name}.md'), f'{name}.md does not exist'
print('all three dispatched agent names exist as real agent files')
"
```

Expected: `frontmatter OK` then `all three dispatched agent names exist as real agent files`, no assertion error.

- [ ] **Step 2b: Verify the orchestrator-side protocol is structurally present**

```bash
python3 -c "
text = open('.claude/commands/create-film.md').read()
assert 'NEEDS_INPUT:' in text
assert 'HUMAN_RESPONSE:' in text
assert 'resume the SAME subagent dispatch you already have running' in text, 'must forbid fresh-dispatch on resume'
assert 'never resume with \`approved: true\` unless the user actually said so' in text, 'must forbid assuming cost approval'
print('orchestrator protocol structure OK')
"
```

Expected: `orchestrator protocol structure OK` with no traceback.

- [ ] **Step 3: Behaviorally verify `ai-film init` re-entry logic and the Step 0 preflight check (the two things this command does directly, outside the protocol)**

```bash
rm -rf /tmp/afs-createfilm-check && mkdir -p /tmp/afs-createfilm-check
cd /Users/nathan/Projects/ai-film-studio
which ai-film > /dev/null 2>&1 && echo "unexpected: bare ai-film resolves in this shell (fine either way, just confirming for the record)" || echo "OK: bare ai-film not on PATH here — Step 0's preflight check exists precisely for this"
.venv/bin/ai-film version > /dev/null && echo "OK: .venv/bin/ai-film resolves and runs, confirming the Step 0 check (ai-film version) is a real, working command once the venv is active"
test -f /tmp/afs-createfilm-check/config.json && echo "unexpected: config.json exists before init" || echo "OK: no config.json yet, would run init"
.venv/bin/ai-film init "Create Film Check" --path /tmp/afs-createfilm-check
test -f /tmp/afs-createfilm-check/config.json && echo "OK: config.json exists after init, resume path would skip init"
# Re-run init to confirm it's safe/idempotent if ever invoked twice (defense for the resume branch's own claim)
.venv/bin/ai-film init "Create Film Check" --path /tmp/afs-createfilm-check
echo "OK: re-running init did not error"
```

Expected: the `.venv/bin/ai-film version` line prints `OK: ...` confirming Step 0's `ai-film version` check is a real command; `OK: no config.json yet, would run init`, `OK: config.json exists after init, resume path would skip init`, `OK: re-running init did not error` — confirming both branches of Step 1's re-entry check are accurate. The first `which ai-film` line's outcome depends on your shell's current PATH and either message is fine — it's there to make the point that Step 0 catches exactly this ambiguity for the actual dispatched agent's shell, not to assert a specific PATH state.

- [ ] **Step 4: Commit**

```bash
git add .claude/commands/create-film.md
git commit -m "feat: add /create-film orchestrator command"
```

---

### Task 6: End-to-end golden-path verification

**Files:**
- None created or modified — this task is pure verification, exercising every CLI command referenced across all five files from Tasks 1-5 in one continuous run, per the spec's own Testing Strategy (§8).

**Interfaces:**
- Consumes: every file path and CLI convention declared in Tasks 1-5.
- Produces: no artifacts kept — the scratch project is deleted at the end of this task.

- [ ] **Step 1: Walk a full film through the documented pipeline by hand**

This simulates exactly what `/create-film` + the three agents would do in a live conversation, substituting hand-written content for what a live Claude Code conversation would have produced at each step, using the `mock` provider throughout (no `FAL_KEY` needed, no real spend).

```bash
rm -rf /tmp/afs-golden-path && mkdir -p /tmp/afs-golden-path
cd /Users/nathan/Projects/ai-film-studio

# --- /ai-film-setup + /create-film Step 1: scaffold ---
.venv/bin/ai-film init "The Last Ship" --path /tmp/afs-golden-path
python3 -c "
import json
p = '/tmp/afs-golden-path/config.json'
cfg = json.load(open(p))
cfg['providers']['image']['provider'] = 'mock'
json.dump(cfg, open(p, 'w'), indent=2)
"

# --- Director agent output (00_story/, 02_scenes/) ---
mkdir -p /tmp/afs-golden-path/00_story /tmp/afs-golden-path/02_scenes
cat > /tmp/afs-golden-path/00_story/story.md << 'EOF'
# The Last Ship

**Logline:** A lone engineer discovers the ship she thought was empty still has one passenger.

## Story

Aboard a derelict generation ship, engineer Mara works alone through the
red-lit corridors, keeping failing systems alive. She believes she is the
last living person aboard — until she finds a sealed door with a working
life-support reading behind it.

## Characters

- **Mara** — the last engineer, resourceful and guarded
EOF
cat > /tmp/afs-golden-path/02_scenes/SC01.md << 'EOF'
# Scene 1: The Corridor

**Characters:** Mara

**Action:** Mara walks through a dim, humming corridor, red emergency
light pulsing. She stops at a sealed door, hesitates, then presses her
palm to the reader.

**Dialogue:**
Mara: "Please still be locked."
EOF

# --- Character agent output, for the one unique name "Mara" ---
mkdir -p /tmp/afs-golden-path/01_bibles/characters
cat > /tmp/afs-golden-path/01_bibles/characters/Mara.md << 'EOF'
# Mara

## Personality

Resourceful, guarded, quietly exhausted from years alone.

## Appearance

Lean build, close-cropped dark hair, wears a patched grey engineer's
jumpsuit with a tool belt. Streak of grease along one cheek.

## Role in the story

The last known engineer aboard the ship; the story's point-of-view character.
EOF
.venv/bin/ai-film approve-generation --scope bibles --targets character:Mara --path /tmp/afs-golden-path
.venv/bin/ai-film generate-candidates --target character:Mara --count 4 \
  --prompt "a woman engineer, cinematic sci-fi style, grey patched jumpsuit, grease streak on cheek" \
  --path /tmp/afs-golden-path
.venv/bin/ai-film review --target character:Mara --path /tmp/afs-golden-path
.venv/bin/ai-film edit-candidate --target character:Mara --id 002 \
  --instruction "shorter hair" --path /tmp/afs-golden-path
.venv/bin/ai-film select-candidate --target character:Mara --id 005 --path /tmp/afs-golden-path
test -f /tmp/afs-golden-path/assets/characters/Mara/reference.png && echo "CHARACTER LOCKED"

# --- Storyboard agent output, for Scene 1's single shot ---
cat > /tmp/afs-golden-path/03_shots/S01_SH01.json << 'EOF'
{
  "schema_version": "1.0", "id": "S01_SH01", "status": "draft", "duration_seconds": 5,
  "continuity": {"status": "pending", "checked_at": null, "issues": []},
  "action": "Mara walks through a dim corridor and presses her palm to a sealed door's reader",
  "visual": {"style": "cinematic sci-fi", "lighting": "red emergency light, pulsing"},
  "camera": {"shot": "wide", "movement": "slow_push_in"},
  "dialogue": {"text": "Please still be locked.", "speaker": "Mara"},
  "characters": [{"name": "Mara", "reference": "assets/characters/Mara/reference.png"}],
  "generation": {
    "image": {"status": "pending", "attempts": 0}, "video": {"status": "pending", "attempts": 0},
    "voice": {"status": "not_required"}, "sfx": {"status": "not_required"}, "music": {"status": "not_required"}
  }
}
EOF
.venv/bin/ai-film validate --path /tmp/afs-golden-path
.venv/bin/ai-film check-continuity --shot S01_SH01 --status passed --path /tmp/afs-golden-path
.venv/bin/ai-film approve-generation --scope storyboard --targets shot:S01_SH01:image --path /tmp/afs-golden-path
.venv/bin/ai-film generate-candidates --target shot:S01_SH01:image --count 4 --path /tmp/afs-golden-path
.venv/bin/ai-film review --target shot:S01_SH01:image --path /tmp/afs-golden-path
.venv/bin/ai-film edit-candidate --target shot:S01_SH01:image --id 002 \
  --instruction "warmer red tone" --path /tmp/afs-golden-path
.venv/bin/ai-film select-candidate --target shot:S01_SH01:image --id 005 --path /tmp/afs-golden-path

# --- Final acceptance bar, per spec §8 ---
.venv/bin/ai-film status --path /tmp/afs-golden-path
.venv/bin/ai-film validate --path /tmp/afs-golden-path
```

Expected, at each stage:
- `ai-film init` and the mock-provider config edit succeed with no error.
- `generate-candidates --target character:Mara --count 4` reports 4 added candidates; `edit-candidate` adds a 5th (`edit of 002`); `select-candidate --id 005` succeeds; `CHARACTER LOCKED` prints.
- `ai-film validate` reports `S01_SH01.json: valid` (proving the hand-written shot matches the exact template from Task 4's agent file against the real schema).
- `check-continuity` reports `S01_SH01: continuity passed`.
- The shot's `approve-generation --targets shot:S01_SH01:image` → `generate-candidates --target shot:S01_SH01:image` → `edit-candidate` → `select-candidate` sequence completes with no cost-gate error — note the `--targets` value must be the full `shot:S01_SH01:image` string, not the bare id `S01_SH01`; `is_approved` (`src/ai_film/approval.py`) matches the exact target string, so a bare id silently fails to authorize the shot target and every subsequent `generate-candidates`/`edit-candidate` call errors with the same cost-gate message even though `approve-generation` itself reported success.
- Final `ai-film status` reports `S01_SH01  ready`, not `completed` — `compute_status` (`src/ai_film/shot_store.py`) only returns `completed` once all five `generation` stages are `completed`/`not_required`, and `video` stays `pending` here since this pipeline never calls `generate-video` (spec Non-Goals). `ready` (continuity passed, image done, video/voice/sfx/music not yet run, nothing failed) is the correct, healthy end state for this slice — this is the acceptance bar spec §8 means by "a healthy project," not literally `completed`.
- Final `ai-film validate` reports `S01_SH01.json: valid` with exit code 0.

If any command in this sequence fails or produces output inconsistent with what Tasks 1-5's files claim, that is a defect in the corresponding task's file — fix the file's instructions (not this verification script) and re-run from the top before proceeding.

- [ ] **Step 2: Clean up the scratch project**

```bash
rm -rf /tmp/afs-golden-path /tmp/afs-setup-check /tmp/afs-director-check /tmp/afs-character-check /tmp/afs-storyboard-check /tmp/afs-createfilm-check
```

- [ ] **Step 3: Update the README's Roadmap section to reflect this layer is now implemented**

Read `README.md`. Replace the Roadmap section (currently describing this layer as "written but not yet implemented") with:

```markdown
## Roadmap

The Claude Code Agent layer is implemented: `/ai-film-setup` configures providers,
`/create-film "Title"` scaffolds a project and walks the whole story -> character ->
shot -> reviewed-storyboard-image pipeline through conversation, dispatching the
`ai-film-director`, `ai-film-character`, and `ai-film-storyboard` subagents in turn.
See `docs/superpowers/specs/2026-08-23-agent-layer-design.md` and
`docs/superpowers/plans/2026-08-23-agent-layer.md` for the design and implementation
history.

Per that spec's Future Extensions: a dedicated Continuity agent if shot volume ever
justifies a second independent-context pass, video/audio candidate review once the
engine gains video candidate storage and an edit/regenerate loop for clips, voice/
sfx/music generation agents and an Editor agent for final assembly, and a
programmatic cost-estimation engine in `ai_film` replacing the static per-model
prompt-knowledge table the agents use today.
```

Also update the second paragraph near the top of the README ("There is currently no agent layer...") to drop the "future Skill/Agents layer will do this" framing, since it now exists:

```markdown
`/create-film` (a Claude Code slash command) conducts story, character, and shot
creation through conversation and writes `shot.json` for you — see Roadmap below.
This CLI is the production engine underneath that layer, and remains fully usable
directly for anyone who prefers hand-authoring shots.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: mark agent layer as implemented in README roadmap"
```

---

## Self-Review Notes

- **Spec coverage:** §2 Architecture → Tasks 2-5 (three agents + orchestrator, sequential handoff). §3 Data Flow → Task 2's scene format, Task 3's bible/reference paths, Task 4's shot.json field population, all cross-checked against `src/ai_film/schema.py` and `prompts.py` directly. §4 Agent Responsibilities → each agent's Steps map 1:1 to its paragraph (re-entry, brainstorm-then-checkpoint, cost-gate-then-loop, image-viewing-before-edit). §5 Cost Estimation → static tables in Tasks 3 and 4, sourced from the real two-model image catalog (`fal/nano-banana`, `fal/nano-banana-pro`), explicitly labeled advisory/approximate. §6 Entry-Point Commands → Tasks 1 and 5. §7 Error Handling → cost-gate/provider-error handling written into Tasks 3 and 4's agent files verbatim, with Task 3/4's verification steps proving the exact error text. §8 Testing Strategy → this plan's entire per-task verification approach plus Task 6.
- **Non-Goals respected:** no video-candidate review, no dedicated Continuity agent, no `ai_film` schema/engine changes, no programmatic cost engine — confirmed absent from every task.
- **Type/interface consistency:** shot ID convention (`S<SS>_SH<NN>`), scene file convention (`SC<NN>.md`), and candidate target syntax (`character:<name>`, `shot:<id>:image`) are used identically across Tasks 2, 3, 4, 5, and 6 — cross-checked against the real CLI source (`cli.py`, `candidate_store.py`) rather than assumed from the spec's own (slightly looser) illustrative examples.
- **Every command in every task was actually executed** against the real CLI with the mock provider (not just read for plausibility) before this plan was considered done, catching two defects the spec's own illustrative examples didn't surface: (1) `approve-generation --targets` for a storyboard-scope shot must be the full `shot:<id>:image` string — `is_approved` matches it exactly, so the README's own bare-id example (`--targets S01_SH01`) silently fails to authorize the candidate-loop target and only happens to "work" in the README because that example precedes `generate-image`, which never checks approval at all; (2) a shot with a locked image but no video ends at `ai-film status` `ready`, never `completed` — `compute_status` requires all five generation stages done, and this pipeline deliberately never runs `generate-video`. The Storyboard agent file (Task 4) and the golden-path script (Task 6) were corrected to the full target-string form and the `ready` expectation before this plan was finalized; the Character agent (Task 3) was already correct since `character:<name>` needs no such expansion.
