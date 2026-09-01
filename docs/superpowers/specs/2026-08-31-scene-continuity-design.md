# Scene Continuity (Spatial Canon) — Design

## Problem

`shot.json` describes what one shot contains. It has no concept of what stays
true *across* a scene's shots — in particular, which side of frame each
character occupies and which way they face. Nothing stops two shots in the
same scene from independently, plausibly describing a character on opposite
sides: each shot looks fine in isolation, but cut together the character
appears to teleport across the 180-degree line. This was observed directly
on `one-more-life`'s Scene 1 (Doctor's screen side flipped between shots).

The existing `characters[].reference`/`environment.reference` mechanism (and
its recent fix — reference-conditioned image generation was silently
unconditioned until routed to fal's `/edit` endpoint) locks *appearance*.
Nothing locks *blocking*. The existing `previous_shot_image_reference` chain
(`shot_store.py`) anchors each shot only to the one immediately before it —
drift introduced anywhere in the chain compounds forward with no fixed point
to correct against.

## Non-goals (explicitly out of scope for this design)

- Computer-vision detection of character position from a generated image.
- 3D world-coordinate inference or a spatial/physics engine.
- Automatic blocking correction (regenerating a shot because it "looks
  wrong" spatially) — all judgment here stays LLM/text/reference-image
  based, same as the rest of this project's continuity checking.
- A relational graph form (`{subject, relation, object}` triples) for
  arbitrary N-character choreography. This project's real scenes cap at 2
  on-screen characters; a flat per-character map is sufficient and renders
  into clearer prompt text than triples would. If a future scene genuinely
  needs 3+-character choreography, the relational form is a natural
  addition alongside (not replacing) the semantic map below — not built now.

## Model

```
shot.json              — what happens in THIS shot
SC<NN>.continuity.json  — what remains true ACROSS this scene (this design)
SC<NN> master image     — what that spatial world visually looks like
<id>.feedback.json      — what the human said was wrong
```

Agent responsibilities:

```
Storyboard  — establishes scene canon, generates shots against it,
              can declare explicit transitions
Continuity  — validates shots against canonical state
Media       — inherits already-correct blocking from the locked image;
              no changes needed there
```

## Data model

New module: `src/ai_film/scene_continuity.py`. Same weight class as
`feedback_store.py` — plain JSON, atomic write (`tempfile` + `os.replace`),
inline constant-based validation, **no jsonschema**. This is agent-authored,
agent-consumed data, not an engine-validated pipeline artifact like
`shot.json`.

**Storage:** `02_scenes/SC<NN>.continuity.json`, sibling to `SC<NN>.md`.
Absence is a valid, non-error state (see "Absent/partial canon" below).

**Shape:**

```json
{
  "master_shot": "S01_SH01",
  "master_reference_image": "02_scenes/SC01_master_reference.png",
  "spatial": {
    "Mara Voss": {"screen_side": "left", "facing": "right"},
    "Doctor": {"screen_side": "right", "facing": "left"}
  },
  "transitions": [
    {
      "after_shot": "S01_SH03",
      "changes": {"Mara Voss": {"screen_side": "right", "facing": "left"}},
      "reason": "Mara walks around the Doctor to reach the door."
    }
  ]
}
```

- `master_shot`: the scene's anchor shot id, for traceability. Set once, via
  `set-scene-continuity`, before that shot's image exists (see "Master-shot
  lifecycle").
- `master_reference_image`: a frozen snapshot path, populated only by
  `lock-continuity-master`, absent until that command has run. This is what
  generation actually reads — never resolved live from the shot's current
  artifact (see "Master-shot lifecycle" for why).
- `spatial`: each on-screen character's *initial* state, decided when the
  scene's first shot is authored. `screen_side ∈ {left, center, right}`.
  `facing ∈ {left, right, camera, away}`. Validated inline against these
  constant tuples; an unknown value is a `ValueError`, same style as
  `shot_store.save_shot`'s validation failures. **No enforced maximum
  character count** — today's real scenes have 2 on-screen characters, but
  that's a fact about the current story, not a schema constraint; a
  3rd/4th character key works the same way, just without any relational
  semantics between them (the documented, not-yet-built extension point).
- `transitions`: the *only* legitimate way the canon changes after initial
  authoring. Each entry's `changes` maps a subset of characters to their
  **complete** new `{screen_side, facing}` pair — never a partial single-field
  patch. A character not mentioned in `changes` is unaffected by that
  transition.

### Effective state and the transition boundary

```python
def effective_spatial_state(continuity: dict, shot_id: str) -> dict[str, dict]:
    """spatial, folded with every transition whose after_shot sorts
    strictly before shot_id (scene-relative shot number comparison, not
    lexical/list order — reuses the existing S<SS>_SH<NN> parsing in
    shot_store.py, so S01_SH09 sorts before S01_SH10 correctly)."""
```

Algorithm, made explicit (this was the one genuinely ambiguous point in
review):

```
effective(shot_id) = spatial
                    + every transition where after_shot < shot_id
                      (applied in after_shot order, not list order)
```

A transition declared `after_shot: S01_SH03` is **not yet true at SH03
itself** — it becomes true starting at SH04:

```
SH03 → old state (transition not yet applied)
SH04 → new state (transition applied)
```

Transitions are sorted by scene-relative shot number before folding, so
list-authoring order in the JSON file doesn't matter for correctness (though
the agent should still append them in chronological order for readability).
Each character's dict is replaced wholesale by a later transition's entry
for that character (`dict.update()` per character key) — never merged field
by field.

### Absent / partial canon — explicitly backward-compatible

```
continuity file absent
  → no spatial prompt fragment, no master reference
  → existing (pre-this-design) generation behavior, unchanged

continuity file present but a character isn't listed in `spatial`
  → the prompt includes only the known constraints for characters that
    are listed; an unlisted character is generated with no blocking
    constraint, same as today
  → this is not an error and must not fail generation
```

## Master-shot lifecycle

`master_shot` is *selected* (written into the continuity file) before that
shot has an image — it's a forward declaration, decided at scene-authoring
time (Storyboard Step 2), independent of generation order or retries.

The master reference is a **fixed anchor, not a live pointer**. The whole
point of the master image is to be a stable point later shots can't drift
away from — if it silently followed the master shot's own later
regenerations, the canonical reference itself could drift, which defeats
the purpose. So locking it is a separate, explicit, one-time step:

```
set-scene-continuity called (master_shot = S01_SH01)
        ↓
S01_SH01 generated, reviewed, locked (select-candidate, as normal)
        ↓
lock-continuity-master --scene S01   (new, explicit command — see below)
        ↓
S01_SH01's artifact AT THAT MOMENT is copied to a fixed, permanent path
and recorded as `master_reference_image` in the continuity file
        ↓
that snapshot never changes again unless a later
`lock-continuity-master --force` deliberately re-locks it
```

Until `lock-continuity-master` has been run, `master_reference_image` is
absent and generation for other shots in the scene proceeds **without** a
master-reference image — not an error, just "not available yet." A later
regeneration of `S01_SH01` itself (a new version, a re-selected candidate)
does **not** automatically update `master_reference_image` — the frozen
snapshot stays exactly what it was at lock time until someone deliberately
re-locks it. `master_shot` (the id) and `master_reference_image` (the frozen
file) are two different fields for two different purposes: the id is
bookkeeping/traceability ("this snapshot came from S01_SH01"); the frozen
file is what generation actually references.

## Generation-time integration

### Reference image ordering

Extends the existing reference-building path in `cli.py`
(`_character_and_environment_references` / `_image_references`, both
consumed by `generate-image` and `generate-candidates --target shot:...`).
New order:

```
environment → characters → master reference image (if master_reference_image
  is set, and if it differs from both the current shot and the previous-shot
  reference) → previous-shot image
```

Resolution is a plain read of `master_reference_image` from the scene's
continuity file — no shot lookup, no live-artifact resolution. If it's
absent (not yet locked via `lock-continuity-master`), no master reference is
attached; this is not an error.

Master reference and previous-shot reference serve different, non-replacing
roles and both should be attached when they differ:

> **Invariant:** the master reference is always the scene's fixed anchor;
> the previous-shot reference is always the local temporal anchor. Neither
> replaces the other. If they resolve to the same image (e.g. the scene's
> second shot, where "master" and "previous" are both SH01), only one copy
> is attached — never send the same image twice.

This is the structural difference from the current previous-shot-only
chain:

```
        MASTER                    SH01 → SH02 → SH03 → SH04
        /    \                    (compounding drift, no fixed point —
      SH01   SH02                  what this design replaces)
              \
              SH03
```

Never append the master reference when the shot *being generated* **is**
the master shot itself (no self-reference).

### Prompt text

`build_image_prompt(shot: dict, spatial: dict | None = None) -> str` gains
an optional parameter — the function stays pure (no file I/O; the caller in
`cli.py` loads the scene's continuity file and calls
`effective_spatial_state` before invoking it). When `spatial` is non-empty,
a text fragment renders it next to the existing reference-legend fragment,
e.g.: `"Mara Voss is screen-left, facing right; Doctor is screen-right,
facing left — maintain these relative positions unless the shot's action
explicitly changes them."` Scoped to image generation only —
`build_video_prompt` is unchanged, since video conditions on the
already-correctly-blocked locked image and inherits correctness for free.

## CLI commands

Four new commands, no provider spend — pre-authorized in
`.claude/settings.json` alongside `check-continuity`/`add-feedback`.

**`set-scene-continuity --scene <id> [--master-shot <shot-id>] --character "<name>" --screen-side <left|center|right> --facing <left|right|camera|away>`**

Upserts one character's *initial* state. Called once per on-screen character
when the Storyboard agent authors a scene's first shot. **Must not silently
rewrite an established state**: if the character already exists in
`spatial` with a *different* `screen_side`/`facing`, the command fails with
a clear error rather than overwriting — re-running with the *same* values is
a harmless no-op (safe to retry). Deliberately revising an established canon
requires `--force`, an explicit, separate signal of intent — never a
side effect of a routine retry. `--master-shot`, when given, sets/updates
`master_shot` on the file (also idempotent for the same value).

**`--force`'s scope is narrow and must stay narrow**: it permits changing
the *stored* initial state, nothing more. It does **not** regenerate,
re-validate, or otherwise touch any already-generated shot — a shot locked
against the old canon does not get silently repaired, and may well now
fail the Continuity check (Step 3) against the revised canon. That's
correct, expected behavior: `--force` is for intentional canon correction
during authoring, and the caller (the Storyboard agent, or a human) is
responsible for deciding what to do about any shots that predate the
correction. This keeps the command's effect fully predictable — it changes
exactly the file it's told to change, never triggers a hidden production
workflow.

**`lock-continuity-master --scene <id> [--force]`**

Copies the scene's `master_shot`'s *current* locked artifact to a
permanent, scene-owned path (`02_scenes/SC<NN>_master_reference.png`) and
records that path as `master_reference_image`. Requires `master_shot`'s
image to actually be locked (`generation.image.artifact` populated) —
fails clearly otherwise, telling the caller to lock that shot's image
first. Idempotency mirrors `set-scene-continuity`: if
`master_reference_image` is already set, the command fails unless
`--force` is passed — re-locking is a deliberate act, same reasoning as
revising `spatial`: a routine retry must never silently replace the
scene's fixed anchor.

**`add-continuity-transition --scene <id> --after-shot <shot-id> --character "<name>" --screen-side <..> --facing <..> --reason "<text>"`**

Adds (or merges into) the transition for that `after_shot`: if a transition
for the same `after_shot` was already created earlier in this authoring
pass, this character's change is merged into its `changes` map (and
`reason` is updated to this call's text); otherwise a new transition entry
is created. One character per call, by design — mirrors how
`set-scene-continuity` is also one character per call, keeping each CLI
invocation's semantics simple and the failure surface small.

**`show-continuity --shot <id>`**

Read-only. Resolves the shot's scene, loads its continuity file (prints a
clear "no continuity file for this scene" message if absent — not an
error), and prints `effective_spatial_state` for that shot. Used by the
Continuity check (Step 3) so the agent isn't hand-folding transition logic
itself, and optionally by Step 2 before deciding whether a new transition is
needed.

