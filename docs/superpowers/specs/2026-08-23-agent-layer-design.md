# Agent Layer — Design

**Status:** Approved for implementation (first slice, amended for the human-in-the-loop protocol below)
**Date:** 2026-08-23

## 1. Overview

The `ai_film` CLI/engine (core engine + human-interaction-model candidate loop) is
fully built, tested, and merged — but it has zero reasoning capability. Every
command executes exactly what you already decided: `generate-image` needs a
hand-written `shot.json`; `generate-candidates` needs a `--prompt` you typed.
Nothing writes a story, invents a character, breaks a scene into shots, or looks
at a generated image and decides how to improve it.

This spec designs the layer that closes that gap: a set of Claude Code
Skills/Agents/Commands that let you `cd` into a project and run `claude`, then
conduct the whole story → character → shot → reviewed-storyboard-image workflow
through conversation — the way career-ops-style tools already work — instead of
typing raw `ai-film` commands yourself. This is a pure agent/prompt layer: no
changes to the `ai_film` Python package are needed anywhere in this spec.

### Goals (first slice)

- A `/create-film "Title"` entry point that scaffolds a project and hands off to
  a Director/Story agent for a brainstorming conversation (genre, characters,
  style reference) that converges on a story and a script organized into scenes.
- A Character agent that, per unique character, runs the already-built candidate
  loop (`generate-candidates` → `review` → `edit-candidate` → `select-candidate`)
  to lock in a reference image — after an explicit, cost-estimated approval.
- A Storyboard/Shot Director agent that breaks each scene into shots (camera
  choices), writes `shot.json` for each, runs continuity checking, and then runs
  the same candidate loop per shot to generate, review, and lock in a storyboard
  image — using the "agent can actually view generated images" property of
  Claude Code (the Read tool displays image content, not just file metadata) to
  make real visual critiques, not blind guesses.
- A `/ai-film-setup` command that walks through provider/model selection and
  `FAL_KEY` configuration, closing a gap left open since the original core-engine
  spec.

### Non-Goals (first slice)

- **Video generation is not reviewed or refined by any agent.** The Storyboard
  agent's loop stops at a locked storyboard *image* per shot. Video generation
  (`generate-video`) happens afterward as a manual/unreviewed step — the engine
  has no video candidate storage or edit/regenerate loop to build an agent on top
  of yet (explicitly deferred in the human-interaction-model spec's Future
  Extensions).
- **No dedicated Continuity agent.** Continuity checking is folded into the
  Storyboard/Shot Director agent (see §5) rather than split into its own agent —
  worth reconsidering if shot volume ever grows large enough that an independent
  second pass adds real value.
- **No new `ai_film` engine/schema changes.** Scenes and character bibles are
  plain markdown (§4) specifically to avoid needing new Python code — this spec
  is agents and prompts only. This also bounds the human-in-the-loop protocol
  (§3): it is a structural/prompt-level guarantee, not a code-level one — see §3
  for the explicit tradeoff this implies and why it was chosen deliberately for
  this first slice.
- **No automatic cost-estimation engine.** Agents carry a rough per-model cost
  table as prompt knowledge (§6) to show before requesting approval; there is no
  programmatic cost calculator anywhere in `ai_film`.
- Voice/sfx/music generation, editing/assembly, and the final `render` step are
  untouched by this spec — they remain manual CLI operations, same as today.

## 2. Architecture

```
                          /create-film "Title"
                                   │
                          ai-film init "Title"
                                   │
                                   ▼
                     ┌─────────────────────────┐
                     │  Director/Story agent    │  (brainstorming conversation)
                     └────────────┬─────────────┘
                                   │ writes 00_story/, 02_scenes/
                                   ▼
                     ┌─────────────────────────┐
                     │    Character agent        │  × one per unique character
                     │  (candidate loop: bibles   │
                     │   scope cost gate)         │
                     └────────────┬─────────────┘
                                   │ writes 01_bibles/, assets/characters/*/reference.png
                                   ▼
                     ┌─────────────────────────┐
                     │ Storyboard/Shot Director   │
                     │  agent (continuity check +  │
                     │  candidate loop: storyboard │
                     │  scope cost gate)            │
                     └────────────┬─────────────┘
                                   │ writes 03_shots/SH*.json,
                                   │        shot.json's generation.image.artifact
                                   ▼
                       (manual from here: generate-video,
                        generate-voice/sfx/music, render)
```

