# Media Agent — Design

**Status:** Approved for implementation
**Date:** 2026-08-28

## 1. Overview

`/create-film` currently takes a project from brainstorm through a locked storyboard *image* per shot, then stops — Step 5 of `create-film.md` just tells the user that `generate-video`/`generate-voice`/`generate-sfx`/`generate-music`/`render` are manual CLI steps from here. The media-review-design.md spec (already implemented on master) built the engine primitives for reviewing and fixing that generated media — version/history bookkeeping, a feedback log, a static review page, a cheap audio-offset fix — but explicitly left the *agent* that drives those primitives through conversation as future work (§10: "A Media Review Agent... the direct analogue of how agent-layer-design.md sits on top of human-interaction-model-design.md").

This spec is that agent. It is a pure prompt/agent-layer addition — **zero engine changes** — extending `/create-film`'s pipeline with a fourth phase: for each shot with a locked image, generate its video/voice, let the human review it through conversation, and apply fixes until they're satisfied.

### Goals

- Extend `/create-film` so the film-generation pipeline reaches "reviewed video+voice per shot," not just "locked storyboard image per shot."
- A per-shot agent (`ai-film-media`) that generates a shot's video (and voice, if it has dialogue), builds the review page, collects the human's reaction through the existing `NEEDS_INPUT`/`HUMAN_RESPONSE` protocol, and either applies a cheap fix, edits a narrow whitelist of `shot.json` fields and regenerates, or asks for clarification when it can't confidently map feedback to an action.
- A single, reactive, film-wide cost approval: the first shot to need one estimates and approves generation for every shot still in scope, so the other shots' dispatches never re-prompt for approval.
- Batched review cycles: multiple feedback items (and multiple points needing clarification) from one human message are collected and handled together, producing one rebuilt review page per round — not one round trip per comment.
- Honest media agent: it never claims to have watched or heard something it can only read metadata about.

### Non-Goals

- **No engine or CLI changes.** Every command this agent calls already exists on master. If a gap is found during implementation, that's a defect in this spec, not license to add engine code.
- **No automatic sfx/music generation.** These stay `not_required` unless a human explicitly asks for them on a specific shot — see §4.2 for why this is agent policy, not something the engine's cost gate enforces on its own.
- **No new approval round trips per shot.** One film-wide approval (§3) covers initial generation and every later `--force` regeneration from a fix, for every shot in scope, for the rest of the run.
- **No fixed cap on review rounds.** A shot stays in review until the human says they're happy with it, same as the Storyboard agent's candidate loop has no round cap.
- **No perceptual claims the agent can't back up** (§4.1) — this is a hard behavioral rule, not a style preference.
- **No standalone entry point.** Per the brainstorming discussion, this agent is dispatched only from `/create-film`'s pipeline — no separate `/review-shots`-style command in this slice.

## 2. Architecture

```
/create-film (orchestrator, unchanged Steps 1-4, extended Step 5)
    │
    ├── determines shot scope: every shot with generation.image.artifact populated
    │
    └── for each shot in scope, in order:
            dispatch ai-film-media(PROJECT_PATH, shot_id, in_scope_shot_ids)
            run the existing NEEDS_INPUT/HUMAN_RESPONSE protocol loop
              (verbatim reuse of agent-layer-design.md §3 — this spec
              does not redefine that protocol, only uses it)
                    │
                    ▼
              ai-film-media (one dispatch per shot)
                    │
        ┌───────────┼────────────┐
        │           │            │
    generate      review      feedback → fix
   (video, voice   (review-    (add-feedback,
    if dialogue)    media)      apply-audio-offset,
        │                       generate-* --force,
        │                       resolve-feedback)
        └───────────┴────────────┘
                    │
              ai-film CLI (unchanged) ← feedback_store / media_review (unchanged)
```

Each shot's media agent is a fresh dispatch — no shared context between shots, matching `ai-film-character.md`'s per-character granularity rather than `ai-film-storyboard.md`'s single-run-for-everything shape. This keeps one shot's multi-round feedback conversation from bloating into every other shot's context, which matters more here than it did for image candidates: a media review round can touch four tracks (video/voice/sfx/music) at once, not one image.

## 3. Dispatch Scope and Cost Approval

### 3.1 What the orchestrator passes at dispatch time

`/create-film`'s Step 5 computes `in_scope_shot_ids` **once**, before dispatching the first shot: every shot ID with `generation.image.artifact` populated (i.e., every shot the Storyboard phase actually locked). This list is fixed for the whole Step 5 run — it is not recomputed by each shot's agent. Each `ai-film-media` dispatch receives:

- `PROJECT_PATH`
- `shot_id` — the one shot this dispatch owns
- `in_scope_shot_ids` — the complete, orchestrator-determined list (including `shot_id` itself)

**Invariant:** the approval scope is determined by the orchestrator for this run, not reconstructed by any dispatched agent. A shot that needs no video generation at all (e.g., already has one from an earlier partial run) still belongs to `in_scope_shot_ids` if it's part of this pipeline pass — inclusion in the list is about scope membership, not about what work remains.

### 3.2 Reactive, film-wide approval

No separate cost-approval step is added to the orchestrator or to a designated "first" shot — every shot's agent is written identically. Since Step 5 dispatches shots in order (§2, §6), in the normal case this means the first shot in that sequence that actually needs a paid generation call is the one that hits `CostGateError` and does the estimate-and-approve round trip, covering **every shot in `in_scope_shot_ids`**, not just its own:

```bash
ai-film approve-generation --scope storyboard --targets <every id in in_scope_shot_ids, comma-separated>
```

**Invariant: the agent must never call `approve-generation` with a subset of `in_scope_shot_ids` during this Step 5 run.** Always the full list, always in one call — never per-shot, never a partial batch, regardless of which shot happens to trigger it.

Every subsequent shot's `generate-video`/`generate-voice` call — whether it's that shot's first generation or a later `--force` regeneration from a fix — is already covered by this approval and needs no further round trip, because `approve-generation --scope storyboard` is keyed by shot ID only (per the existing `_SCOPE_BY_STAGE` mapping, unchanged by this spec) and this approval is never re-issued for a narrower list during the run.

Cost table (advisory only, same "not real-time pricing" caveat every existing agent's table already carries):

| Model | Approx. cost |
|---|---|
| fal/veo-3 (video) | ~$0.50 per generation |
| fal/csm-1b (voice) | ~$0.02 per generation |
| fal/csm-1b (music) | ~$0.05 per generation |
| fal/thinksound (sfx) | ~$0.02 per generation |
| mock | $0.00 |

The estimate covers video for every shot in scope, plus voice for every shot in scope whose `dialogue.text` is non-empty (sfx/music are never included in this upfront estimate — see §4.2).

### 3.3 What this approval does *not* cover

The film-wide approval authorizes: initial generation of video (every shot) and voice (shots with dialogue), and any later `--force` regeneration of those same stages triggered by a fix. It does **not**, on its own, license the agent to generate a stage that was `not_required` going in (sfx or music) just because the shot's ID is in the approved scope.

This is worth being precise about: the **engine's** cost gate doesn't distinguish stages at all — once a shot ID is approved, `is_approved` returns true for a `generate-sfx --force` call on that shot exactly as readily as for `generate-video`. The restriction that stops the agent from generating sfx/music unprompted is **agent policy** (§4.2), not an engine guarantee. Don't describe it as the engine enforcing stage-level scope in any implementation of this spec — it doesn't, and a future engine change that made it actually enforce this would be a separate, real change, not something this spec can claim already exists.

The same engine/policy distinction bounds *which shots* a generation call may target, not just which stages: **a generation call is eligible to rely on the existing film-wide approval only when its shot ID belongs to `in_scope_shot_ids`.** The engine's approval record would technically also cover any other shot ID that happened to be included in an *earlier* approval call from a prior run (approval persists in `config.json` across runs — see §5) — but the agent must never reason "the engine would technically allow this" as license to act on a shot outside its current dispatch's `in_scope_shot_ids`. A dispatch for shot X touches only shot X; it has no legitimate reason to call `generate-*` for any other shot ID regardless of what the engine's approval record happens to contain.

## 4. Per-Shot Agent Flow

### 4.1 Generate and open review — and the perception rule

For the assigned shot: `generate-video` always; `generate-voice` only if `dialogue.text` is non-empty. **This is the sole gating condition, decided deliberately, not left ambiguous:** `dialogue.speaker` is not a second gate — it's passed through to the voice provider as-is (the engine's `VoiceGenerationRequest` already defaults it to `""` with no error if unset). The Storyboard agent's own shot-writing template sets `dialogue.text`/`dialogue.speaker` together whenever a shot has a real line and leaves both `""` when it doesn't (`ai-film-storyboard.md` §Step 2), so in practice a non-empty `dialogue.text` already implies a populated `dialogue.speaker` by construction — the media agent doesn't need its own redundant check on top of that. Then `review-media --shot <id>` to build the page.