## Agent workflow changes (`.claude/agents/ai-film-storyboard.md`)

**Step 2 addition:** when writing a scene's *first* shot (only), after
deciding the shot breakdown, the agent decides each on-screen character's
initial `screen_side`/`facing` and records it via `set-scene-continuity`
(once per character) plus `--master-shot <that shot's id>` — before writing
that shot's `action` text, so the action can reference the same
established layout. If a later shot in the same scene requires a
deliberate blocking change (a character crosses the room, walks around
another), the agent calls `add-continuity-transition` for the affected
character(s) with a `reason`, rather than just writing new `action` text
and hoping it reads as consistent.

**Step 5 addition:** immediately after the scene's *first* shot's image is
generated and locked (`select-candidate`, as normal — this always happens
before any other shot in the scene, per Step 5's existing strictly-
increasing-order rule), the agent runs `lock-continuity-master --scene
<id>` before moving on to the scene's next shot. This is the one point
where the master reference snapshot gets created; every later shot in the
scene then has it available.

**Step 3 addition:** the continuity check gains an explicit rule, checked
via `show-continuity --shot <id>` before judging: *a shot's described
blocking may differ from the effective spatial state only if a transition
already explains the difference by that shot* — otherwise `failed`, not "the
agent's vague sense that it's probably fine." Critically, this check is
about **relative spatial relationships and declared facing, not pixel-level
framing** — a close-up shot filling the frame with one character is not a
violation of `screen_side: right` just because the other character isn't
visible; the canon constrains where a character *would be* if shown, not
that every shot must show it. Composition/shot size decisions remain the
agent's normal judgment call, layered on top of (never contradicting) the
canon.

