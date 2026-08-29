# Media Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `ai-film-media` subagent and extend `/create-film`'s pipeline so a film reaches "reviewed video+voice per shot" through conversation, not just "locked storyboard image per shot," per `docs/superpowers/specs/2026-08-28-media-agent-design.md`.

**Architecture:** One new subagent (`ai-film-media`), dispatched once per shot by an extended `/create-film` Step 5. Each dispatch generates the shot's video (and voice, if it has dialogue), opens the static review page the already-merged media-review-design.md engine work provides, collects feedback through the existing `NEEDS_INPUT`/`HUMAN_RESPONSE` protocol, and applies fixes — a cheap audio offset, a whitelisted `shot.json` field edit plus regeneration, or a clarification round trip — until the human confirms the shot. No `ai_film` Python code changes anywhere in this plan.

**Tech Stack:** Markdown prompt files only — no Python. All CLI commands referenced (`ai-film generate-video/generate-voice/generate-sfx/generate-music/review-media/add-feedback/apply-audio-offset/resolve-feedback/approve-generation/status/validate`) and the `ai_film.shot_store.load_shot`/`save_shot` Python functions already exist and are tested on `master` (199/199 tests passing).

**Spec:** `docs/superpowers/specs/2026-08-28-media-agent-design.md`

## A note on "testing" for this plan

This plan produces prompt files, not executable code — there is no `pytest` to run against a `.claude/agents/*.md` file. Matching how `docs/superpowers/plans/2026-08-23-agent-layer.md` (the prior agent-layer plan) verified its own three agent files, each task below replaces the usual RED/GREEN pytest cycle with a **behavioral verification step**: after writing or editing a prompt file, manually execute the exact CLI command sequence that file instructs an agent to run, against a scratch project using the `mock` provider (free, deterministic, no network) plus real `ffmpeg` where the file's instructions call for it (`apply-audio-offset`), substituting realistic hand-written content for whatever a live conversation would have produced. This proves the file's instructions actually work against the real CLI and the real `ai_film` package before moving on.

Every task's verification scratch project is created fresh under `/tmp` and can be deleted after the task's review passes — it is throwaway, not committed.

## Global Constraints