**Hard rule, not a style preference: the agent MUST NOT claim to have visually or audibly evaluated media it cannot directly perceive.** It can inspect: the `review-media` HTML's structure and text content via Read, artifact durations and version/history metadata from `shot.json`, and any waveform PNGs the review page generated (also via Read — the agent's Read tool does display image content, same capability the Storyboard agent already relies on for storyboard images, so a waveform PNG is genuinely inspectable; the video/audio media files themselves are not).

Questions to the human are phrased around what the agent actually knows, never around a fabricated impression:

- Correct: *"I've generated v2 — the review page shows the voice track is 3.2s long and the video is 6.0s. What do you think?"*
- Wrong: *"The voice sounds too fast."* — the agent cannot know this.

### 4.2 Collect feedback — batched, not one round trip per item

The `NEEDS_INPUT type: clarification` asking for reaction to a shot accepts a single human reply that may contain multiple distinct complaints (e.g., "the voice is early and the camera angle feels too wide"). The agent processes them in ordered passes within the round — because an ambiguous complaint genuinely cannot be fixed until the human resolves the ambiguity, a further round trip is a hard dependency for those items, not an optional nicety, and the pass ordering below exists to keep that dependency to **at most one clarification round trip and one new-stage-confirmation round trip per review round**, never one per item:

**Pass 1 — classify every complaint from the reply, immediately, no waiting:**

For each distinct complaint: call `add-feedback` with the agent's best judgment of `target` (`video`/`voice`/`sfx`/`music`/`sync`) and `at`/`range` parsed from what the human said, then classify it into exactly one of:

- **Cheap fix** — a timing/sync complaint mappable to a concrete audio track offset. Apply `apply-audio-offset` now (no round trip — matches the earlier decision that free, reversible fixes don't need pre-confirmation). Don't call `resolve-feedback` yet — see "Resolve and rebuild" below.
- **Field edit** — the complaint maps confidently to one of the whitelisted `shot.json` fields (table below). Edit the field now. **Don't regenerate yet** — see "Regenerate once per stage" below.
- **New stage requested** — the complaint explicitly asks for sfx or music where the shot currently has `not_required`. Don't call `generate-sfx`/`generate-music` yet — collect it for the confirmation pass below, even though the film-wide approval would technically permit the call (§3.3): this is the one case where the agent asks before spending.
- **Not confidently mappable** — the complaint doesn't clearly fit any of the above. Don't guess — collect it for the clarification pass below.

**Pass 2 — at most two round trips, only if Pass 1 left anything pending:**

- If any complaints were classified "new stage requested," bundle all of them into one `NEEDS_INPUT type: confirmation` (e.g. "you asked for music on this shot and sfx on another — confirm generating both?") and stop the turn until resumed.
- If any complaints were classified "not confidently mappable," bundle all of them into one `NEEDS_INPUT type: clarification` (e.g. "do you want the camera movement changed, or the character's action?" covering every unmapped item from this reply) and stop the turn until resumed.
- These are two different `NEEDS_INPUT` types (confirmation vs. clarification), so they're two distinct round trips when both are needed — but each is still exactly one round trip regardless of how many items it covers. If Pass 1 left nothing pending in a category, that category's round trip doesn't happen at all.
- Once resumed, treat each answer exactly like a Pass 1 outcome: an approved new-stage request becomes a field-edit-shaped case (its regeneration is `generate-sfx`/`generate-music --force` after this pass, not a whitelisted `shot.json` field — no field to edit, just the first-time generation); a clarified ambiguous item becomes a field edit or cheap fix per what the human actually said.

**Regenerate once per stage, after all passes are done, not once per feedback item.** Track every stage (`video`, `voice`, and — only if a new-stage request was approved — `sfx`/`music`) that had at least one field edit or first-time-generation approval across *all* passes in this round. Once every edit/approval for this round is in, call `--force` regeneration **at most once per affected stage** — e.g. one complaint about `camera` and another about `action` in the same reply produce exactly one `generate-video --force`, not two, even though they touch different fields. This is a cost control, not just tidiness: each stage's `--force` call is real spend, and a human's single reply must not silently trigger more paid generations than the number of stages it actually touched.

**Resolve and rebuild.** For every feedback item resolved this round — cheap fix, field edit, or clarified/confirmed item — call `resolve-feedback` with a `--resolution` naming the concrete change (see the sync-vs-track distinction below), after the stage-level regeneration above has actually happened, so the resolution text accurately describes what's now live. Then rebuild the review page once — `review-media --shot <id>` — and ask again whether the human is happy with this shot or wants another round.

**Whitelisted field edits** — the agent may directly edit only these fields, chosen because each maps unambiguously to one complaint category; anything else falls into "not confidently mappable" above:

| Feedback is about... | Allowed edit | Then regenerate |
|---|---|---|
| Dialogue wording | `dialogue.text` | `generate-voice --force` |
| Who's speaking | `dialogue.speaker` | `generate-voice --force` |
| Visual content/style | `visual` | `generate-video --force` |
| Camera shot/movement | `camera` | `generate-video --force` |
| What happens in the shot | `action` | `generate-video --force` |
| Timing/sync between tracks | *(no field edit)* | `apply-audio-offset` |

**`sync`-targeted feedback resolves to a concrete track fix, and the record says so.** A human's complaint about relative timing is filed with `target=sync` (it's not really "a problem with the video" or "a problem with the audio" in isolation), but the actual fix always lands on one specific track. Example:

```
add-feedback --target sync --at 3.0 --note "voice comes in early"
apply-audio-offset --track voice --offset-ms 400
resolve-feedback --id FB-00N --resolution "voice offset +400ms"
```

*(Corrected during final review: "voice comes in early" needs the track delayed. Per the engine's real `apply_audio_offset` behavior — a positive `--offset-ms` prepends silence (delays); a negative one trims from the head (advances) — the fix for "early" is a positive offset. The example previously had the sign backwards.)*

The feedback entry's `target` stays `sync` (that's what was reviewed); the `resolution` text names what was actually changed. Nothing in the engine needs to change for this — `target` and `resolution` are already independent fields in the feedback log.

### 4.3 Confirmation and moving on

Once the human confirms they're happy with a shot (a plain "looks good" reply, or a `type: selection`/`confirmation` round trip if the agent wants to be explicit), the dispatch reports a genuine completion — not a `NEEDS_INPUT` — same completion-signaling convention every other agent in this pipeline already uses.

## 5. Re-Entry and State

`generation.video.status == "completed"` (an artifact exists) is **not** the same as "a human has reviewed and approved this shot." Re-running `/create-film` on a project that already has some shots with generated video must not silently treat those as done. The state rule:

```
video (and voice, if dialogue) artifact exists
  + no open feedback entries
  + no human confirmation recorded in THIS run
  → REVIEW_REQUIRED (open the review page and ask, per §4.2)

any open feedback entries remain
  → resume the fix loop for those entries (§4.2)

video (or voice, if dialogue) artifact missing
  → generate the missing stage(s) (§4.1), then REVIEW_REQUIRED
```

**§4.1's generation step is never skipped on re-entry — it always runs.** This is deliberate, not an oversight: `generate-video`/`generate-voice` are already idempotent at the engine level (`run_generation_stage` returns a stage's existing artifact immediately, with no new provider call or spend, whenever that stage's `status` is already `"completed"` and `--force` wasn't passed). Calling both unconditionally on every dispatch is therefore free for stages already done, and it's what correctly fills in a stage that's genuinely still missing — e.g. a shot whose video completed in an earlier run but whose voice never got generated (perhaps that run was interrupted, or dialogue was added to the shot afterward). A version of this rule that special-cased "skip generation if *any* artifact exists" would silently leave that voice track ungenerated forever; always running §4.1 avoids that without needing the agent to reason about which specific stage is missing.

This also means the reactive approval in §3.2 keeps working correctly across separate `/create-film` runs without any extra logic: `approve-generation`'s record persists in `config.json`, so a re-entry run's `generate-video`/`generate-voice` calls simply find `is_approved` already true and proceed straight to the (free, idempotent) no-op or the genuinely-needed generation — no `CostGateError`, no redundant approval round trip.

There is no persistent "confirmed" flag written anywhere (the engine has no such field, and this spec adds none) — confirmation is a fact about *this conversation*, not project state. A shot with a completed video artifact and an empty feedback log, encountered in a *new* `/create-film` run, is `REVIEW_REQUIRED` again: the agent opens the review page and asks, it does not assume a prior run's silence meant approval.

**"Confirmation recorded in THIS run" is conversation state held by the agent/orchestrator dispatch, not a value read from `shot.json`, the feedback log, or any other project artifact.** An implementation of this spec must not invent a persistence mechanism for it — no new field on the shot, no new file, nothing written to disk. If the dispatch ends (agent reports completion, or the session ends) and a later run re-examines the same shot, that later run has no way to know a human already said "looks good" last time, by design — it re-derives `REVIEW_REQUIRED` from the state rule above and asks again.

## 6. Entry-Point Changes

`create-film.md`'s Step 5 changes from a closing note into an actual pipeline step:

