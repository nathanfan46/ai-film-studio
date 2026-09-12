# ComfyUI Workflow Interop — Design

## Goal

> `ai-film-studio` becomes an AI-native operator for ComfyUI workflows.
> ComfyUI stays the visual engine/editor; this project becomes the
> natural-language interface for understanding and modifying workflows
> built there — by anyone, including workflows this project has never
> seen before.

A user imports a ComfyUI workflow (their own, or one shared by someone
else), discusses it with an AI agent in natural language ("what does
this do", "replace the character with Mara", "remove this LoRA"), and
exports a modified workflow that ComfyUI can load and run, assuming the
required models/custom nodes are installed locally.

This is not a node editor. ComfyUI already has one.

## Non-Goals

| Item | Decision | Why |
|---|---|---|
| Drag-and-drop / visual node editor | Rejected | ComfyUI already is one. Duplicating it adds enormous surface area for zero product value. |
| Universal semantic model covering every ComfyUI node type | Rejected | A real 427-node production workflow (this spec's golden fixture is modeled on one) has ~10 "core" node types and 40+ custom-node-pack types. Cataloging every community node is an unbounded, always-behind task. See "Two-layer semantic model" below. |
| Replacing native `ai-film-studio` generation with ComfyUI | Rejected | Existing `generate-image`/`generate-video`/etc. must keep working unchanged. This is an additive, parallel capability — the user picks native generation or a ComfyUI workflow per shot, this spec doesn't make that choice for them. |
| API/prompt-format (`workflow_api.json`) as the primary interchange format | Rejected | The success criteria require exporting something ComfyUI can load and visually inspect. The API format has no layout (`pos`/`size`), so a round-tripped-through-API-format workflow would load as every node stacked at the origin — a degraded result contradicting "ComfyUI remains the visual engine when I want it." The UI **workflow** format (`nodes[]` + `links[]`, what Save/Load actually use) is the target. |
| Support for the newer v1.0 ComfyUI schema variant (object-based `links`, `state` instead of `last_node_id`/`last_link_id`) | Deferred | Verified via `docs.comfy.org` that this shape exists, but every real shared workflow checked for this spec (including the golden fixture's source) uses the legacy shape. Importing a v1.0-shaped file fails with a clear, explicit error rather than silently mis-parsing it. |
| A deterministic "what stage is this" decomposition algorithm | Rejected | See "Stage narration is the agent's job, not Python's" below — this is the single most important architectural decision in this spec. |
| Automatic parameter-level understanding of every node in a workflow (e.g. every MimicMotion/LivePortrait widget) | Rejected for V1 | Would require cataloging arbitrary custom nodes' internal widget schemas, which don't exist in the workflow JSON itself (see "The widget-addressing problem"). V1 gets targeted parameter writes only for a small, hand-curated "known node" registry; everything else is still modifiable via type-safe graph surgery (see "Three mutation primitives"). |
| Shot.json integration (a shot referencing/embedding a ComfyUI workflow) | Deferred | Genuinely useful later, but touches the shot schema, which should not be redesigned for this pass. A workflow is a standalone, shareable asset in V1, the same way a template is. |
| Static HTML visualization of the workflow graph (`07_review`) | Deferred | Explicitly optional in the originating feature request. This plan is already the largest single feature in the backlog; visualization is a natural, self-contained fast-follow once the core loop is proven. |
| Expanding Layer B pre-emptively for popular custom-node families (a full MimicMotion/LivePortrait parameter map) | Rejected | Layer B stays small and hand-curated, growing one node type at a time only when a real user request needs parameter-level access to it — not predicted in advance. The internal widget schemas for arbitrary custom nodes aren't reliably available from the workflow JSON alone (see "The widget-addressing problem"), so speculative registry entries would be guesses, not verified facts, violating this project's own standing practice of never encoding an unverified assumption as fact. |
| A general multi-mutation transaction/rollback engine | Deferred | Real value once natural-language requests routinely span several edits, but V1 gets there more cheaply: each *individual* mutation is atomic (validated in memory before anything is written to disk), and a multi-step request executes as a sequence of atomic steps with a mutation log the agent uses to report exactly what succeeded before a failure. See "Atomicity and the mutation log" below. |

## Real-world grounding

Before any of the design below was written, a real ~427-node production
ComfyUI workflow was inspected directly (a face/body reanimation
pipeline: source video → depth+segmentation-guided SD1.5 character
regeneration → MimicMotion body-motion transfer → LivePortrait facial
retargeting → ReActor face-swap → AnimateDiff-based temporal refinement
→ RIFE frame interpolation → video export). This is the golden-fixture
source (genericized — see "Golden fixture" below, no real video
title/URL/personal notes are ever committed).

What that inspection actually found, verified from the raw file, not
assumed:

1. **Only ~10 of 427 nodes are "core" ComfyUI types** (`CheckpointLoaderSimple`,
   `CLIPTextEncode`, `KSampler`, `VAEDecode`/`VAEEncode`, `EmptyLatentImage`,
   `LoraLoaderModelOnly`). Everything else belongs to custom-node packs
   (VideoHelperSuite, AnimateDiff-Evolved, LivePortrait, MimicMotion,
   ReActor, segment-anything, RIFE, Crystools generic pipe/bundler nodes,
   core `Reroute`, `Note`). For any workflow a real user actually has,
   "handle nodes we don't have a full parameter map for" **is** the
   feature, not an edge case.
2. **`widgets_values` is not always a positional array.** Several
   VideoHelperSuite nodes (`VHS_LoadVideo`, `VHS_SelectImages`,
   `VHS_VideoCombine`) store it as a **named dict** instead
   (`{"video": "...", "force_rate": 30, ...}`). Any known-node registry
   entry must declare which addressing mode that node type uses.
3. **A widget can be "converted to an input socket."** `EmptyLatentImage`'s
   `width`/`height` appear as *both* a `widgets_values` slot and an
   `inputs[]` entry tagged `"widget": {"name": "width"}` with a real
   `link`. When a widget is converted, its `widgets_values` array
   position shifts (the array only contains the *remaining*
   non-converted widgets, in the node type's declared order). A
   semantic write that assumes a fixed array index is wrong whenever a
   user has converted an earlier widget on that same node instance. See
   "The widget-addressing problem" below for the resolution algorithm.
4. **`size`/`pos` appear in two shapes even within one file**
   (`[w, h]` arrays vs. `{"0": w, "1": h}` objects) — harmless, since
   this feature never touches those fields, but it confirms: parse
   loosely, preserve byte-faithfully, mutate only explicitly named
   fields. Never re-normalize structure you don't need to touch.
5. **Heavy use of passthrough/bundler nodes** (core `Reroute`, and
   custom generic multi-slot bundlers like Crystools' "Pipe from/to
   any") to route connections across a large canvas. Tracing "what
   actually feeds this input" one hop at a time is often useless.

## Two-layer semantic model

Every node gets classified by **two independent layers**, computed as
read-only views over the same parsed dict — there is no separate,
persisted "semantic model" object. See "Internal representation" below
for why.

**Layer A — role inference (broad, cheap, applies to every node).**
A coarse pipeline-role tag, via, in order:

1. Exact match against the small known-node registry (Layer B; see
   below) — these nodes get a precise role for free.
2. A **type-name substring table** for well-known custom-node
   *families*, matched by substring containment anywhere in the type
   string (case-sensitive) — not a prefix match. This distinction is
   verified necessary, not stylistic: `DownloadAndLoadLivePortraitModels`
   and `DownloadAndLoadMimicMotionModel` both contain their family name
   but don't start with it, so a prefix match would silently miss both
   loader nodes. Substring matching is what lets `LivePortraitCropper`,
   `LivePortraitRetargeting`, `LivePortraitComposite`, *and*
   `DownloadAndLoadLivePortraitModels` all read as one
   `facial_performance_transfer` stage. Initial table (extend over
   time, never exhaustively):

   | Type name contains | Role |
   |---|---|
   | `MimicMotion` | `body_motion_transfer` |
   | `LivePortrait` | `facial_performance_transfer` |
   | `ADE_`, `AnimateDiff` | `temporal_consistency` |
   | `ReActor`, `FaceSwap` | `identity_stabilization` |
   | `RIFE`, `VFI`, `Interpolat` | `frame_interpolation` |
   | `SAM`, `GroundingDino`, `Segment`, `Mask` | `segmentation` |
   | `ControlNet` | `control_guidance` |
   | `VHS_LoadVideo`, `VHS_VideoCombine` | `video_io` |
   | `Reroute` (exact type match) | `structural` (never a stage; always resolved through) |
   | `Note` (exact type match) | `comment` (surfaced separately, never a stage) |

3. No match → role `null`. **Not hidden** — the node's type, inputs,
   outputs, and resolved links are still fully visible in
   `list-workflow-nodes`; it's simply unlabeled.

Known, accepted V1 limitation: short substrings (e.g. `SAM`, `Mask`)
carry some false-positive collision risk against an unrelated node type
that happens to contain the same letters. Case-sensitive matching
mitigates this somewhat; the table is expected to be extended as real
collisions are found, not designed to be collision-proof up front — a
misclassified Layer A role only affects the human-facing description,
never a mutation's safety, since mutations are gated by Layer B's exact
registry or by socket-type checks, neither of which depend on Layer A.

**Layer B — parameter-level semantic read/write (narrow, hand-curated,
~10-15 types for V1: `CheckpointLoaderSimple`, `CLIPTextEncode`,
`KSampler`/`KSamplerAdvanced`, `VAEEncode`/`VAEDecode`,
`EmptyLatentImage`, `LoadImage`, `SaveImage`, `LoraLoader`/
`LoraLoaderModelOnly`).** Each registry entry declares, per semantic
field name: whether it lives in `widgets_values` (array-indexed or
dict-keyed — per finding #2 above) or can be exposed as a converted
input, and the node type's full declared widget order (needed for the
position-resolution algorithm below). This is the **only** thing that
enables `set-workflow-field` — everything outside this registry is
still modifiable, just through the type-safe graph-surgery primitives
instead of a named field (see "Three mutation primitives").

## The widget-addressing problem

Given finding #3 above, resolving "the value of semantic field X on
node N" for a Layer-B node is **not** a fixed array index. The
algorithm:

1. Look up node N's declared widget order in the registry (e.g.
   `KSampler`: `["seed", "control_after_generate", "steps", "cfg",
   "sampler_name", "scheduler", "denoise"]`).
2. Check N's `inputs[]` for an entry with `"widget": {"name": X}` and a
   non-null `link`. If found, X has been converted to a socket — the
   correct edit is `rewire-workflow-link` (point that input at a
   different source), not a `widgets_values` write. Stop here.
3. Otherwise, filter the registry's declared widget order down to only
   the fields *not* found as a converted-and-linked input on this node
   instance, preserving declared order. X's position in the *filtered*
   list is its index into `widgets_values` (array-addressed types) or
   its name is used directly (dict-addressed types).
4. **Never guess past this point.** Before trusting the computed
   position, sanity-check it against the actual instance: for
   array-addressed types, the filtered widget-order list's length must
   equal `len(widgets_values)` on this node; for dict-addressed types,
   every filtered field name must actually be a key present in the
   dict. If either check fails — the registry's assumed widget schema
   doesn't match what this specific node instance actually contains,
   which can happen as custom nodes and even core ComfyUI nodes evolve
   their widget layout across versions — `set-workflow-field` returns a
   clear `schema mismatch` error naming the node, field, and the
   expected vs. actual shape, rather than writing to a position that
   might be wrong. The caller (agent or human) then chooses
   `set-workflow-raw` with an explicitly named index/key, accepting the
   risk deliberately instead of it being silently taken for them.

This is bounded and tractable precisely because Layer B is small and
hand-curated — this would not scale to a "know every node" registry,
which is exactly why Layer A exists as the separate, broad mechanism.

## Stage narration is the agent's job, not Python's

The tool's job stops at making the graph **legible**: role tags, resolved
adjacency (with `Reroute` transparently skipped — no other passthrough
type is resolved through in V1; Crystools-style generic bundlers stay
opaque), and a flat list of `Note` node text (verbatim, **not**
auto-correlated to a specific stage by spatial-proximity heuristics —
canvas layout is arbitrary and this kind of correlation is exactly the
fuzzy judgment call the agent layer exists for).

Every resolved connection in `list-workflow-nodes`'s output carries an
explicit `"resolution"` tag: `"direct"` when the source was reached
directly or through only `Reroute` hops, or `"opaque_passthrough"` when
resolution stopped at an unresolvable bundler node. This distinction
must be visible, not silently absorbed — an agent that sees
`KSampler.model ← (opaque_passthrough) ← node 271` should reason "there
is a connection here I can't see through," never "this input has
nothing feeding it."

Deciding "this is a singing-reanimation pipeline, MimicMotion handles
body motion and LivePortrait handles facial expression, per this note
the eyes are deliberately tuned to prefer MimicMotion's" is reasoning
over that structure, done fresh by the agent every time it's asked —
never a hardcoded decomposition algorithm. This keeps the Python side
from turning into an ever-growing pile of "workflow interpretation
heuristics," and puts the interpretive work where every other feature
in this codebase already puts it:

```
Python                          Agent
──────────────────────────────  ─────────────────────────────
extract graph                   understand intent
resolve links (Reroute-aware)   describe pipeline / decide stages
identify role families          decide what to change
preserve unknown nodes    ───▶
validate mutations
                          ◀───  (primitive calls: rewire / remove / set)
perform graph surgery
validate
export
```

## Internal representation

The imported workflow's **original parsed dict is the only
representation** — there is no separate, lossy "semantic model" object
converted to and from. Every "semantic" operation (`describe-workflow`,
`list-workflow-nodes`, role inference, link resolution) is a read-only
function over that same dict. Every mutation (`set-workflow-field`,
`set-workflow-raw`, `rewire-workflow-link`, `remove-workflow-node`) is a
narrow, targeted in-place field edit on that same dict, followed by
`validate_workflow`. Export writes that same dict back out.

This is what makes "preserve unknown nodes," "preserve node IDs," and
"preserve custom node data" true for free, rather than properties a
converter has to be separately engineered to uphold. A no-op
import→export is **structurally equivalent and preserves all workflow
data** — same nodes, same ids, same types, same links, same
`widgets_values`, same unknown/custom fields — not byte-equivalent: the
only thing that changes is JSON formatting (`json.dumps(..., indent=2)`,
this repo's existing convention). Testing verifies the structural claim
directly (see "Testing" below), which is the defensible guarantee — byte
equality would additionally require preserving the original file's exact
whitespace/key-ordering characteristics, which is not worth the
complexity for a real, achievable win of the same practical value.

## Atomicity and the mutation log

Two related risks the mutation architecture must not leave open:

**Within one mutation.** Every mutation command (`set-workflow-field`,
`set-workflow-raw`, `rewire-workflow-link`, `remove-workflow-node`)
loads the stored workflow, applies its one edit **in memory**, runs
`validate_workflow`, and only writes to
`workflows/<id>/workflow.json` if there are no errors. On
failure, the command exits non-zero and the file on disk is untouched —
every single mutation call is atomic by construction, not by a
separate rollback step.

**Across several mutations for one request.** A natural-language ask
like "remove the LoRA, change the character to Mara, and switch the
checkpoint" is three separate primitive calls. A full transaction
(stage all three, validate once, commit atomically or not at all) is
real future value but unnecessary complexity for V1 — instead, each
call is atomic individually and applied sequentially, and every
mutation call appends one entry to a workflow-scoped log via the
existing `write_attempt_log` mechanism already used elsewhere in this
codebase (e.g. `candidate_service.py`'s selection provenance logging).
Since a workflow isn't project-scoped (see "Storage" below), the log
lives inside the workflow's own self-contained directory —
`write_attempt_log`'s first parameter is just a base directory it
doesn't otherwise interpret, so calling it with
`workflows/<workflow_id>/` as that base and a fixed group name produces
`workflows/<workflow_id>/99_logs/mutations/<timestamp>_<primitive>_attempt01.json`
— which primitive ran, its arguments, and a before/after summary, kept
right next to that workflow's own `workflow.json` rather than under an
ambiguous shared top-level log directory. If step 2 of 3 fails, steps 1 and 2's log entries
already show exactly what's applied — the agent reads this log to
report precisely what succeeded before reporting the failure and asking
the human how to proceed (undo via more conversation, or continue from
the partial state), rather than the human discovering an inconsistent
workflow later with no explanation. This is deliberately simpler than a
transaction engine and is upgraded to one only if real usage shows the
sequential-with-reporting approach isn't enough.

## `validate_workflow`

```python
def validate_workflow(workflow: dict) -> dict:
    # returns {"errors": list[str], "warnings": list[str]}
```

Mirrors the existing `validate_shot`/`validate_template` naming
convention, extended with a severity split (new to this codebase,
justified by a real need: community workflows are messy, and treating
every anomaly as fatal would make this feature useless on exactly the
workflows it needs to handle). Two named levels for V1; a third is
explicitly identified and explicitly deferred:

**Level 1 — graph integrity (errors, block export/mutation).** A link
id referenced by a node's `inputs[].link` that doesn't exist in the
top-level `links` array (or vice versa); a link whose `target_id`/
`target_slot` doesn't match the node it claims to target; a rewire that
connects incompatible socket `type` strings (types are already declared
on every socket, known node or not — this check needs zero
node-specific knowledge).

**Level 2 — known-node semantic validation (errors, Layer B fields
only).** Part of `validate_workflow`'s normal whole-graph pass, exactly
like Level 1 — not a check gated to the moment of a
`set-workflow-field` call. Every node matching a Layer B type has its
known fields checked against that field's declared shape in the
registry (e.g. `steps` must parse as a positive integer, `sampler_name`
must be a non-empty string) — a basic type/shape sanity check using data
the registry already carries for addressing, not a reimplementation of
ComfyUI's own per-node validation. This means a bad value is caught the
same way whether it was just written by `set-workflow-field` or was
already present in the imported file. It only ever applies to the small
set of fields Layer B actually knows about; it says nothing about nodes
outside the registry.

**Level 3 — environment validation (deferred, not V1).** Whether
required custom nodes or models are actually installed and available at
generation time. The success criteria already state that running the
exported workflow assumes those are installed — V1 does not attempt to
verify it.

**Warnings** (surfaced, never blocking): a node type matching neither
Layer A nor Layer B (fully unclassified); a node identified as a
generic passthrough/bundler that can't be resolved through (e.g.
Crystools' pipe nodes); anything else structurally sound but outside
this tool's understanding.

Run automatically after every mutation (before that mutation's write —
see "Atomicity and the mutation log" above) and standalone before every
export.

## Three mutation primitives

A key realization from working through the original feature request's
own example modifications: most of them are **graph surgery**, which
only needs socket **types** (present on every socket, known or not) —
not node-specific parameter knowledge:

| Example request | Primitive | Needs Layer B? |
|---|---|---|
| "Use a different image model" | `rewire-workflow-link` | No |
| "Remove this LoRA" | `remove-workflow-node --bypass` | No |
| "Keep the motion, swap the model" | `rewire-workflow-link` | No |
| "Change the character to Mara" | `set-workflow-field` (prompt/reference) | Yes |
| "Change camera to close-up" | `set-workflow-field` (prompt text) | Yes |

So: **`rewire-workflow-link`** (point a target node's named input at a
different source node's named output, rejected by `validate_workflow`
if the socket types don't match) and **`remove-workflow-node`**
(optionally `--bypass`: only auto-reconnects when there's one
unambiguous type-matching input/output pair on the removed node — e.g.
`LoraLoaderModelOnly`'s `model` in → `model` out; otherwise the node and
its direct links are simply removed, leaving a gap the agent must
mention) work on **any** node, known or not. **`set-workflow-field`**
(Layer B only, clear error naming the escape hatch if the node isn't
registered, or the schema-mismatch error described above) and
**`set-workflow-raw`** (explicit index/key, for anything outside the
registry, clearly labeled as unsafe/best-effort) cover the smaller set
of true parameter edits.

**`set-workflow-raw` requires human confirmation before use.** This is
a hard rule enforced at the agent-instruction level (mirroring how this
project already gates real spend behind an explicit
`NEEDS_INPUT`/`HUMAN_RESPONSE` cost-approval round trip before
`generate-candidates`/`edit-candidate` run) — the agent must never call
it silently. It states plainly which node, which raw index or key, and
why (the node isn't in the known registry, or the registry's expected
shape didn't match this instance), and proceeds only after the human
says yes. `set-workflow-field`, `rewire-workflow-link`, and
`remove-workflow-node` need no such gate — their safety comes from
`validate_workflow`, not from asking permission first.

**Node identity is contextual, not merely id-based.** A workflow can
contain several structurally-identical node families (e.g. two
independent `LivePortraitCropper → LivePortraitRetargeting →
LivePortraitComposite` chains processing two different source clips).
A bare node id means nothing to a human ("change node 241"), and the
agent must not treat id as sufficient context for identifying which
chain a request like "change the face settings on the second one"
refers to. `list-workflow-nodes` always includes each node's resolved neighbors
(per the `direct`/`opaque_passthrough` tagging above), precisely so the
agent can disambiguate using role + neighbors + upstream/downstream
source — cross-referenced, at the agent's own discretion, against the
flat notes list `describe-workflow` already surfaces — the same way a
human would trace wires on the canvas. Never by id alone.

## Storage

`workflows/<workflow_id>/workflow.json` — a CWD-relative top-level
directory, exactly like `templates/` (`DEFAULT_TEMPLATES_PATH` in
`cli.py`), not `assets/workflows/`: `assets/` is always inside a
specific project (one of `project.py`'s `PROJECT_DIRS`), so nesting
under it would make workflows project-scoped by construction — the
opposite of the intent. **Not project-scoped** (no `--path`; a
`--workflows-dir` override exists, mirroring `--templates-dir`): a
workflow is a reusable, shareable asset, not tied to one project's
shots. `<workflow_id>` is validated against the same
`^[a-z0-9][a-z0-9-]*$` pattern already used for template ids in
`schema.py` — reusing that constraint closes the same path-traversal
class of bug the template feature had to fix, instead of reintroducing
it.

## CLI commands

All in `src/ai_film/cli.py`, none project-scoped:

- `import-workflow <file> --id <id>` — parse, reject clearly on
  API-format or the rare v1.0 schema shape, run `validate_workflow`
  (warnings OK, errors block), copy into `workflows/<id>/`.
- `describe-workflow --id <id>` — compressed, human-facing summary:
  role-tagged nodes grouped by role, unlabeled nodes grouped by
  type+count (never enumerated individually for a large graph), notes
  listed verbatim.
- `list-workflow-nodes --id <id> [--role <role>]` — full, uncompressed
  per-node detail (id/type/role/resolved inputs+outputs), the substrate
  the agent reasons over to find exact targets.
- `set-workflow-field --id <id> --node <n> --field <f> --value <v>`
- `set-workflow-raw --id <id> --node <n> (--index <i> | --key <k>) --value <v>`
- `rewire-workflow-link --id <id> --target-node <n> --target-input <name> --source-node <n> --source-output <name>`
- `remove-workflow-node --id <id> --node <n> [--bypass]`
- `validate-workflow --id <id>` — standalone; prints warnings always,
  errors exit 1.
- `export-workflow --id <id> --out <path>` — validates (errors block),
  writes.

## Agent integration

New `.claude/agents/ai-film-workflow.md`, dispatched by a new
`/import-workflow <file>` command — optional and user-invoked, **not**
part of `/create-film`'s automatic pipeline (importing a workflow is a
deliberate, occasional action, the same way `/analyze-reference-video`
is). Flow: `import-workflow` → `describe-workflow` → present the
agent's own narrated understanding to the human → loop on natural-
language change requests via the same `NEEDS_INPUT`/`HUMAN_RESPONSE`
protocol every other agent in this project already uses, picking
whichever primitive makes the smallest targeted change (using contextual
node identification, not bare ids, when a workflow has repeated
structurally-similar families) → re-describe/confirm → repeat until the
human says export → `export-workflow`.

**Hard rule: the agent only ever calls CLI primitives over Bash, never
edits `workflow.json` directly.** Enforced at the tool-permission level,
not just by instruction: this agent's tool list is **Read, Bash, Glob —
deliberately no Write**. Every mutation, however small, goes through
`set-workflow-field`/`set-workflow-raw`/`rewire-workflow-link`/
`remove-workflow-node`, so every mutation is validated and logged (see
"Atomicity and the mutation log") no matter which agent turn produced
it. This is the same reasoning that already led this project to have
Director/Storyboard read `templates/<id>/template.json` directly rather
than inventing Bash access they don't have — here it runs the other
direction: the capability exists, and is deliberately withheld, so that
"fuzzy LLM judgment" and "proven-safe graph mutation" stay on opposite
sides of a hard, tool-enforced boundary rather than a boundary that only
holds as long as the agent's instructions are followed correctly.

## Golden fixture

A genericized, structurally-equivalent fixture built from the real
427-node workflow inspected for this spec — **not** the real file
verbatim. Preserves every hard case found above (≈40 nodes, `Reroute`
chains, dict-shaped `widgets_values`, a converted-widget-to-input node,
`MimicMotion*`/`LivePortrait*`/`ReActor*`-family type names, VHS video
I/O nodes, a couple of genuinely unrecognized custom types, `Note`
nodes, realistic cross-stage connections) with synthetic prompt text,
node content, and note text — no real video title, URL, or personal
note text is ever committed. Additionally includes **two independent,
structurally-identical role-family chains** (e.g. two separate
`LivePortraitCropper → LivePortraitRetargeting → LivePortraitComposite`
sequences fed by two different upstream sources) specifically to
exercise contextual node identification — a test that asserts the tool
can distinguish "the LivePortrait chain fed by node X" from "the one fed
by node Y" using resolved neighbors, not node id. A small ~6-node
fixture (Checkpoint→CLIP→KSampler→VAE) also exists for fast, focused
unit tests.

Before this feature is considered done, `import-workflow`/
`describe-workflow` should also be manually smoke-tested against a
handful of additional real, non-fixture community workflows (downloaded
separately, never committed to the repo) — the golden fixture is
thorough but is still one graph shape; real-world variety is the actual
target.

## Testing

- Round-trip: both fixtures, no-op import→export is structurally
  identical (same node set/ids/types/links/widgets_values); a modified
  export leaves every untouched node byte-identical.
- Each mutation primitive: prompt-text change, reference-image swap,
  LoRA removal with bypass, link rewire swapping a loader, a raw
  index/key edit — each checked for minimal unrelated change.
- Unknown-node mutation: removing an unrecognized/unclassified node
  succeeds; `--bypass` on one only auto-reconnects when its in/out
  socket types are unambiguous, otherwise leaves a reported gap, same
  as a known node — bypass eligibility depends on socket types, which
  every node declares, not on Layer A/B classification.
- Widget-schema mismatch: a Layer B node whose actual instance doesn't
  match its registered widget schema (simulating a version drift)
  returns the explicit `schema mismatch` error, never a silently wrong
  write.
- `set-workflow-raw` without a prior confirmation step is exercised at
  the agent-instruction level, not the CLI level — the CLI command
  itself has no concept of "confirmed," by design (see "Three mutation
  primitives"); this is a documentation/agent-doc concern, not a
  Python test.
- Atomicity: a mutation that fails `validate_workflow` leaves the
  stored `workflow.json` byte-identical to before the call; a
  successful mutation's log entry appears at
  `workflows/<id>/99_logs/mutations/`.
- `validate_workflow`: dangling link and type-mismatched rewire are
  Level 1 errors and block; an out-of-range value on a Level 2 Layer B
  field is rejected; unknown node type and unresolvable bundler node
  are warnings and never block.
- `describe-workflow`/`list-workflow-nodes` against the golden fixture:
  correct role grouping (including multi-node families reading as one
  stage), correct type+count compression in the summary view vs. full
  detail in the list view, notes surfaced verbatim, `resolution: direct`
  vs. `resolution: opaque_passthrough` tagged correctly across a
  `Reroute` chain vs. a Crystools-style bundler, and the two-duplicate-
  family fixture case correctly distinguishable via resolved neighbors.
- Full CLI wiring for all 9 commands.
- Existing test suite stays green throughout — this feature touches no
  existing module's behavior.

## Success criteria (unchanged from the originating request)

A user can: receive a workflow from someone else → import it →
ask what it does → ask for a natural-language change → have unrelated
parts of the workflow preserved → export → load the result in ComfyUI
→ inspect the actual node graph → run it successfully, assuming
required models/custom nodes are installed.
