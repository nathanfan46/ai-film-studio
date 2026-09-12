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

This is what makes "preserve unknown nodes," "preserve node IDs,"
"preserve custom node data," and "a no-op import→export is
byte-equivalent" true for free, rather than properties a converter has
to be separately engineered to uphold. The only thing that changes on
export is JSON formatting (`json.dumps(..., indent=2)`, this
repo's existing convention) — matching this spec's own "semantic
equivalence, not byte equality" round-trip requirement.

## `validate_workflow`

```python
def validate_workflow(workflow: dict) -> dict:
    # returns {"errors": list[str], "warnings": list[str]}
```

Mirrors the existing `validate_shot`/`validate_template` naming
convention, extended with a severity split (new to this codebase,
justified by a real need: community workflows are messy, and treating
every anomaly as fatal would make this feature useless on exactly the
workflows it needs to handle).

**Errors** (block export/mutation): a link id referenced by a node's
`inputs[].link` that doesn't exist in the top-level `links` array (or
vice versa); a link whose `target_id`/`target_slot` doesn't match the
node it claims to target; a rewire that connects incompatible socket
`type` strings (types are already declared on every socket, known node
or not — this check needs zero node-specific knowledge).

**Warnings** (surfaced, never blocking): a node type matching neither
Layer A nor Layer B (fully unclassified); a node identified as a
generic passthrough/bundler that can't be resolved through (e.g.
Crystools' pipe nodes); anything else structurally sound but outside
this tool's understanding.

Run automatically after every mutation and before every export.

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
registered) and **`set-workflow-raw`** (explicit index/key, for
anything outside the registry, clearly labeled as unsafe/best-effort)
cover the smaller set of true parameter edits.

## Storage

`assets/workflows/<workflow_id>/workflow.json` — mirrors the
character/environment asset layout. **Not project-scoped** (no
`--path`), same convention as the Template Library: a workflow is a
reusable, shareable asset, not tied to one project's shots.
`<workflow_id>` is validated against the same `^[a-z0-9][a-z0-9-]*$`
pattern already used for template ids in `schema.py` — reusing that
constraint closes the same path-traversal class of bug the template
feature had to fix, instead of reintroducing it.

## CLI commands

All in `src/ai_film/cli.py`, none project-scoped:

- `import-workflow <file> --id <id>` — parse, reject clearly on
  API-format or the rare v1.0 schema shape, run `validate_workflow`
  (warnings OK, errors block), copy into `assets/workflows/<id>/`.
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
whichever primitive makes the smallest targeted change → re-describe/
confirm → repeat until the human says export → `export-workflow`.

## Golden fixture

A genericized, structurally-equivalent fixture built from the real
427-node workflow inspected for this spec — **not** the real file
verbatim. Preserves every hard case found above (≈40 nodes, `Reroute`
chains, dict-shaped `widgets_values`, a converted-widget-to-input node,
`MimicMotion*`/`LivePortrait*`/`ReActor*`-family type names, VHS video
I/O nodes, a couple of genuinely unrecognized custom types, `Note`
nodes, realistic cross-stage connections) with synthetic prompt text,
node content, and note text — no real video title, URL, or personal
note text is ever committed. A small ~6-node fixture
(Checkpoint→CLIP→KSampler→VAE) also exists for fast, focused unit
tests.

## Testing

- Round-trip: both fixtures, no-op import→export is structurally
  identical (same node set/ids/types/links/widgets_values); a modified
  export leaves every untouched node byte-identical.
- Each mutation primitive: prompt-text change, reference-image swap,
  LoRA removal with bypass, link rewire swapping a loader, a raw
  index/key edit — each checked for minimal unrelated change.
- `validate_workflow`: dangling link and type-mismatched rewire are
  errors and block; unknown node type and unresolvable bundler node are
  warnings and never block.
- `describe-workflow`/`list-workflow-nodes` against the golden fixture:
  correct role grouping (including multi-node families reading as one
  stage), correct type+count compression in the summary view vs. full
  detail in the list view, notes surfaced verbatim.
- Full CLI wiring for all 9 commands.
- Existing test suite stays green throughout — this feature touches no
  existing module's behavior.

## Success criteria (unchanged from the originating request)

A user can: receive a workflow from someone else → import it →
ask what it does → ask for a natural-language change → have unrelated
parts of the workflow preserved → export → load the result in ComfyUI
→ inspect the actual node graph → run it successfully, assuming
required models/custom nodes are installed.