- Compute `in_scope_shot_ids` (every shot with `generation.image.artifact` populated).
- For each shot in that list, in order: dispatch `ai-film-media` with `PROJECT_PATH`, that shot's ID, and the full `in_scope_shot_ids` list, and run the same protocol loop used for Steps 2-4.
- After every shot reports completion, show the user a summary (shots reviewed, any left with open feedback because the human asked to pause) and *then* mention `render` as the remaining manual step — rendering the final cut stays out of scope for both this spec and the media-review-design.md spec it builds on.

## 7. Error Handling

- **Provider failures** (`generate-video`/`generate-voice`/`generate-sfx`/`generate-music` raising `ProviderError`): same `type: confirmation` retry/adjust/skip pattern the Character and Storyboard agents already use — never retried silently.
- **`apply-audio-offset` failures** (ffmpeg missing, or the engine's own over-trim rejection from a too-large negative offset): reported plainly with the real error text, with a suggestion to try a full regeneration instead if the offset genuinely can't be applied.
- **Cost-gate errors on a shot the agent believed was already approved** (shouldn't happen given §3.2, but if the orchestrator's `in_scope_shot_ids` was somehow stale): treated as a protocol error, not silently re-approved — report it and let the human decide.
- **Protocol errors** (malformed `NEEDS_INPUT`, mismatched `HUMAN_RESPONSE` id): same as every other agent in this pipeline — stop and report, never guess.
- **A failed `--force` regeneration mid-round** (the engine raises `ProviderError` after a field edit was already applied): the media agent relies entirely on `run_generation_stage`'s existing archive/restore behavior (media-review-design.md §4.1's `ArchiveResult.restore`) to leave the shot's prior working artifact intact — **the media agent MUST NOT implement its own artifact rollback, backup-copy, or recovery logic.** If the engine's own rollback guarantee were ever insufficient, that's a defect in the engine layer to fix there, not something this agent should work around with duplicated filesystem logic.
- **The agent MUST NOT invoke `render` at any point in its dispatch.** Rendering the final cut stays the user's manual step (§1 Non-Goals, §6) for the entire Step 5 lifecycle — generate, review, flag, fix, resolve, re-review, confirm — none of that phase touches `render`, regardless of how many shots reach a confirmed state. This is worth stating as a hard boundary rather than an implied one: agents built to "finish the pipeline" have a natural pull toward chaining the next logical CLI command, and `render` immediately follows this phase in the README's command list.

## 8. Testing Strategy

This is a prompt/agent-instruction layer, not Python code — no `pytest` suite, matching agent-layer-design.md's own testing approach. Verification is behavioral: running `/create-film` end to end on a project with locked storyboard images through the new Step 5, confirming —

- the film-wide approval genuinely happens once (not per shot), covering the exact `in_scope_shot_ids` list and nothing narrower;
- a shot with dialogue gets both video and voice generated, gated on `dialogue.text` alone;
- a reply with two complaints mapping to the *same* stage (e.g. both `camera` and `action`) produces exactly one `--force` regeneration of that stage, not two;
- a reply mixing an immediately-mappable complaint with a genuinely ambiguous one produces exactly one clarification round trip covering only the ambiguous part — the mappable part's `add-feedback` call and field edit happen in Pass 1 without waiting on it, even though (per the "regenerate once per stage, after all passes" rule) its actual regeneration and the round's one rebuilt review page still land together with the clarified item's fix, not separately;
- a sfx/music request on a `not_required` stage produces exactly one confirmation round trip, and a later fix to that same now-generated stage doesn't re-ask;
- a sync complaint resolves to a named track fix (`resolution` names the concrete track/offset, `target` stays `sync`);
- re-running `/create-film` on a project with unreviewed-but-generated shots re-enters review (`REVIEW_REQUIRED`) rather than treating them as done, and a shot whose video exists but whose voice doesn't (dialogue added after an interrupted run) gets its voice generated on re-entry rather than staying permanently silent;
- no `render` invocation occurs anywhere during Step 5, on any path, for any number of shots.

## 9. Future Extensions (explicitly out of scope for this spec)

- A standalone entry point for revisiting review on an already-built film without going through `/create-film` again.
- Batch-dispatching multiple shots' media agents in parallel rather than strictly in order (deferred the same way the Character agent's sequential-not-parallel dispatch is deferred today).
- Automatic sfx/music suggestion (the agent proposing tracks unprompted) rather than only generating them on explicit request.
- Wiring `render`'s final-cut assembly to pull in the reviewed voice/sfx/music tracks — `render.py` still only assembles video, per media-review-design.md's own Non-Goals.