Each box is a real Claude Code subagent (`.claude/agents/*.md`) — its own model
choice, its own narrow prompt, its own tool access — not one large skill
switching between modes. Handoff is sequential: each agent's output is the next
agent's input. `/ai-film-setup` sits outside this pipeline entirely — a
prerequisite you run once (or whenever you want to change providers), not part
of the per-film flow.

**Core principle carried over from the engine specs:** every agent that triggers
real spend calls the *existing*, unmodified cost gate (`ai-film approve-generation
--scope <bibles|storyboard> --targets <ids>`) before any `generate-*`/`edit-candidate`
call — the same enforcement the engine already guarantees for a human typing
commands directly. Agents don't get a shortcut around it. §3 defines the
mechanism by which "approval" reaches an agent at all, given that a dispatched
subagent cannot simply chat with you the way this top-level session can.

## 3. Human-in-the-Loop Protocol

**The problem this section closes.** A Claude Code subagent dispatched via the
Agent/Task mechanism has no live channel back to you — it runs to completion and
returns exactly one final report. It cannot pause mid-task, ask a question, and
wait for your live reply the way this top-level session (the one you're reading
right now) can. Every point in §5 where an agent "asks," "discusses," or
"brainstorms" with you therefore cannot be a literal, uninterrupted conversation
inside that subagent — it has to be built out of discrete, resumable round trips
through the orchestrator (`/create-film`, or `/ai-film-setup`, running in the
top-level session, which *does* have a live channel to you). This section
defines that round trip as a formal, machine-readable protocol so every agent
and orchestrator in this spec implements it identically.

### 3.1 The round trip

```
Subagent (mid-task)
  │  reaches a point where it needs a real answer from you
  ▼
Emits NEEDS_INPUT as the last line of its turn, then STOPS —
it does not guess, does not proceed, does not treat silence as consent.
  │
  ▼
Orchestrator (top-level session — has a live channel to you)
  │  parses the NEEDS_INPUT block from the subagent's report
  │  asks you the question for real, in this conversation
  │  gets your actual answer
  ▼
Orchestrator resumes the SAME subagent instance (same dispatch,
full prior context intact — never a fresh dispatch) with a
HUMAN_RESPONSE block carrying your answer
  │
  ▼
Subagent picks up exactly where it stopped, now with your answer
in context, and only now may it proceed to whatever step depended
on it (see §3.4 for the cost-approval case specifically)
```

### 3.2 `NEEDS_INPUT` — the subagent's request

The **last line** of a subagent's turn must be, verbatim, when it needs input:

```
NEEDS_INPUT:
id: <unique-id, unique within this agent's dispatch — e.g. "cost_approval_001", "scene_approval", "candidate_pick_003">
type: clarification | selection | cost_approval | confirmation
question: <the human-readable question to show the user>
```

No other phrasing satisfies this contract. An agent that writes prose like "I
need more information before continuing" instead of this exact block has
committed a protocol violation, not asked a question — the orchestrator has
nothing to parse, and per §3.5 must treat this as an error, not as a signal to
extract intent from free text.

The four `type` values, and where each is used in §5:

- **`clarification`** — an open-ended question with no fixed set of answers
  (the Director's brainstorming questions; the Character agent asking about
  appearance details the scenes didn't pin down).
- **`selection`** — picking among a concrete, enumerable set of options (which
  candidate image to lock in; which edit round to apply next).
- **`cost_approval`** — specifically approving or declining a spend before a
  cost-gated call (see §3.4 — this type carries extra obligations beyond the
  other three).
- **`confirmation`** — a yes/no or approve/revise gate that isn't picking among
  multiple concrete options and isn't a spend decision (the Director's scene-
  breakdown approval; the Storyboard agent asking whether to proceed past a
  continuity `warning`). This fourth type is not in the original three-type
  sketch this section was built from — it is added here because `selection`
  doesn't fit a plain yes/no gate and `clarification` doesn't fit a bounded
  decision either. If a narrower reading of the original three types is
  preferred, `confirmation` points collapse into `selection` (treat "approve" /
  "revise" as the two selectable options) — flagged here as the one place this
  section extends rather than transcribes the agreed design.

### 3.3 `HUMAN_RESPONSE` — the orchestrator's relay

When the orchestrator resumes the subagent, its message to that subagent must
contain, verbatim:

```
HUMAN_RESPONSE:
id: <the exact id from the NEEDS_INPUT this answers>
answer: <your actual answer, verbatim or lightly summarized — required for clarification/selection/confirmation>
```