**The canon is upstream of generation, never downstream of it.** If a
generated shot doesn't match the effective spatial state and no transition
explains why, the fix is to regenerate that shot against the canon — never
to edit the canon to match what the model happened to produce:

```
generated shot disagrees with canon, no transition on file
  ❌ change canon to match the generated shot
  ✅ regenerate the shot against the existing canon
```

Only a deliberate, story-driven blocking decision (made by the agent while
*authoring*, via `add-continuity-transition`) is allowed to change what the
canon says is true. A generation that happened to drift is never, on its
own, evidence that the canon was wrong. This is what makes it a continuity
system rather than a log of whatever the model happened to generate.

## Testing plan

**Unit — `scene_continuity.py`:**
- `effective_spatial_state` with no transitions returns `spatial` unchanged.
- One transition: shot at exactly `after_shot` still sees the *old* state;
  the very next shot sees the *new* state (the off-by-one boundary case —
  `A=left` initially, transition `after_shot: SH01` sets `A=center`;
  assert `effective(SH01)["A"] == "left"` and `effective(SH02)["A"] ==
  "center"`).
- Transition ordering is independent of list order (append two transitions
  out of chronological order, assert folding still applies them by shot
  number).
- A transition naming only one character leaves the other character's
  state untouched.
- Validation rejects an unknown `screen_side`/`facing` value.
- `set-scene-continuity`-equivalent upsert: same character/same values
  twice succeeds; same character/different values without `--force` fails;
  with `--force` succeeds and overwrites.