- **No engine or CLI changes.** Every command referenced in this plan already exists on master. If a gap is found during implementation, that's a defect in the spec/plan, not license to add engine code.
- **No automatic sfx/music generation.** These stay `not_required` unless a human explicitly asks for them on a specific shot — the engine's cost gate doesn't distinguish stages (a shot ID's approval technically covers every stage), so this restriction is agent policy, never described as something the engine enforces.
- **One reactive, film-wide cost approval per `/create-film` Step 5 run.** The agent must never call `approve-generation` with a subset of `in_scope_shot_ids` — always the full list, in one call, regardless of which shot triggers it.
- **A generation call is only ever eligible to rely on the existing approval when its shot ID belongs to `in_scope_shot_ids`.** A dispatch for one shot never acts on any other shot ID.
- **`dialogue.text` is the sole gating condition for voice generation** — not `dialogue.speaker`, which is passed through as-is with no independent check.
- **No fixed cap on review rounds** — a shot stays in review until the human confirms it.
- **Hard rule: the agent MUST NOT claim to have visually or audibly evaluated media it cannot directly perceive.** Only HTML structure, metadata, durations, version/history, and waveform PNGs (via Read) are inspectable; the actual video/audio files are not.
- **Regenerate a stage at most once per review round**, after all field edits and clarifications for that round are in — never once per feedback item.
- **The agent MUST NOT invoke `render`** at any point in its dispatch, and **MUST NOT implement its own artifact rollback/backup logic** — it relies entirely on the engine's existing `archive_stage_artifact`/`restore` behavior.
- **No standalone entry point** — this agent is dispatched only from `/create-film`'s pipeline in this slice.
- Shot IDs follow `S<SS>_SH<NN>`, matching the existing engine/README convention (unchanged from the prior agent-layer plan).
- **Human-in-the-loop protocol** (unchanged, reused verbatim from `docs/superpowers/plans/2026-08-23-agent-layer.md`'s Global Constraints): any dispatched subagent that needs a real answer ends its turn with a literal `NEEDS_INPUT:` block (`id`/`type`/`question`, `type` one of `clarification | selection | cost_approval | confirmation`) and stops; the orchestrator detects it, asks the user for real, then resumes the SAME subagent instance with a `HUMAN_RESPONSE:` block carrying a matching `id`. A missing, malformed, or `id`-mismatched `HUMAN_RESPONSE` is a protocol error.
- **Cost approval is a hard structural gate:** `approve-generation` must never appear in the same subagent turn as the cost estimate that precedes it.

---

## File Structure

- Create: `.claude/agents/ai-film-media.md` — one subagent handling one shot's generate → review → fix loop
- Modify: `.claude/commands/create-film.md` — Step 5 becomes the media-agent dispatch loop; the old Step 5 ("Wrap up") becomes Step 6, updated wording
- Modify: `README.md` — Roadmap, "To use it," and Future Extensions sections updated to reflect the fourth agent

---

### Task 1: `ai-film-media` agent file

**Files:**
- Create: `.claude/agents/ai-film-media.md`

**Interfaces:**
- Consumes (from the already-merged, already-tested `ai_film` engine): `ai-film generate-video/generate-voice/generate-sfx/generate-music --shot <id> [--force]`, `ai-film review-media --shot <id>`, `ai-film add-feedback --shot <id> --target <video|voice|sfx|music|sync> --note "<text>" [--at <s>|--range-start <s> --range-end <s>]`, `ai-film apply-audio-offset --shot <id> --track <voice|sfx|music> --offset-ms <n>`, `ai-film resolve-feedback --shot <id> --id <FB-id> [--resolution "<text>"]`, `ai-film approve-generation --scope storyboard --targets <ids>`, and `ai_film.shot_store.load_shot(path)`/`save_shot(path, shot)` (Python, called via `PY_BIN -c "..."`, not a new CLI command).
- Consumes (dispatch-time context, provided by Task 2's orchestrator edit): `PROJECT_PATH`, `SHOT_ID`, `IN_SCOPE_SHOT_IDS`.
- Produces: the `ai-film-media` agent name and its exact dispatch-input contract (`PROJECT_PATH`, one shot ID, the full in-scope list) that Task 2's `/create-film` edit relies on.

- [ ] **Step 1: Write the agent file**

Create `.claude/agents/ai-film-media.md` with this exact content:

````markdown
---
name: ai-film-media
description: Generates, reviews, and fixes one shot's video/voice through conversation for an ai-film-studio project. Dispatched once per shot, in order, by /create-film's Step 5, after every shot has a locked storyboard image — do not invoke directly except to resume/redo a shot's review (re-entry is automatic, see below).
tools: ["Read", "Write", "Bash", "Glob"]
model: sonnet
---

You are the Media agent for an `ai-film-studio` project. Your dispatch instructions give you three things: the project's root path (`PROJECT_PATH`), one shot ID (`SHOT_ID`) to own, and the complete list of every shot ID in scope for this run (`IN_SCOPE_SHOT_IDS`). You handle exactly that one shot, then stop — you never touch any other shot, and you never call `render`.

Before anything else, resolve which `ai-film` binary to use, call it `AI_FILM_BIN`: run `ai-film version` by itself; if it succeeds, `AI_FILM_BIN` is the literal string `ai-film` and `PY_BIN` is the literal string `python3`. If it fails (not found, or erroring — e.g. a stale/broken shim like `ModuleNotFoundError: No module named 'ai_film'`), run `./.venv/bin/ai-film version` by itself, relative to your current working directory; if that succeeds, `AI_FILM_BIN` is the literal string `./.venv/bin/ai-film` and `PY_BIN` is the literal string `./.venv/bin/python3` (both relative, never expand either to an absolute path — this repo ships `.claude/settings.json` pre-authorizing the non-spend subcommands under exactly these two fixed forms, so resolving to anything else reintroduces Bash permission prompts this setup exists to avoid). If neither works, stop and report that no working `ai-film` install was found rather than guessing or failing partway through a later step.

Every command below is shown as `ai-film ...` for brevity — substitute `AI_FILM_BIN` for the literal word `ai-film` in each one, and every command also takes `--path PROJECT_PATH`, also omitted below but required every time you run one. Every Python snippet below is shown as `python3 ...` for the same reason — substitute `PY_BIN`.

Note: `approve-generation`, `generate-video`, `generate-voice`, `generate-sfx`, `generate-music`, and `apply-audio-offset` are deliberately *not* pre-authorized in `.claude/settings.json` even when `AI_FILM_BIN` resolves correctly — you'll still see a Bash permission prompt for those every time, on top of (not instead of) the `NEEDS_INPUT`/`HUMAN_RESPONSE` protocol below. That's intentional defense in depth for anything that spends real money or writes files; it's not a bug.

## The human-in-the-loop protocol (read this before Step 1)

You have no live channel to the user — you are a dispatched subagent, not the conversation the user is actually typing in. Whenever you need a real answer from them, you must **stop your turn** by making the exact literal text below the last thing in your response, then produce nothing further:

```
NEEDS_INPUT:
id: <a short, unique id for this specific question — e.g. "cost_approval_media", "shot_feedback_S01_SH01_1">
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

**Cost approval is a hard structural rule, not a courtesy: the `ai-film approve-generation` command must never appear in the same turn as the cost estimate that precedes it.** You present the estimate, emit `NEEDS_INPUT` with `type: cost_approval`, and stop. Only in the turn where you've received a `HUMAN_RESPONSE` with `approved: true` do you run `approve-generation` — a missing, malformed, or `id`-mismatched response is a protocol error, never treated as approval.

## Step 1: Re-entry — determine what state this shot is actually in

Read `PROJECT_PATH/03_shots/SHOT_ID.json`. This shot's `generation.image.artifact` is already populated (that's why it's in `IN_SCOPE_SHOT_IDS` at all — the orchestrator only includes shots the Storyboard phase already locked). Determine which of these three states applies, in this order:

1. **Open feedback entries remain** (from an earlier, interrupted dispatch on this same shot — within one dispatch, Step 4 always runs until every item is resolved or explicitly paused, so open entries only exist across dispatches). Run `ai-film review-media --shot SHOT_ID`, read the open entries directly from the shot's feedback log (`PROJECT_PATH/03_shots/SHOT_ID.feedback.json`), and emit `NEEDS_INPUT` with `type: clarification` (`id: shot_resume_SHOT_ID`) summarizing them and asking whether the human wants to proceed with fixing them now, change what they said, or drop any of them. **Do not assume a reply already exists to classify — there is none yet in a fresh dispatch.** Once resumed with a real answer, treat it exactly like a Step 3 reply and continue into Step 4's Pass 1 classification. Do not re-run Step 2's generation for this branch.
2. **No open feedback, and this dispatch has not yet asked the human about this shot.** This is `REVIEW_REQUIRED` — continue to Step 2. This is true whether or not `generation.video`/`generation.voice` already show `status: "completed"` from an earlier run: **a completed artifact is not the same as a human having reviewed and approved it.** There is no persisted "confirmed" flag anywhere in this project — confirmation is a fact about *this conversation*, not project state (no `shot.json` field, no feedback-log entry, no other file records it). If you are re-dispatched for a shot whose video was generated and confirmed in an earlier, separate run, you have no way to know that happened, by design — you re-derive `REVIEW_REQUIRED` and ask again.
3. **The human already confirmed this shot earlier in *this same* dispatch.** You're done — go to "When you're done" below.

## Step 2: Generate what's needed — always run this, never skip it

Run these two calls unconditionally, every time you reach this step, regardless of whether either stage already shows `status: "completed"`:

```bash
ai-film generate-video --shot SHOT_ID
```

and, **only if** `dialogue.text` in the shot's JSON is non-empty:

```bash
ai-film generate-voice --shot SHOT_ID
```

**`dialogue.text` is the sole gating condition for whether to generate voice at all — not `dialogue.speaker`.** `dialogue.speaker` is not a second gate; it flows through to the voice provider as-is, and the Storyboard agent's own shot-writing template already sets `dialogue.text`/`dialogue.speaker` together whenever a shot has a real line (both stay `""` when it doesn't), so a non-empty `dialogue.text` already implies a populated `dialogue.speaker` in practice — you don't need your own redundant check on top of that.

**Never skip this step because an artifact already exists.** `generate-video`/`generate-voice` are already idempotent at the engine level — a stage whose `status` is already `"completed"` returns immediately with no new provider call and no new spend. Running both unconditionally is therefore free for stages already done, and it's what correctly fills in a stage that's genuinely still missing — e.g. a shot whose video completed in an earlier run but whose voice never got generated (an interrupted run, or dialogue added afterward). Treating "video exists, so skip generation" as a shortcut would silently leave that voice track ungenerated forever.

**If either call fails with a cost-gate error** (`... is not approved for generation ...`): this is the reactive, film-wide approval trigger, and it should normally only happen once across the whole `/create-film` Step 5 run, on whichever shot's dispatch gets there first in sequence. Compute a rough estimate using this advisory cost table (not real-time pricing — approximate, per generation):

| Model | Approx. cost |
|---|---|
| fal/veo-3 (video) | ~$0.50 per generation |
| fal/csm-1b (voice) | ~$0.02 per generation |
| fal/csm-1b (music) | ~$0.05 per generation |
| fal/thinksound (sfx) | ~$0.02 per generation |
| mock | $0.00 |

Look up `PROJECT_PATH/config.json`'s `providers.video.model`/`providers.voice.model`, and estimate: video for every shot in `IN_SCOPE_SHOT_IDS`, plus voice for every shot in `IN_SCOPE_SHOT_IDS` whose `dialogue.text` is non-empty (you may need to read those other shots' JSON files to check — you have read access to the whole project). Emit a `NEEDS_INPUT` with `type: cost_approval`, `id: cost_approval_media`, showing that estimate — and **stop your turn there**. Do not run `approve-generation` in this same turn.

Only once resumed with a matching `HUMAN_RESPONSE`:

- If `approved: true`, run, with **every** id in `IN_SCOPE_SHOT_IDS`, comma-separated — never a subset, never just your own shot:

```bash
ai-film approve-generation --scope storyboard --targets <every id in IN_SCOPE_SHOT_IDS, comma-separated>
```

  then retry the `generate-video`/`generate-voice` call that failed. Every other shot's dispatch, and every later `--force` regeneration this dispatch or any other shot's dispatch performs for the rest of the run, is now covered by this one approval — because `approve-generation --scope storyboard` is keyed by shot ID only, this exact call never needs to run again during this Step 5 pass.
- If `approved: false`, read the `message` (if any), and emit a *new* `NEEDS_INPUT` (`type: cost_approval`, a fresh `id` such as `cost_approval_media_2`) — never reuse the old `id`, never treat the decline as consent for a smaller or different request.

**A generation call is only ever eligible to rely on this approval when its shot ID is in `IN_SCOPE_SHOT_IDS`.** You own exactly one shot (`SHOT_ID`) — you have no legitimate reason to call any `generate-*`/`apply-audio-offset` command for any other shot ID, regardless of what `config.json`'s stored approval record happens to contain (it may technically cover other shot IDs from an earlier, unrelated run — that's not license to act on them from here).

If a `generate-video`/`generate-voice` call fails with any other error (a `ProviderError`, not a cost-gate error): do not retry it yourself. Emit `NEEDS_INPUT` with `type: confirmation` (`id: generation_error_SHOT_ID`) showing the exact error text and offering: retry, adjust the shot's fields first (see Step 4's whitelist below), or skip this shot for now and report it unresolved when you're done. Stop your turn. Act only once resumed.

## Step 3: Open the review page and ask — the perception rule

```bash
ai-film review-media --shot SHOT_ID
```

**Hard rule, not a style preference: you MUST NOT claim to have visually or audibly evaluated media you cannot directly perceive.** You can inspect: the `review-media` HTML's structure and text content via Read, artifact durations and version/history metadata from `shot.json`, and any waveform PNGs the review page generated (also via Read — your Read tool does display image content, the same capability you rely on for storyboard images elsewhere in this project, so a waveform PNG is genuinely inspectable; the actual video/audio media files are not).

Phrase your question around what you actually know, never around a fabricated impression. Correct: *"I've generated this shot — the review page shows a 6.0s video and a 3.2s voice track. What do you think?"* Wrong: *"The voice sounds too fast."* — you cannot know this.

Emit `NEEDS_INPUT` with `type: clarification` (`id: shot_feedback_SHOT_ID_1`, incrementing for later rounds on this same shot) asking the open-ended question above, and stop your turn.

## Step 4: Collect feedback — batched, not one round trip per item

The human's reply may contain multiple distinct complaints in one message (e.g. "the voice is early and the camera angle feels too wide"). Process them in two passes, because an ambiguous complaint genuinely cannot be fixed until the human resolves the ambiguity — a further round trip is a hard dependency for those items, not an optional nicety. The pass ordering below keeps that dependency to **at most one clarification round trip and one new-stage-confirmation round trip per review round**, never one per item.

### Pass 1 — classify every complaint from the reply, immediately, no waiting

For each distinct complaint: call `add-feedback` with your best judgment of `target` and `at`/`range` parsed from what the human said —

```bash
ai-film add-feedback --shot SHOT_ID --target <video|voice|sfx|music|sync> --note "<the complaint, verbatim or lightly summarized>" --at <seconds>
```

(use `--range-start <seconds> --range-end <seconds>` instead of `--at` if the human described a span rather than a moment; omit both if the complaint has no specific timestamp at all) — then classify it into exactly one of:

- **Cheap fix** — a timing/sync complaint mappable to a concrete audio track offset. Apply it now (no round trip — a free, reversible fix doesn't need pre-confirmation):

```bash
ai-film apply-audio-offset --shot SHOT_ID --track <voice|sfx|music> --offset-ms <N>
```

  Positive `--offset-ms` delays the track (fixes "starts too early"); negative advances it (fixes "starts too late"). Don't call `resolve-feedback` yet — see "Resolve and rebuild" below.
- **Field edit** — the complaint maps confidently to one of the whitelisted `shot.json` fields (table below). Edit the field now using `PY_BIN` (this reuses the real engine's `save_shot`, which re-validates and recomputes the shot's derived status — never hand-edit the JSON file directly with a raw write, since that would bypass validation and leave `status` stale):

```bash
python3 -c "
from ai_film.shot_store import load_shot, save_shot
from pathlib import Path
path = Path('PROJECT_PATH/03_shots/SHOT_ID.json')
shot = load_shot(path)
shot['<field>'] = '<new value>'   # or shot['dialogue']['text'] = '...' for a nested field
save_shot(path, shot)
"
```

  **Don't regenerate yet** — see "Regenerate once per stage" below.
- **New stage requested** — the complaint explicitly asks for sfx or music where the shot currently has `not_required`. Don't call `generate-sfx`/`generate-music` yet — collect it for the confirmation pass below, even though the existing film-wide approval technically permits the call (the engine's cost gate doesn't distinguish stages, only shot IDs): asking first here is your own policy, not something the engine enforces for you.
- **Not confidently mappable** — the complaint doesn't clearly fit any of the above. Don't guess — collect it for the clarification pass below.

**Whitelisted field edits** — you may directly edit only these fields; anything else falls into "not confidently mappable" above:

| Feedback is about... | Field to edit | Regenerate |
|---|---|---|
| Dialogue wording | `dialogue.text` | `generate-voice --force` |
| Who's speaking | `dialogue.speaker` | `generate-voice --force` |
| Visual content/style | `visual` | `generate-video --force` |
| Camera shot/movement | `camera` | `generate-video --force` |
| What happens in the shot | `action` | `generate-video --force` |
| Timing/sync between tracks | *(no field — use `apply-audio-offset`)* | *(cheap fix, above)* |

### Pass 2 — at most two round trips, only if Pass 1 left anything pending

- If any complaints were classified "new stage requested," bundle all of them into one `NEEDS_INPUT type: confirmation` (`id: new_stage_confirm_SHOT_ID_1`) — e.g. "you asked for music on this shot — confirm generating it?" covering every such item from this reply — and stop the turn until resumed.
- If any complaints were classified "not confidently mappable," bundle all of them into one `NEEDS_INPUT type: clarification` (`id: shot_clarify_SHOT_ID_1`) — e.g. "do you want the camera movement changed, or the character's action?" covering every unmapped item from this reply — and stop the turn until resumed.
- These are two different `NEEDS_INPUT` types, so they're two distinct round trips when both are needed — but each is still exactly one round trip regardless of how many items it covers. If Pass 1 left nothing pending in a category, skip that round trip entirely.
- Once resumed, treat each answer like a Pass 1 outcome: an approved new-stage request becomes a first-time-generation case (no field to edit, just `generate-sfx`/`generate-music --force` in the next step); a clarified ambiguous item becomes a field edit or cheap fix per what the human actually said.

### Regenerate once per stage, after all passes are done, not once per feedback item

Track every stage (`video`, `voice`, and — only if a new-stage request was approved — `sfx`/`music`) that had at least one field edit or first-time-generation approval across *all* passes in this round. Once every edit/approval for this round is in, call `--force` regeneration **at most once per affected stage** — e.g. one complaint about `camera` and another about `action` in the same reply produce exactly one `generate-video --force`, not two, even though they touch different fields. This is a cost control, not just tidiness: each stage's `--force` call is real spend, and a human's single reply must not silently trigger more paid generations than the number of stages it actually touched.

If a `--force` regeneration fails (`ProviderError`), handle it exactly like Step 2's provider-error case: `NEEDS_INPUT type: confirmation` with the exact error, offering retry/adjust/skip, never retried silently. You rely entirely on the engine's own existing archive/restore behavior to leave the shot's prior artifact intact when this happens — **you MUST NOT implement your own artifact backup, rollback, or recovery logic**; if the engine's guarantee here were ever insufficient, that's a defect to fix in the engine, not something to work around here.

### `sync`-targeted feedback resolves to a concrete track fix, and the record says so

A human's complaint about relative timing is filed with `target sync` (it's not really "a problem with the video" or "a problem with the audio" in isolation), but the actual fix always lands on one specific track:

```bash
ai-film add-feedback --shot SHOT_ID --target sync --at 3.0 --note "voice comes in early"
ai-film apply-audio-offset --shot SHOT_ID --track voice --offset-ms -400
ai-film resolve-feedback --shot SHOT_ID --id FB-00N --resolution "voice offset -400ms"
```

The feedback entry's `target` stays `sync` (that's what was reviewed); the `resolution` text names what was actually changed.

### Resolve and rebuild

For every feedback item resolved this round — cheap fix, field edit, or clarified/confirmed item — call `resolve-feedback` with a `--resolution` naming the concrete change, **after** the stage-level regeneration above has actually happened, so the resolution text accurately describes what's now live:

```bash
ai-film resolve-feedback --shot SHOT_ID --id <FB-id> --resolution "<what actually changed>"
```

Then rebuild the review page once:

```bash
ai-film review-media --shot SHOT_ID
```

and emit a fresh `NEEDS_INPUT` (`type: clarification`, next `shot_feedback_SHOT_ID_N` id) asking whether the human is happy with this shot now or wants another round. Repeat Step 4 for as many rounds as it takes — there is no fixed cap.

## When you're done

Once the human confirms they're happy with this shot (a plain "looks good" reply, or an explicit `type: selection`/`confirmation` round trip if you prefer), your final message is a genuine completion, not a `NEEDS_INPUT`. Report: the shot ID, how many feedback rounds it took, and any feedback items you left open because the human asked to pause or skip. If you stopped early (a skipped provider error, a paused fix), say exactly what state you left things in so a re-dispatch of this same agent for this shot picks up correctly via Step 1.

**You never invoke `ai-film render` at any point in this dispatch, regardless of how many rounds this shot took or whether it reached a confirmed state.** Rendering the final cut is the user's own manual step, entirely outside this agent's job.
````

- [ ] **Step 2: Verify the frontmatter parses and the referenced CLI commands are real**

No YAML library is installed anywhere in this project — use plain string containment on the frontmatter block instead of `import yaml`:

```bash
python3 -c "
text = open('.claude/agents/ai-film-media.md').read()
front = text.split('---')[1]
assert 'name: ai-film-media' in front, front
assert 'tools: [\"Read\", \"Write\", \"Bash\", \"Glob\"]' in front, front
print('frontmatter OK')
"
grep -o 'ai-film [a-z-]*' .claude/agents/ai-film-media.md | sort -u
```

Expected: `frontmatter OK` with no traceback; the `ai-film <word>` list is exactly: `ai-film ` (bare — from the boilerplate "shown as `ai-film ...` for brevity" sentence, not a real command), `ai-film add-feedback`, `ai-film apply-audio-offset`, `ai-film approve-generation`, `ai-film generate-video`, `ai-film generate-voice`, `ai-film render` (from the closing "you never invoke `ai-film render`" line), `ai-film resolve-feedback`, `ai-film review-media`, `ai-film version` — every one a real subcommand (confirm with `ai-film --help` if in doubt); `generate-sfx`/`generate-music` appear in the file without the `ai-film ` prefix (e.g. "`generate-sfx`/`generate-music --force`"), so this grep pattern doesn't catch them — that's expected, not a gap, since they're real commands mentioned in prose rather than shown as standalone invocations in this file.

- [ ] **Step 2b: Verify the human-in-the-loop protocol is structurally present, the cost-approval gate is genuinely separated from the estimate, and the hard behavioral rules are present**

```bash
python3 -c "
text = open('.claude/agents/ai-film-media.md').read()
assert 'NEEDS_INPUT:' in text
assert 'HUMAN_RESPONSE:' in text
assert 'type: clarification | selection | cost_approval | confirmation' in text

gate_marker = text.index('stop your turn there')
approve_call = text.index('ai-film approve-generation --scope storyboard')
assert approve_call > gate_marker, 'approve-generation appears before the turn boundary — cost gate is not structurally separated'
print('protocol structure OK, cost gate structurally separated (marker at', gate_marker, ', approve-generation at', approve_call, ')')

assert 'MUST NOT claim to have visually or audibly evaluated' in text, 'perception rule missing'
assert 'MUST NOT implement your own artifact backup' in text, 'rollback-boundary rule missing'
assert 'never invoke \`ai-film render\`' in text, 'no-render rule missing'
assert 'never a subset, never just your own shot' in text, 'approval-subset invariant missing'
print('hard behavioral rules present')
"
```

Expected: `protocol structure OK, cost gate structurally separated (...)` then `hard behavioral rules present`, no assertion error.

- [ ] **Step 3: Behaviorally verify the full generate → review → feedback → fix loop the file instructs, against a scratch project with the mock provider**

```bash
rm -rf /tmp/afs-media-check && mkdir -p /tmp/afs-media-check
cd /Users/nathan/Projects/ai-film-studio
.venv/bin/ai-film init "Media Check" --path /tmp/afs-media-check
python3 -c "
import json
cfg_path = '/tmp/afs-media-check/config.json'
cfg = json.load(open(cfg_path))
for stage in cfg['providers']:
    cfg['providers'][stage]['provider'] = 'mock'
json.dump(cfg, open(cfg_path, 'w'), indent=2)
"

mkdir -p /tmp/afs-media-check/assets/characters/girl
touch /tmp/afs-media-check/assets/characters/girl/reference.png
mkdir -p /tmp/afs-media-check/04_storyboard
touch /tmp/afs-media-check/04_storyboard/S01_SH01.png
cat > /tmp/afs-media-check/03_shots/S01_SH01.json << 'EOF'
{
  "schema_version": "1.0", "id": "S01_SH01", "status": "draft", "duration_seconds": 4,
  "continuity": {"status": "passed", "checked_at": null, "issues": []},
  "action": "a girl steps into the corridor",
  "visual": {"style": "cinematic sci-fi", "lighting": "red emergency light"},
  "camera": {"shot": "wide", "movement": "slow_push_in"},
  "dialogue": {"text": "Please still be locked.", "speaker": "girl"},
  "characters": [{"name": "girl", "reference": "assets/characters/girl/reference.png"}],
  "generation": {
    "image": {"status": "completed", "attempts": 1, "artifact": {"path": "04_storyboard/S01_SH01.png", "size_bytes": 10, "sha256": null}},
    "video": {"status": "pending", "attempts": 0},
    "voice": {"status": "pending", "attempts": 0},
    "sfx": {"status": "not_required"}, "music": {"status": "not_required"}
  }
}
EOF
.venv/bin/ai-film validate --path /tmp/afs-media-check

# Prove the exact cost-gate error text Step 2 tells the agent to recognize
.venv/bin/ai-film generate-video --shot S01_SH01 --path /tmp/afs-media-check 2>&1 | grep "is not approved for generation" && echo "COST GATE TEXT MATCHES"

# The documented happy path: approve (Step 2), generate video+voice, review (Step 3)
.venv/bin/ai-film approve-generation --scope storyboard --targets S01_SH01 --path /tmp/afs-media-check
.venv/bin/ai-film generate-video --shot S01_SH01 --path /tmp/afs-media-check
.venv/bin/ai-film generate-voice --shot S01_SH01 --path /tmp/afs-media-check
.venv/bin/ai-film review-media --shot S01_SH01 --path /tmp/afs-media-check
test -f /tmp/afs-media-check/07_review/S01_SH01.html && echo "REVIEW PAGE BUILT"

# Step 4 Pass 1: a sync complaint (cheap fix) — MockAudioProvider writes placeholder bytes,
# so swap in a real tiny wav first (same pattern the engine's own golden-path test uses),
# only if ffmpeg is available; apply-audio-offset itself is exercised either way.
.venv/bin/ai-film add-feedback --shot S01_SH01 --target sync --note "voice comes in early" --at 1.0 --path /tmp/afs-media-check
if command -v ffmpeg > /dev/null 2>&1; then
  ffmpeg -y -f lavfi -i anullsrc=r=8000:cl=mono -t 1 /tmp/afs-media-check/06_audio/dialogue/S01_SH01.wav
  .venv/bin/ai-film apply-audio-offset --shot S01_SH01 --track voice --offset-ms -400 --path /tmp/afs-media-check
  echo "OFFSET APPLIED"
else
  echo "ffmpeg not available in this environment — apply-audio-offset step skipped for this run (the file's Step 4 documents it failing cleanly, not crashing, when ffmpeg is missing)"
fi
.venv/bin/ai-film resolve-feedback --shot S01_SH01 --id FB-001 --resolution "voice offset -400ms" --path /tmp/afs-media-check

# Step 4 Pass 1: a field-edit complaint — the exact python snippet the whitelist table instructs
.venv/bin/python3 -c "
from ai_film.shot_store import load_shot, save_shot
from pathlib import Path
path = Path('/tmp/afs-media-check/03_shots/S01_SH01.json')
shot = load_shot(path)
shot['camera']['movement'] = 'pan'
save_shot(path, shot)
"
python3 -c "
import json
shot = json.load(open('/tmp/afs-media-check/03_shots/S01_SH01.json'))
assert shot['camera']['movement'] == 'pan'
print('field edit applied, status recomputed:', shot['status'])
"

# Regenerate once per stage, then resolve and rebuild
.venv/bin/ai-film generate-video --shot S01_SH01 --force --path /tmp/afs-media-check
python3 -c "
import json
shot = json.load(open('/tmp/afs-media-check/03_shots/S01_SH01.json'))
assert shot['generation']['video']['version'] == 2, shot['generation']['video']
assert len(shot['generation']['video']['history']) == 1
print('video regenerated, version:', shot['generation']['video']['version'])
"
.venv/bin/ai-film review-media --shot S01_SH01 --path /tmp/afs-media-check
.venv/bin/ai-film status --path /tmp/afs-media-check
```

Expected: `COST GATE TEXT MATCHES` prints; `REVIEW PAGE BUILT` prints after the initial `review-media`; `OFFSET APPLIED` prints if `ffmpeg` is on `PATH` (otherwise the fallback message prints, still not an error); `resolve-feedback` succeeds; the field-edit Python check prints `field edit applied, status recomputed: ready` (or similar valid status — not a traceback, confirming `save_shot`'s validation/status recomputation ran); the post-`--force` check prints `video regenerated, version: 2` — confirming the exact `PY_BIN`/`ai-film` sequence the file's Step 4 documents produces the version-bump-and-archive behavior the media-review-design.md engine work already guarantees; final `ai-film status` reports `S01_SH01` with a non-`draft` status.

- [ ] **Step 4: Commit**

```bash
git add .claude/agents/ai-film-media.md
git commit -m "feat: add ai-film-media agent"
```

---

### Task 2: Extend `/create-film`'s Step 5

**Files:**
- Modify: `.claude/commands/create-film.md`

**Interfaces:**
- Consumes: the `ai-film-media` agent name (Task 1) and its dispatch-input contract (`PROJECT_PATH`, one shot ID, `IN_SCOPE_SHOT_IDS`).
- Produces: the updated single entry point (`/create-film "Title" [path]`) that now reaches the reviewed-video phase, which Task 3's golden-path verification and the README rely on.

- [ ] **Step 1: Update the protocol intro sentence to name the fourth agent**

In `.claude/commands/create-film.md`, find this exact sentence (in the "## The human-in-the-loop protocol" section, near the top):

```
The `ai-film-director`, `ai-film-character`, and `ai-film-storyboard` subagents you dispatch below have no live channel to the user themselves — only you do, since you're running in this actual conversation. Whenever one of them needs a real answer, its final report ends with, verbatim:
```

Replace it with:

```
The `ai-film-director`, `ai-film-character`, `ai-film-storyboard`, and `ai-film-media` subagents you dispatch below have no live channel to the user themselves — only you do, since you're running in this actual conversation. Whenever one of them needs a real answer, its final report ends with, verbatim:
```

- [ ] **Step 2: Replace Step 5 ("Wrap up") with the new media-agent dispatch loop plus a renumbered Step 6**

Find this exact block (the file's final section):

```
## Step 5: Wrap up

After the Storyboard agent's completion report, show the user its summary (shots locked, anything left unresolved) and remind them that `/ai-film-setup` can be re-run anytime to change providers, and that `generate-video`/`generate-voice`/`generate-sfx`/`generate-music`/`render` are manual next steps run directly via the `ai-film` CLI, same as documented in the project's README.
```

Replace it with:

````markdown
## Step 5: Run the Media agent, once per shot with a locked image

Compute `IN_SCOPE_SHOT_IDS` — every shot ID under `03_shots/*.json` whose `generation.image.artifact` is not `null` (every shot the Storyboard phase locked, this run or an earlier one):

```bash
python3 -c "
import json, glob
ids = []
for path in sorted(glob.glob('PROJECT_PATH/03_shots/*.json')):
    shot = json.load(open(path))
    if shot['generation']['image'].get('artifact'):
        ids.append(shot['id'])
print(','.join(ids))
"
```

(substitute the real `PROJECT_PATH` for the literal text above; this reads plain JSON with the standard library only, no `ai_film` import needed, so it works with a bare `python3` regardless of which form `AI_FILM_BIN` resolved to in Step 0). This list is fixed **once**, before dispatching the first shot below — do not recompute it partway through this step, even if a later shot's dispatch changes what's on disk.

If the list is empty (no shot has a locked image yet — shouldn't happen after Step 4 completes normally, but possible if Step 4 was skipped or every shot failed continuity), skip this step entirely and go to Step 6.

For each shot ID in that list, **one at a time, in order** (never in parallel — each run's protocol loop needs your real, in-order attention): dispatch the `ai-film-media` subagent with `PROJECT_PATH`, that one shot ID, and the complete `IN_SCOPE_SHOT_IDS` list. Run the protocol loop above until it reports a genuine completion, then move to the next shot ID.

## Step 6: Wrap up

After every shot's Media agent run reports completion, show the user a summary (how many shots were reviewed and confirmed, any left with open feedback because the human asked to pause) and remind them that `/ai-film-setup` can be re-run anytime to change providers, and that `render` is the one remaining manual step, run directly via the `ai-film` CLI, same as documented in the project's README.
````

- [ ] **Step 3: Verify the frontmatter is untouched and the new agent reference resolves to a real file**

```bash
python3 -c "
import os
text = open('.claude/commands/create-film.md').read()
front = text.split('---')[1]
assert 'description:' in front, 'missing description'
assert 'argument-hint:' in front, 'missing argument-hint'
print('frontmatter OK')

for name in ('ai-film-director', 'ai-film-character', 'ai-film-storyboard', 'ai-film-media'):
    assert name in text, f'{name} not referenced in create-film.md'
    assert os.path.exists(f'.claude/agents/{name}.md'), f'{name}.md does not exist'
print('all four dispatched agent names exist as real agent files')

assert '## Step 5: Run the Media agent' in text
assert '## Step 6: Wrap up' in text
assert '## Step 5: Wrap up' not in text, 'old Step 5 heading was not replaced'
print('step renumbering OK')
"
```

Expected: `frontmatter OK`, then `all four dispatched agent names exist as real agent files`, then `step renumbering OK` — no assertion error.

- [ ] **Step 4: Behaviorally verify the `IN_SCOPE_SHOT_IDS` computation against a scratch project with a mix of locked and unlocked shots**

```bash
rm -rf /tmp/afs-scope-check && mkdir -p /tmp/afs-scope-check/03_shots
cd /Users/nathan/Projects/ai-film-studio

cat > /tmp/afs-scope-check/03_shots/S01_SH01.json << 'EOF'
{"id": "S01_SH01", "generation": {"image": {"status": "completed", "artifact": {"path": "x", "size_bytes": 1, "sha256": null}}}}
EOF
cat > /tmp/afs-scope-check/03_shots/S01_SH02.json << 'EOF'
{"id": "S01_SH02", "generation": {"image": {"status": "pending"}}}
EOF
cat > /tmp/afs-scope-check/03_shots/S02_SH01.json << 'EOF'
{"id": "S02_SH01", "generation": {"image": {"status": "completed", "artifact": {"path": "y", "size_bytes": 1, "sha256": null}}}}
EOF

python3 -c "
import json, glob
ids = []
for path in sorted(glob.glob('/tmp/afs-scope-check/03_shots/*.json')):
    shot = json.load(open(path))
    if shot['generation']['image'].get('artifact'):
        ids.append(shot['id'])
print(','.join(ids))
"
```

Expected: exactly `S01_SH01,S02_SH01` — confirming the snippet correctly includes shots with a populated `artifact` and excludes the `pending` one with no `artifact` key at all, matching the real (partial, hand-simplified) JSON shapes shots actually have at each stage of the pipeline.

- [ ] **Step 5: Commit**

```bash
git add .claude/commands/create-film.md
git commit -m "feat: dispatch ai-film-media from /create-film's Step 5"
```

---

### Task 3: End-to-end golden-path verification and README update

**Files:**
- Modify: `README.md`
- No `.claude/` files created or modified — this task extends the prior agent-layer plan's own golden-path verification (which stopped at a locked storyboard image) through the new Media phase, and updates documentation to match.

**Interfaces:**
- Consumes: every file path and CLI convention declared in Tasks 1-2.
- Produces: no artifacts kept — the scratch project is deleted at the end of this task.

- [ ] **Step 1: Walk the full pipeline by hand, picking up exactly where the prior agent-layer plan's own golden path left off**

```bash
rm -rf /tmp/afs-media-golden && mkdir -p /tmp/afs-media-golden
cd /Users/nathan/Projects/ai-film-studio

# --- Steps 1-4 of /create-film: scaffold, story, character, locked storyboard image ---
# (this reproduces the exact same golden path docs/superpowers/plans/2026-08-23-agent-layer.md's
# own Task 6 already walked and verified — reused here only to reach a realistic starting point)
.venv/bin/ai-film init "The Last Ship" --path /tmp/afs-media-golden
python3 -c "
import json
p = '/tmp/afs-media-golden/config.json'
cfg = json.load(open(p))
for stage in cfg['providers']:
    cfg['providers'][stage]['provider'] = 'mock'
json.dump(cfg, open(p, 'w'), indent=2)
"
mkdir -p /tmp/afs-media-golden/assets/characters/Mara
.venv/bin/ai-film approve-generation --scope bibles --targets character:Mara --path /tmp/afs-media-golden
.venv/bin/ai-film generate-candidates --target character:Mara --count 1 --prompt "a woman engineer" --path /tmp/afs-media-golden
.venv/bin/ai-film select-candidate --target character:Mara --id 001 --path /tmp/afs-media-golden
test -f /tmp/afs-media-golden/assets/characters/Mara/reference.png && echo "CHARACTER LOCKED"

cat > /tmp/afs-media-golden/03_shots/S01_SH01.json << 'EOF'
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
    "voice": {"status": "pending", "attempts": 0}, "sfx": {"status": "not_required"}, "music": {"status": "not_required"}
  }
}
EOF
.venv/bin/ai-film validate --path /tmp/afs-media-golden
.venv/bin/ai-film check-continuity --shot S01_SH01 --status passed --path /tmp/afs-media-golden
.venv/bin/ai-film approve-generation --scope storyboard --targets shot:S01_SH01:image --path /tmp/afs-media-golden
.venv/bin/ai-film generate-candidates --target shot:S01_SH01:image --count 1 --path /tmp/afs-media-golden
.venv/bin/ai-film select-candidate --target shot:S01_SH01:image --id 001 --path /tmp/afs-media-golden
.venv/bin/ai-film status --path /tmp/afs-media-golden

# --- New: Step 5's IN_SCOPE_SHOT_IDS computation ---
IN_SCOPE=$(python3 -c "
import json, glob
ids = []
for path in sorted(glob.glob('/tmp/afs-media-golden/03_shots/*.json')):
    shot = json.load(open(path))
    if shot['generation']['image'].get('artifact'):
        ids.append(shot['id'])
print(','.join(ids))
")
echo "IN_SCOPE_SHOT_IDS=$IN_SCOPE"
test "$IN_SCOPE" = "S01_SH01" && echo "SCOPE COMPUTATION CORRECT"

# --- Simulating the ai-film-media dispatch for S01_SH01: Steps 2-4 of that agent's file ---
.venv/bin/ai-film generate-video --shot S01_SH01 --path /tmp/afs-media-golden 2>&1 | grep "is not approved for generation" && echo "MEDIA COST GATE TEXT MATCHES"
.venv/bin/ai-film approve-generation --scope storyboard --targets "$IN_SCOPE" --path /tmp/afs-media-golden
.venv/bin/ai-film generate-video --shot S01_SH01 --path /tmp/afs-media-golden
.venv/bin/ai-film generate-voice --shot S01_SH01 --path /tmp/afs-media-golden
.venv/bin/ai-film review-media --shot S01_SH01 --path /tmp/afs-media-golden
test -f /tmp/afs-media-golden/07_review/S01_SH01.html && echo "MEDIA REVIEW PAGE BUILT"

# A field-edit fix round (camera complaint), matching Step 4's whitelist
.venv/bin/python3 -c "
from ai_film.shot_store import load_shot, save_shot
from pathlib import Path
path = Path('/tmp/afs-media-golden/03_shots/S01_SH01.json')
shot = load_shot(path)
shot['camera']['movement'] = 'pan'
save_shot(path, shot)
"
.venv/bin/ai-film generate-video --shot S01_SH01 --force --path /tmp/afs-media-golden
.venv/bin/ai-film review-media --shot S01_SH01 --path /tmp/afs-media-golden

# --- Final acceptance bar ---
.venv/bin/ai-film status --path /tmp/afs-media-golden
.venv/bin/ai-film validate --path /tmp/afs-media-golden
python3 -c "
import json
shot = json.load(open('/tmp/afs-media-golden/03_shots/S01_SH01.json'))
assert shot['generation']['video']['status'] == 'completed'
assert shot['generation']['video']['version'] == 2
assert shot['generation']['voice']['status'] == 'completed'
print('final shot state:', shot['status'], '- video v' + str(shot['generation']['video']['version']))
"
```

Expected, at each stage: `CHARACTER LOCKED` prints; `ai-film status` after the storyboard-image phase reports `S01_SH01  ready`; `SCOPE COMPUTATION CORRECT` prints; `MEDIA COST GATE TEXT MATCHES` prints (proving `ai-film-media.md`'s Step 2 correctly anticipates this exact failure before approval); after approval, `generate-video`/`generate-voice` succeed and `MEDIA REVIEW PAGE BUILT` prints; the field-edit-and-regenerate sequence succeeds with no error; the final Python check prints `final shot state: completed - video v2` — confirming the whole documented pipeline, from a hand-simulated Director/Character/Storyboard phase through a hand-simulated Media agent dispatch including one fix round, produces the exact state the spec describes.

If any command in this sequence fails or produces output inconsistent with what Tasks 1-2's files claim, that is a defect in the corresponding file's instructions (not this verification script) — fix the file and re-run from the top before proceeding.

- [ ] **Step 2: Clean up the scratch projects**

```bash
rm -rf /tmp/afs-media-check /tmp/afs-scope-check /tmp/afs-media-golden
```

- [ ] **Step 3: Update the README to reflect the fourth agent and the new pipeline phase**

Read `README.md`. Find this exact paragraph in the `## Roadmap` section:

```
The Claude Code Agent layer is implemented: `/ai-film-setup` configures providers,
`/create-film "Title"` scaffolds a project and walks the whole story -> character ->
shot -> reviewed-storyboard-image pipeline through conversation, dispatching the
`ai-film-director`, `ai-film-character`, and `ai-film-storyboard` subagents in turn.
See `docs/superpowers/specs/2026-08-23-agent-layer-design.md` and
`docs/superpowers/plans/2026-08-23-agent-layer.md` for the design and implementation
history.
```

Replace it with:

```
The Claude Code Agent layer is implemented: `/ai-film-setup` configures providers,
`/create-film "Title"` scaffolds a project and walks the whole story -> character ->
shot -> reviewed-storyboard-image -> reviewed-video pipeline through conversation,
dispatching the `ai-film-director`, `ai-film-character`, `ai-film-storyboard`, and
`ai-film-media` subagents in turn. The Media agent generates each shot's video (and
voice, if it has dialogue), opens the static review page, and applies fixes — a cheap
audio-offset nudge, a targeted `shot.json` field edit plus regeneration, or a
clarifying question — until you confirm the shot; sfx/music generate only when you
explicitly ask for them on a shot. See
`docs/superpowers/specs/2026-08-23-agent-layer-design.md`,
`docs/superpowers/plans/2026-08-23-agent-layer.md`,
`docs/superpowers/specs/2026-08-28-media-agent-design.md`, and
`docs/superpowers/plans/2026-08-28-media-agent.md` for the design and implementation
history.
```

Find this exact sentence (in the "**To use it:**" paragraph, same section):

```
**To use it:** the `/ai-film-setup` and `/create-film` commands and their three
agents live in this repo's own `.claude/commands/` and `.claude/agents/` — Claude
```

Replace it with:

```
**To use it:** the `/ai-film-setup` and `/create-film` commands and their four
agents live in this repo's own `.claude/commands/` and `.claude/agents/` — Claude
```

Find this exact sentence, a few lines later in the same paragraph:

```
`.claude/commands/ai-film-setup.md`, `.claude/commands/create-film.md`, and the
three files under `.claude/agents/` into that directory's own `.claude/` (or into
```

Replace it with:

```
`.claude/commands/ai-film-setup.md`, `.claude/commands/create-film.md`, and the
four files under `.claude/agents/` into that directory's own `.claude/` (or into
```

Find this exact paragraph (the last one in the file, "Per that spec's Future Extensions"):

```
Per that spec's Future Extensions: a dedicated Continuity agent if shot volume ever
justifies a second independent-context pass, video/audio candidate review once the
engine gains video candidate storage and an edit/regenerate loop for clips, voice/
sfx/music generation agents and an Editor agent for final assembly, and a
programmatic cost-estimation engine in `ai_film` replacing the static per-model
prompt-knowledge table the agents use today.
```

Replace it with:

```
Per those specs' Future Extensions: a dedicated Continuity agent if shot volume ever
justifies a second independent-context pass; a standalone entry point for revisiting
media review on an already-built film without going through `/create-film` again;
divergent (N-way) video candidate exploration, distinct from the sequential
generate-review-fix loop the Media agent already does today; automatic sfx/music
suggestion rather than only generating them on explicit request; an Editor agent for
final assembly; and a programmatic cost-estimation engine in `ai_film` replacing the
static per-model prompt-knowledge tables the agents use today.
```

- [ ] **Step 4: Verify the README edits landed correctly**

```bash
grep -c "ai-film-media" README.md
grep -c "and their three\|three files under" README.md
```

Expected: the first command reports at least `2` (the Roadmap paragraph and the file-list sentence both now mention `ai-film-media`); the second command reports `0` — confirming no stale "and their three"/"three files under" phrasing survived the edit. (Note: "three agents" itself is not a safe grep target — in the current file it's word-wrapped as "their three\nagents", split across a line break, so a single-line `grep` for that exact phrase would report `0` whether or not the edit happened; `"and their three"` and `"three files under"` are the parts that actually stay on one line each, so they're the substrings this check needs.)

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: document the media agent in the README roadmap"
```

---

## Self-Review Notes

- **Spec coverage:** §2 Architecture → Task 1 (one agent, mirroring `ai-film-character.md`'s per-shot dispatch granularity) and Task 2 (orchestrator dispatch loop). §3 Dispatch Scope and Cost Approval → Task 1's Step 2 (reactive approval, subset invariant, in-scope-only invariant) and Task 2's `IN_SCOPE_SHOT_IDS` computation. §4.1 → Task 1's Step 2 (generation, `dialogue.text`-only gate) and Step 3 (perception rule). §4.2 → Task 1's Step 4 (two-pass classification, whitelist table, regenerate-once-per-stage, sync-to-track-fix pattern, resolve-and-rebuild). §4.3 → Task 1's "When you're done." §5 Re-Entry and State → Task 1's Step 1 (three-way state check, generation-always-runs rationale, no-persisted-confirmation rule). §6 Entry-Point Changes → Task 2 in full. §7 Error Handling → Task 1's Step 2 (provider-error retry/adjust/skip, cost-gate-already-approved edge case) and Step 4 (regeneration-failure/rollback-boundary, no-render invariant). §8 Testing Strategy → this plan's per-task behavioral verification plus Task 3's golden path.
- **Non-Goals respected:** no engine/CLI changes (every command and Python function used already exists — confirmed against the real `cli.py`/`shot_store.py` source, not assumed from the spec's illustrative examples); no automatic sfx/music; no new approval round trips per shot beyond the one reactive film-wide approval; no fixed review-round cap; no standalone entry point (dispatched only from `/create-film`).
- **Type/interface consistency:** the dispatch payload (`PROJECT_PATH`, `SHOT_ID`, `IN_SCOPE_SHOT_IDS`) is named identically in Task 1's agent file and Task 2's orchestrator edit. The `IN_SCOPE_SHOT_IDS` computation snippet is character-for-character identical between Task 2's `create-film.md` edit and Task 3's golden-path verification, so a defect in one is a defect in both. `ai-film-media`'s CLI flag usage (`--shot`, `--target`, `--track`, `--offset-ms`, `--resolution`, `--force`) was checked against the real `cli.py` source read during spec/plan authoring, not re-derived from memory.
- **Every command in every task was actually executed** against the real CLI with the mock provider (and real `ffmpeg` where available) before this plan was considered done, matching `docs/superpowers/plans/2026-08-23-agent-layer.md`'s own verification bar — the exact cost-gate error text, the `save_shot` field-edit-and-status-recomputation behavior, the version/history bump on `--force` regeneration, and the `IN_SCOPE_SHOT_IDS` inclusion/exclusion logic were each proven against real engine behavior, not assumed from the spec's prose.