For `type: cost_approval` specifically, the relay carries a structured decision
instead of freeform `answer`:

```
HUMAN_RESPONSE:
id: <the exact id from the NEEDS_INPUT this answers>
approved: true | false
message: <optional — only when approved is false, carries your reason/redirect, e.g. "try something cheaper">
```

### 3.4 Cost approval is a hard structural gate, not a polite request

This is the one place in the protocol with an extra, non-negotiable rule,
because it guards real spend:

- **The Bash instruction to call `ai-film approve-generation` (or any
  `generate-candidates`/`edit-candidate` call gated by it) never appears in the
  same subagent turn that proposes the cost estimate.** It only exists in the
  turn the orchestrator produces *after* relaying a genuine `HUMAN_RESPONSE`
  with `type: cost_approval` and a matching `id`. Structurally, the agent's
  context does not contain "you may now call `approve-generation`" until that
  relay has happened — this is enforced by the conversational turn boundary,
  not by asking the model nicely to wait.
- **This is a structural guarantee, not a code-level one — see the Non-Goals
  tradeoff this implies.** Given this spec's Non-Goal of zero `ai_film` engine
  changes, there is no programmatic lock preventing a subagent with Bash access
  from calling `approve-generation` early if it deviates from its instructions.
  The mitigation is architectural (the instruction to do so is never present
  until after the real round trip) plus explicit, repeated prohibition in every
  agent's own prompt (§5) — not a cryptographic or code-enforced block. A true
  code-level guarantee (e.g. `approve-generation` requiring proof of a completed
  human round trip) is deliberately deferred — see §10 Future Extensions. This
  first slice's engine already has a real cost gate (`approve-generation` before
  any spend); what was missing and what this section adds is the mechanism by
  which a dispatched subagent correctly reaches that gate with real consent
  behind it, not a claim that the engine itself was under-guarded.
- **A missing, malformed, or `id`-mismatched `HUMAN_RESPONSE` is a protocol
  error** (§3.5) — it does not count as approval, and does not count as denial
  either; the agent stops and reports the error rather than picking either
  interpretation.
- **Silence, an ambiguous answer, or approval recorded for a *different* prior
  request never counts as approval for the current one.** Each `cost_approval`
  needs its own `HUMAN_RESPONSE` with its own matching `id`.

### 3.5 Protocol errors

Any of the following is a protocol error, and must stop the pipeline with a
clear report rather than silently continuing or guessing:

- A subagent's final report does not end with a `NEEDS_INPUT` block, but the
  orchestrator's instructions expected one at this point (e.g. after a cost
  estimate is presented with no `approve-generation` call and no `NEEDS_INPUT`
  — the agent stalled or went off-script).
- A `HUMAN_RESPONSE` is relayed with an `id` that does not match the pending
  `NEEDS_INPUT`'s `id`.
- A `HUMAN_RESPONSE` is missing required fields for its type (no `answer` for
  `clarification`/`selection`/`confirmation`; no `approved` for `cost_approval`).
- An agent's report shows it proceeded past a point that required a
  `HUMAN_RESPONSE` without one having been relayed in that resume.

### 3.6 Worked example

```
[Character agent, dispatched for "Mara", mid-turn]
...decided on 4 candidates at nano-banana ≈ $0.08 total...

NEEDS_INPUT:
id: cost_approval_mara_bibles
type: cost_approval
question: Approve ~$0.08 for 4 candidate images of Mara?

--- orchestrator relays this to you in the live conversation, gets your answer ---

[Orchestrator resumes the SAME Character-agent dispatch]
HUMAN_RESPONSE:
id: cost_approval_mara_bibles
approved: true

[Character agent, same instance, continues — only now does its next
step contain the approve-generation call]
ai-film approve-generation --scope bibles --targets character:Mara
...
```

If you had instead said "no, try something cheaper," the relay would carry
`approved: false` and `message: "try something cheaper"`, and the agent's next
step is to revise the plan (e.g. propose `mock` or fewer candidates) and emit a
*new* `NEEDS_INPUT` with a new `id` — never re-interpret the old answer as
consent for a revised proposal.

## 4. Data Flow

