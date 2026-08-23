# Agent Layer — Design

**Status:** Approved for implementation (first slice)
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
  Storyboard/Shot Director agent (see §4) rather than split into its own agent —
  worth reconsidering if shot volume ever grows large enough that an independent
  second pass adds real value.
- **No new `ai_film` engine/schema changes.** Scenes and character bibles are
  plain markdown (§3) specifically to avoid needing new Python code — this spec
  is agents and prompts only.
- **No automatic cost-estimation engine.** Agents carry a rough per-model cost
  table as prompt knowledge (§5) to show before requesting approval; there is no
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
commands directly. Agents don't get a shortcut around it.

## 3. Data Flow

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

## 4. Agent Responsibilities in Detail

**Director/Story agent.** Runs the brainstorming conversation first (personality,
genre, style reference — converging on a brief, entirely in chat, no file writes
yet). Once the story is agreed, writes `00_story/story.md`. Then proposes a scene
breakdown — just scene titles and one-line summaries — and gets your go-ahead
*before* writing full action/dialogue for each scene. This checkpoint stays
inside the Director/Story agent rather than becoming a separate agent: the
agent that proposed the scene structure is best positioned to revise it based
on your feedback, and no other agent needs to be involved in that back-and-forth.
Once approved, writes one `02_scenes/SC*.md` file per scene.

**Character agent.** Dispatched once per unique character name found across
`02_scenes/*.md`. Discusses appearance/personality with you if the scenes didn't
already pin it down, writes `01_bibles/characters/<name>.md`, computes a rough
cost estimate for N candidates (§5), asks for approval, calls
`ai-film approve-generation --scope bibles --targets character:<name>`, then runs
`generate-candidates` → `review` (opens the gallery) → discusses your feedback →
`edit-candidate` (zero or more rounds) → `select-candidate` to lock in
`reference.png`.

**Storyboard/Shot Director agent.** For each scene, decides the shot breakdown
(how many shots, camera type/lens/movement per shot, which portion of the
scene's action/dialogue belongs to each) and writes `03_shots/SH*.json` — a
real, schema-valid shot per the existing contract, with `characters[].reference`
pointing at the locked character reference images. Runs
`ai-film check-continuity --shot <id> --status <passed|warning|failed>` for each
shot (this agent's continuity judgment, since it has full context of every shot
and every character bible — see Non-Goals for why this isn't a separate agent).
Once a batch of shots passes continuity, computes a cost estimate, asks for
approval, calls `ai-film approve-generation --scope storyboard --targets <ids>`,
then runs the same generate → review → edit → select loop as the Character
agent, per shot — critically, actually *viewing* each candidate image (via
Claude Code's native image-reading capability) before writing any edit
instruction, so refinements are grounded in what the image actually shows, not
guessed from the prompt alone.

## 5. Cost Estimation (prompt knowledge, not engine code)

Since `ai_film` has no cost-estimation logic, each agent that requests approval
carries a small, static per-model cost table in its own instructions (not
Python code) — e.g., "nano-banana ≈ $0.02/image, so N candidates ≈ $0.02×N" —
and presents that estimate to you as part of asking for approval. This number is
advisory, agent-side, and never touches `ai_film`; it exists purely so
"approve — this'll cost about $X" is a real sentence an agent can say before
calling `approve-generation`.

## 6. Entry-Point Commands

- **`/create-film "Title"`** — runs `ai-film init "Title"`, then dispatches the
  Director/Story agent. The single entry point for starting or resuming a film.
- **`/ai-film-setup`** — a prerequisite command, not part of the per-film agent
  pipeline. Checks `FAL_KEY` is set; walks through `ai-film models --capability
  <x>` for each capability (image/video/voice/sfx/music) and writes the picks
  into `config.json`. Rerunnable anytime to change providers/models.

## 7. Error Handling and Edge Cases

- **Re-entry.** `/create-film` on a project that already has `00_story/`,
  `02_scenes/`, or `03_shots/` content resumes from whatever exists rather than
  overwriting — each agent checks its own phase's expected files/state before
  starting and picks up mid-story, mid-character, or mid-shot-list as needed.
- **Shared characters.** The Character agent dispatches once per *unique*
  character name across all scenes, never once per scene-appearance.
- **Generation failures.** `CostGateError`/`ProviderError` from any `generate-*`/
  `edit-candidate` call (both already surfaced cleanly by the CLI as a message +
  exit 1, per the core engine's existing error handling) are shown to you
  directly by the agent, which then asks how to proceed — retry, adjust the
  prompt, or skip the shot for now — rather than retrying silently or stalling.
- **Changing your mind after locking a candidate.** `select-candidate` is already
  idempotent/re-runnable (§ human-interaction-model spec) — an agent can freely
  re-run it if you want to swap a previously-locked pick, no special handling
  needed beyond what the CLI already guarantees.

## 8. Testing Strategy

This is a prompt/agent-instruction layer, not Python code — there is no `pytest`
suite for it. Verification is behavioral: manually walking a real film through
`/create-film` end to end (brainstorm → scenes → character candidates → shot
candidates → a locked storyboard image per shot) and confirming each agent
produces schema-valid `shot.json` files, correctly enforces both cost-gate
checkpoints (generation genuinely blocked before approval, per the CLI's own
existing tested behavior), and that `ai-film status`/`ai-film validate` report a
healthy project afterward — reusing the existing CLI's own correctness
guarantees as the acceptance bar for what the agents produce.

## 9. Future Extensions (explicitly out of scope for this spec's first slice)

- A dedicated Continuity agent, if shot volume ever justifies a second,
  independent-context pass beyond what the Storyboard agent already does inline.
- Video candidate review/refinement, once the engine gains video candidate
  storage and an edit/regenerate loop for clips (tracked as a human-interaction-
  model Future Extension, not started).
- Voice/sfx/music generation agents, and an Editor agent for final assembly —
  both explicitly out of scope here, same as they were for the engine itself.
- A programmatic cost-estimation engine in `ai_film`, replacing the static
  per-model prompt-knowledge table this spec relies on.