- `add-continuity-transition`-equivalent: two calls with the same
  `after_shot` for different characters merge into one transition entry.
- A `spatial` map with 3+ characters validates and folds transitions
  correctly — guards against the implementation accidentally assuming
  exactly 2 characters anywhere in `effective_spatial_state` or validation.
- `lock-continuity-master`-equivalent: fails if `master_shot`'s image isn't
  locked yet; on success, copies the artifact and sets
  `master_reference_image`; a second call without `--force` fails and
  leaves the existing snapshot untouched; a later regeneration of
  `master_shot`'s own image does not change `master_reference_image` (no
  automatic re-sync — this is the "fixed anchor" invariant, tested
  directly: regenerate the master shot's image, assert the frozen
  snapshot's bytes/path are unchanged).

**Integration:**
- Full scenario: scene canon has `A=left, B=right`, `master_shot=S01_SH01`
  and its image is locked and snapshotted via `lock-continuity-master`;
  SH01's built prompt includes the spatial fragment; SH02 (no transition
  yet) still includes the same fragment and its reference list includes
  the frozen master-reference image; after a transition declared
  `after_shot: S01_SH02`, SH03's prompt reflects the updated state and
  SH03's reference list still includes the *original* frozen master
  image, unchanged (the master anchor doesn't change just because
  blocking did — it's a fixed visual reference point, not a description
  of "current" state).
- Absent continuity file: `generate-image`/`generate-candidates` behavior
  is unchanged from before this design (no fragment, no master reference,
  no error).
- Reference list de-duplication: when the frozen master-reference image and
  the previous-shot reference resolve to the same file, only one copy is
  attached, not two. **Note:** given the frozen-snapshot design, this
  literally never happens through normal use — `master_reference_image`
  always lives under `02_scenes/`, a previous-shot reference always lives
  under `04_storyboard/` (or wherever the video/image stage writes), so the
  two paths can't collide by construction; the scene's second shot (whose
  previous shot is also the master shot) attaches *both* the frozen master
  copy and the master shot's own live artifact, which is correct, not a
  duplicate. The de-dup check still belongs in the code as a defensive
  guard for the literal invariant ("never send the same image twice"), and
  the test plan should verify that guard directly (by forcing a path
  collision) rather than expecting the normal flow to exercise it.