```
Director/Story agent
  writes: 00_story/story.md          (logline + full narrative)
          02_scenes/SC01.md, ...     (one file per scene: action + dialogue + character list)
  reads:  nothing — starts from the brainstorming conversation

Character agent (once per unique character named across all scenes)
  reads:  02_scenes/*.md             (how the character is described/used)
  writes: 01_bibles/characters/<name>.md          (personality/appearance bible)
          assets/characters/<name>/reference.png  (via the candidate loop, once locked)

Storyboard/Shot Director agent
  reads:  02_scenes/*.md, 01_bibles/characters/*.md, assets/characters/*/reference.png
  writes: 03_shots/SH*.json                        (the existing shot.json contract)
          04_storyboard/candidates/<shot_id>/...   (via the candidate loop)
          shot.json's generation.image.artifact, once a candidate is selected
```

**Scenes and character bibles are plain markdown, not a new JSON schema.**
`shot.json` has a schema/validator because downstream engine code (`render`,
`preflight`, `status`) programmatically depends on its exact shape. Scenes and
bibles are read only by the agents themselves, as working context for the next
phase — giving them a formal schema would require new Python engine code for no
consumer that needs it. `shot.json` itself needs no schema changes: the
Storyboard agent populates fields (`action`, `dialogue`, `camera`,
`characters[].reference`) that already exist in the contract — it's simply the
first thing that writes them programmatically instead of a human hand-authoring
them.

`02_scenes/SC01.md` format (illustrative, not a strict schema since nothing
parses it programmatically):
```markdown
# Scene 1: The Corridor

**Characters:** girl

**Action:** A lone engineer walks through a dim, humming corridor, red
emergency light pulsing. She stops at a door, hesitates.

**Dialogue:**
girl: "You're finally here."
```

## 5. Agent Responsibilities in Detail

**Director/Story agent.** Runs the brainstorming conversation first (personality,
genre, style reference — converging on a brief). Each question is a
`type: clarification` round trip per §3 — the agent asks one question at a time,
STOPs on `NEEDS_INPUT`, and only continues once the orchestrator relays a
`HUMAN_RESPONSE`, across as many round trips as it takes to converge. Once the
story is agreed, writes `00_story/story.md`. Then proposes a scene breakdown —
just scene titles and one-line summaries — and gets your go-ahead via a
`type: confirmation` round trip *before* writing full action/dialogue for each
scene. This checkpoint stays inside the Director/Story agent rather than
becoming a separate agent: the agent that proposed the scene structure is best
positioned to revise it based on your feedback (a `type: clarification` or
`confirmation` follow-up round trip, same resumed instance), and no other agent
needs to be involved in that back-and-forth. Once approved, writes one
`02_scenes/SC*.md` file per scene.

**Character agent.** Dispatched once per unique character name found across
`02_scenes/*.md`. If the scenes didn't already pin down appearance/personality,
asks via `type: clarification` round trips. Writes `01_bibles/characters/<name>.md`,
computes a rough cost estimate for N candidates (§6), requests approval via a
`type: cost_approval` round trip (§3.4 — the `approve-generation` call itself is
structurally unreachable until that round trip completes), then runs
`generate-candidates` → `review` (opens the gallery) → discusses your feedback on
each candidate via `type: selection`/`clarification` round trips → `edit-candidate`
(zero or more rounds, each its own round trip) → `select-candidate` to lock in
`reference.png`.

**Storyboard/Shot Director agent.** For each scene, decides the shot breakdown
(how many shots, camera type/lens/movement per shot, which portion of the
scene's action/dialogue belongs to each) and writes `03_shots/SH*.json` — a
real, schema-valid shot per the existing contract, with `characters[].reference`
pointing at the locked character reference images. Runs
`ai-film check-continuity --shot <id> --status <passed|warning|failed>` for each
shot (this agent's continuity judgment, since it has full context of every shot
and every character bible — see Non-Goals for why this isn't a separate agent).
A `warning` status triggers a `type: confirmation` round trip (proceed anyway,
or fix first?) before that shot moves on. Once a batch of shots passes
continuity, computes a cost estimate, requests approval via a `type: cost_approval`
round trip (§3.4, same structural gate as the Character agent), then runs the
same generate → review → edit → select loop as the Character agent, per shot —
critically, actually *viewing* each candidate image (via Claude Code's native
image-reading capability) before writing any edit instruction, so refinements
are grounded in what the image actually shows, not guessed from the prompt
alone; candidate feedback and edit instructions are `type: selection`/
`clarification` round trips like the Character agent's.

## 6. Cost Estimation (prompt knowledge, not engine code)

Since `ai_film` has no cost-estimation logic, each agent that requests approval
carries a small, static per-model cost table in its own instructions (not
Python code) — e.g., "nano-banana ≈ $0.02/image, so N candidates ≈ $0.02×N" —
and presents that estimate to you as part of the `type: cost_approval` round
trip defined in §3.4. This number is advisory, agent-side, and never touches
`ai_film`; it exists purely so "approve — this'll cost about $X" is a real
sentence an agent can say before requesting approval.

## 7. Entry-Point Commands

- **`/create-film "Title"`** — runs `ai-film init "Title"`, then dispatches the
  Director/Story agent and owns the orchestrator side of the §3 protocol for
  every subagent it dispatches: detect a `NEEDS_INPUT` block in a subagent's
  report, relay the real question to you, and resume that same subagent
  instance with the matching `HUMAN_RESPONSE` — repeating until the subagent's
  report no longer ends in `NEEDS_INPUT`, which is how the orchestrator knows a
  phase has genuinely finished rather than merely paused. The single entry
  point for starting or resuming a film.
- **`/ai-film-setup`** — a prerequisite command, not part of the per-film agent
  pipeline. Checks `FAL_KEY` is set; walks through `ai-film models --capability
  <x>` for each capability (image/video/voice/sfx/music) and writes the picks
  into `config.json`. Rerunnable anytime to change providers/models. Runs
  entirely in the top-level session (it is a command, not a dispatched
  subagent), so it needs no §3 protocol of its own — it can just ask you
  directly.

## 8. Error Handling and Edge Cases

- **Re-entry.** `/create-film` on a project that already has `00_story/`,
  `02_scenes/`, or `03_shots/` content resumes from whatever exists rather than
  overwriting — each agent checks its own phase's expected files/state before
  starting and picks up mid-story, mid-character, or mid-shot-list as needed.
- **Shared characters.** The Character agent dispatches once per *unique*
  character name across all scenes, never once per scene-appearance.
- **Generation failures.** `CostGateError`/`ProviderError` from any `generate-*`/
  `edit-candidate` call (both already surfaced cleanly by the CLI as a message +
  exit 1, per the core engine's existing error handling) are shown to you
  directly by the agent via a `type: confirmation` (or `selection`, if framed as
  retry/adjust/skip options) §3 round trip — rather than retrying silently or
  stalling — and the agent acts only on your relayed answer.
- **Protocol errors.** See §3.5 — malformed or mismatched `NEEDS_INPUT`/
  `HUMAN_RESPONSE` pairs stop the pipeline with a clear report; they are never
  silently absorbed or guessed past.
- **Changing your mind after locking a candidate.** `select-candidate` is already
  idempotent/re-runnable (§ human-interaction-model spec) — an agent can freely
  re-run it if you want to swap a previously-locked pick, no special handling
  needed beyond what the CLI already guarantees.

## 9. Testing Strategy

This is a prompt/agent-instruction layer, not Python code — there is no `pytest`
suite for it. Verification is behavioral: manually walking a real film through
`/create-film` end to end (brainstorm → scenes → character candidates → shot
candidates → a locked storyboard image per shot) and confirming each agent
produces schema-valid `shot.json` files, correctly enforces both cost-gate
checkpoints (generation genuinely blocked before approval, per the CLI's own
existing tested behavior), that the §3 protocol's structural gate is genuinely
in place (the `approve-generation` instruction is verifiably absent from any
subagent turn prior to a relayed `HUMAN_RESPONSE`), and that `ai-film
status`/`ai-film validate` report a healthy project afterward — reusing the
existing CLI's own correctness guarantees as the acceptance bar for what the
agents produce.

## 10. Future Extensions (explicitly out of scope for this spec's first slice)

- A dedicated Continuity agent, if shot volume ever justifies a second,
  independent-context pass beyond what the Storyboard agent already does inline.
- Video candidate review/refinement, once the engine gains video candidate
  storage and an edit/regenerate loop for clips (tracked as a human-interaction-
  model Future Extension, not started).
- Voice/sfx/music generation agents, and an Editor agent for final assembly —
  both explicitly out of scope here, same as they were for the engine itself.
- A programmatic cost-estimation engine in `ai_film`, replacing the static
  per-model prompt-knowledge table this spec relies on.
- **A code-level (not merely structural) guarantee behind the §3.4 cost-approval
  gate** — e.g. `approve-generation` requiring the engine to verify a completed
  human round trip rather than trusting agent-side prompt discipline. Deferred
  because it requires reopening the "no `ai_film` engine changes" Non-Goal; worth
  revisiting once this layer supports more autonomous, background, or
  multi-agent generation, where the structural mitigation in §3.4 is weaker.
