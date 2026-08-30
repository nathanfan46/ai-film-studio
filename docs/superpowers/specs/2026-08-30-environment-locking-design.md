# Environment Locking — Design

**Status:** Approved for implementation
**Date:** 2026-08-30

## 1. Overview

Characters get a locked reference image (`characters[].reference`), generated once through a candidate loop and conditioned into every shot that uses them. Locations get nothing equivalent — a shot's setting exists only as free text in `action`/`visual.lighting`, re-typed by hand into every shot. On the live `one-more-life` project this broke concretely: `S01_SH01`'s `action` field explicitly says "hospital corridor, institutional white walls" and reliably generates there; `S01_SH02`'s `action` field never mentions a location at all (only "cold fluorescent light... clinical"), and its generated images repeatedly drifted to a different room. Traced to `build_image_prompt` (`src/ai_film/prompts.py`), which concatenates only `action` + `visual.style` + `visual.lighting` + `camera.shot` — nothing else exists in the schema to anchor a location, and nothing forces consistency across shots the way `characters[].reference` conditioning does for people.

This spec gives locations the same treatment characters already get — a named, reusable, lockable entity with a real reference image, conditioned into every generation call for shots set there — reusing engine infrastructure that already exists rather than inventing new mechanism.

### What already works, unchanged by this spec

- `candidate_store.py` already treats `env:<name>` as a full target kind: `target_dir` resolves it to `assets/environments/<name>`, `scope_for_target` maps it to the `"bibles"` cost-gate scope — identical to `character:<name>`. The entire candidate loop (`generate-candidates` → `review` → `edit-candidate` → `select-candidate`) already runs against `env:` targets today, and `select-candidate` already writes `assets/environments/<name>/reference.png`. **Zero engine changes needed for the locking mechanism itself.**
- `reference_paths` (in `models.py`/`generation_service.py`) is already a flat, multi-image-capable list — populated today from `characters[].reference`. An environment reference slots into the same list.
- The real fal upload path (`providers/fal/client.py`'s `upload_file`) genuinely works — it uploads a local file and returns a real, usable URL. The README's "Known Limitations" claim that this is broken is stale and gets corrected as part of this work.

### A real API constraint this spec designs around

Read directly from the provider code: **image generation sends every reference path** (`providers/fal/image.py`: `input_data["image_urls"] = [client.upload_file(p) for p in request.reference_paths]`), but **video generation sends only `reference_paths[0]`** (`providers/fal/video.py`: `input_data["image_url"] = client.upload_file(request.reference_paths[0])`) — a single image, not a list. This is pre-existing behavior, not something this spec introduces: today, a two-character shot already silently drops its second character's reference for video generation, because the list built for the multi-image case gets reused as-is for the single-slot case. Naively appending an environment reference to the end of that same list would make it invisible to video generation the same way. §4.3 designs around this directly instead of ignoring it.

### Goals

- A location is a named, reusable entity, like a character — the same locked location can be referenced by shots across different scenes (a story revisiting a place later reuses the same lock, not a fresh drift-prone generation).
- Every shot set in a locked location gets that location's reference image conditioned into its generation, the same way character references already are.
- Discovery and locking follow the exact shape already established for characters: the Director records which location each scene uses, `/create-film` dispatches a new agent once per unique location name, using the same candidate loop, the same cost-approval protocol, before Storyboard writes any shots.
- Video generation conditions on the shot's own locked storyboard image once one exists, rather than raw reference sheets — a strictly better single anchor for the one-image-only video API, and consistent with how real multi-shot AI video tools chain from a confirmed prior frame.
- Storyboard image-candidate generation additionally chains from the immediately preceding shot's locked image within the same scene (§4.2) — the same "last confirmed frame" principle applied one stage earlier, closing the shot-to-shot continuity gap that environment locking alone doesn't cover.
- Correct the now-stale README claim that fal reference-image upload doesn't work.

### Non-Goals

- **No automatic text-injection into prompts from a stored environment description.** Matching how characters already work: the engine never auto-injects character bible text into `build_image_prompt`, appearance consistency comes entirely from reference-image conditioning, and bible text exists purely as agent-facing context for writing new shots. Environments get the identical treatment — no new prompt-assembly mechanism invented for this spec.
- **No per-shot multi-location support.** A shot references exactly one environment, matching how `duration_seconds`/`camera` are already singular per shot. A scene spanning two rooms is two shots (or two scenes), each with its own single `environment` reference — same granularity a scene already uses for splitting camera setups.
- **No engine changes to the candidate loop itself.** `env:` targets already work end-to-end; this spec only adds the pieces that were missing (a schema field to reference the lock, reference-conditioning wiring, and agent orchestration to discover/lock locations before shots are written).
- **No change to how `reference_paths` conditions image generation's *character* references** — only how the environment reference is added alongside them, and how video's single-slot constraint is resolved (§4.3).

## 2. Architecture

```
/create-film (orchestrator)
    │
    Step 2: Director agent
      writes 02_scenes/SC*.md, each scene now also carrying a
      **Location:** line (parallel to the existing **Characters:** line)
      final report now also carries: LOCATIONS: <name1>, <name2>, ...
    │
    Step 3: Character agent — unchanged — once per unique character name
    │
    Step 4 (NEW): Environment agent — once per unique location name
      same candidate loop, same cost-approval protocol as Character
      writes 01_bibles/environments/<name>.md
             assets/environments/<name>/reference.png
    │
    Step 5: Storyboard agent (renumbered from Step 4)
      each shot's environment field is populated from its scene's
      **Location:** line (same way characters[] is already populated
      from **Characters:**), referencing the locked environment by name
    │
    Step 6: Media agent (renumbered from Step 5) — unchanged logic,
      generate-video now conditions on the shot's own locked storyboard
      image once available (§4.3), not raw reference sheets
    │
    Step 7: Wrap up (renumbered from Step 6)
```

Every new piece mirrors an existing one exactly: `ai-film-environment.md` mirrors `ai-film-character.md` line-for-line in structure (re-entry check, human-in-the-loop protocol, cost estimate/approval, generate/review/edit/select loop, completion report) — only the target kind (`env:` instead of `character:`) and the bible template's sections differ.

## 3. Data Model

### 3.1 `shot.json` — new `environment` field

Additive to the schema (non-required, so every shot.json written before this spec remains valid with no migration):

```json
"environment": {
  "name": "hospital_corridor",
  "reference": "assets/environments/hospital_corridor/reference.png"
}
```

`name` is a slug (lowercase, underscores — same convention as character names being human-readable strings, but locations get slugged since they're also used as directory names under `assets/environments/`, and directory-safe names avoid the spaces/punctuation a location description might otherwise contain). `reference` is always the project-relative path to that environment's locked `reference.png`, populated the same way `characters[].reference` already is — written by whichever agent (here, Storyboard) assigns the shot to that location, not looked up dynamically at generation time.

### 3.2 `02_scenes/SC*.md` — new `**Location:**` line

Illustrative format (unchanged: nothing parses this file with a strict schema, same as today):

```markdown
# Scene 1: The Corridor

**Characters:** Mara Voss, Doctor

**Location:** hospital_corridor

**Action:** A lone engineer walks through a dim, humming corridor...
```

A scene's `**Location:**` line names exactly one location — matching the one-environment-per-shot Non-Goal, a scene's default is inherited by every shot in it unless the Storyboard agent's own judgment (same judgment it already applies to whittle a scene's `**Characters:**` list down to who's actually visible per shot) decides a specific shot needs a different one.

### 3.3 `01_bibles/environments/<name>.md` — the environment bible

Mirrors a character bible's shape and purpose (agent-facing context for writing consistent shots at this location, never auto-injected into generation):

```markdown
# hospital_corridor

## Description

<2-4 sentences — specific enough to drive an image generation prompt:
architecture, materials, color palette, lighting character>

## Mood / role in the story

<1-2 sentences — what this place means in the story, from story.md/the scenes>
```

## 4. Generation Wiring

### 4.1 `reference_paths` construction gets an environment slot

Today, `cli.py` builds `references = [c["reference"] for c in shot_data.get("characters", []) if c.get("reference")]` identically in three places (`generate_image_cmd`, `generate_video_cmd`, `generate_candidates_cmd` for shot targets). This spec changes the construction (in all three places, plus the equivalent spot in `services/generation_service.py` callers if the list-building ever moves there) to:

```python
references = []
if shot_data.get("environment", {}).get("reference"):
    references.append(shot_data["environment"]["reference"])
references += [c["reference"] for c in shot_data.get("characters", []) if c.get("reference")]
```

**Environment reference goes first.** For image generation (which sends the whole list as `image_urls`), order is a reasonable anchor-first convention, not load-bearing. For video generation specifically, order is load-bearing — see §4.3.

### 4.2 Image generation: environment/character ordering, plus same-scene previous-shot chaining

`providers/fal/image.py` already uploads and sends every path in `reference_paths` as `image_urls` — the environment reference just rides along in the same, already-correct mechanism for the environment/character part.

**A second, related gap this spec also closes:** environment locking anchors *which location* a shot is in, but not shot-to-shot *continuity* within a scene — today, each shot's storyboard image candidates are generated with no visibility into what the immediately preceding shot in that scene actually looked like. This is the same "chain from the last confirmed frame" principle already justified in §4.3 for video, applied one stage earlier: when generating storyboard image candidates for a shot (via `generate-image` or `generate-candidates --target shot:<id>:image`), also include the immediately preceding shot's locked image (same scene, next-lower shot number, i.e. `S01_SH02` chains to `S01_SH01`) as an **additional** reference — never a replacement for the environment/character references, and only when that prior shot exists and already has a completed image artifact.

This is additive, not exclusive, deliberately: a wide establishing shot followed by a close-up on a different character is legitimately supposed to differ in framing, and forcing exact visual repetition would fight that. Adding one more reference image alongside the locked environment/character references reinforces "this is recognizably the same room, a moment later" without demanding identical composition — the same way having multiple character references today doesn't force the output to look identical to any single one of them.

```python
def previous_shot_image_reference(project_dir: Path, shot_id: str) -> str | None:
    """The immediately preceding shot's locked storyboard image, same scene,
    if one exists and is already completed — None for a scene's first shot,
    or if the preceding shot has no locked image yet."""
    scene, num_str = shot_id.split("_SH")
    num = int(num_str)
    if num <= 1:
        return None
    prev_id = f"{scene}_SH{num - 1:02d}"
    prev_path = project_dir / "03_shots" / f"{prev_id}.json"
    if not prev_path.exists():
        return None
    prev_shot = load_shot(prev_path)
    artifact = prev_shot.get("generation", {}).get("image", {}).get("artifact")
    return artifact["path"] if artifact else None
```

§4.1's `references` construction gains one more line, applied identically wherever it's built (`generate_image_cmd`, `generate_candidates_cmd` for shot targets):

```python
prev_ref = previous_shot_image_reference(path, shot)
if prev_ref:
    references.append(prev_ref)
```

Order: environment, then characters (§4.1), then the previous shot's image last — anchoring identity first, continuity second. `generate-candidates`' multi-candidate output is unaffected by this ordering; it's only load-bearing for video's single-slot case (§4.3), which doesn't consume this list at all once a shot's own image is locked.

### 4.3 Video generation: condition on the shot's own locked storyboard image, not raw reference sheets

Per the confirmed design decision: once a shot has a completed image artifact (`generation.image.artifact.path` — populated once `select-candidate` or `generate-image` locks it in), `generate-video`'s reference conditioning uses **that image alone** as `reference_paths`, replacing the raw character/environment reference list entirely for this call. Rationale, not just preference:

- fal's video API accepts exactly one reference image (§1's confirmed constraint) — forcing a choice between "the environment" and "a character" for that single slot is a false choice once a better single image exists: the shot's own locked storyboard frame already has the correct location *and* the correct character(s) composed together, since image generation (§4.1/4.2) now correctly conditions on both.
- This matches documented real-world practice for multi-shot AI video consistency — anchoring the next generation on the last confirmed frame/image rather than a generic reference sheet.

Concretely, in `cli.py`'s `generate_video_cmd` (and the `generate-all --stage video` batch path in `_build_stage_call`):

```python
image_artifact = shot_data.get("generation", {}).get("image", {}).get("artifact")
if image_artifact and image_artifact.get("path"):
    references = [image_artifact["path"]]
else:
    references = []
    if shot_data.get("environment", {}).get("reference"):
        references.append(shot_data["environment"]["reference"])
    references += [c["reference"] for c in shot_data.get("characters", []) if c.get("reference")]
```

The fallback (no locked image yet — an edge case, since Storyboard's phase always locks an image before Media's phase generates video, but not a case to leave undefined) reuses §4.1's construction unchanged, so video generation degrades gracefully rather than erroring when called out of the normal pipeline order.

### 4.4 `build_image_prompt`/`build_video_prompt`: unchanged

Per the Non-Goals correction: no new text gets auto-assembled from environment data. These functions keep concatenating exactly what they already do (`action`, `visual.style`, `visual.lighting`, `camera.shot`/`movement`) — the environment's contribution to consistency is the reference image (§4.1-4.3), not prompt text. An agent writing a *new* shot at an already-locked location is expected to read that location's bible (§3.3) the same way it already reads a character's bible before writing appearance-consistent `visual`/`action` text — a documented agent responsibility (§5), not an engine mechanism.

## 5. Agent Responsibilities

**Director agent** (`ai-film-director.md`, modified): when writing each scene file, adds a `**Location:**` line alongside the existing `**Characters:**` line — one location name per scene, using the same brainstorming round trips already used to converge on characters (no new protocol type). The final completion report gains a second parseable line, `LOCATIONS: <name1>, <name2>, ...` (or `LOCATIONS:` with nothing after the colon if a story is somehow entirely location-agnostic — not expected in practice, but handled the same way an empty `CHARACTERS:` list already is).

**Environment agent** (`ai-film-environment.md`, new): dispatched once per unique location name in the parsed `LOCATIONS` list, mirroring `ai-film-character.md` exactly:
- Re-entry check: does `assets/environments/<name>/reference.png` already exist?
- Reads every scene mentioning this location for descriptive context (architecture, mood, lighting) the same way the Character agent reads every scene mentioning a character name; asks clarifying questions only for gaps the scenes don't already answer.
- Writes the bible (§3.3), computes a cost estimate using the *same* advisory image-model cost table already in `ai-film-character.md` (same models generate both), requests approval via the identical `cost_approval` round trip.
- Runs the identical generate → review → discuss → edit → select candidate loop, viewing each candidate image directly (Read tool) before writing any edit instruction — same discipline the Character and Storyboard agents already apply.
- Locks in via `select-candidate --target env:<name> --id <candidate-id>`, writing `reference.png`.
- Same wholesale-replace caveat on the `"bibles"` approval scope already documented in `ai-film-character.md` — a non-issue as long as each Environment dispatch (like each Character dispatch) fully resolves before the next one starts, which is how `/create-film` already dispatches both.

**Storyboard agent** (`ai-film-storyboard.md`, modified): when writing each shot, populates `environment: {"name": ..., "reference": "assets/environments/<name>/reference.png"}` from the shot's scene's `**Location:**` line (default) or a more specific judgment call if the scene's action clearly splits across locations — the identical judgment call already applied to narrow a scene's `**Characters:**` list down to who appears in a specific shot. Before writing any shot, confirms `assets/environments/<name>/reference.png` exists for that scene's location, the same existing-file check already performed for `assets/characters/<name>/reference.png` — if missing, stops and reports which location(s) still need the Environment agent run first (mirrors the existing character check exactly).

**Media agent** (`ai-film-media.md`): no changes to its own dispatch/protocol logic — it calls `generate-video` exactly as before; §4.3's behavior change lives entirely in the engine/CLI layer, transparent to this agent.

**`/create-film` orchestrator**: Step 3 (Character) stays as-is; a new Step 4 dispatches the Environment agent once per unique location name from the parsed `LOCATIONS` list, same sequential (never parallel) dispatch discipline already used for characters; Storyboard/Media/Wrap-up steps renumber to 5/6/7.

## 6. Error Handling and Edge Cases

- **A scene names a location the Director never explicitly asked about** (shouldn't happen given the brainstorming round trip, but not left undefined): same handling as an underspecified character — the Environment agent's own re-entry/context step asks the clarifying question directly rather than the Director being solely responsible for completeness.
- **Two scenes use slightly different wording for what's meant to be the same place** ("the corridor" vs. "hospital corridor"): a content/authoring concern, not an engine one — same category of risk that already exists for character names needing to match exactly across scenes (`ai-film-director.md` already documents this exact-match requirement for characters; the same requirement extends to location names here, stated in the Director agent's updated instructions).
- **A shot needs no locked location at all** (an abstract/black-frame shot, rare but possible): `environment` stays absent from that shot's JSON, exactly like `characters[]` can already be an empty list — no special-casing needed since the field is non-required.
- **Video generation runs before an image is locked** (out-of-order manual CLI use, not the normal pipeline path): §4.3's fallback covers this explicitly rather than erroring.

## 7. Testing Strategy

Engine-level changes (schema field, `reference_paths` construction in `cli.py`, `generate_video_cmd`'s image-artifact-first conditioning, `previous_shot_image_reference`) get real `pytest` coverage, following this codebase's existing patterns (`tests/test_schema.py` for the additive field, `tests/test_cli_generation_commands.py`-style tests asserting the mock provider receives the expected `reference_paths` for: a shot with an environment set, a scene's first shot (no previous-shot reference), a later shot whose predecessor already has a locked image (previous-shot reference included, appended last), a later shot whose predecessor has no locked image yet (gracefully omitted, not an error), a shot with a locked image present at video-generation time, and the video fallback path when it's absent). Agent-file changes (Director/Environment/Storyboard/`/create-film`) get the same behavioral verification approach already established in `docs/superpowers/plans/2026-08-23-agent-layer.md` and `docs/superpowers/plans/2026-08-28-media-agent.md` — real CLI command sequences against a scratch project with the mock provider, since there is no pytest for prompt files.

## 8. Documentation Corrections Bundled Into This Work

- `README.md`'s "Known limitations" section currently claims fal reference-image upload isn't implemented — stale per §1's direct code read; corrected as part of this spec's implementation, not left for a future cleanup.
- `README.md`'s Roadmap/pipeline description and `create-film.md`'s own step numbering get updated for the new Step 4, matching how the media-agent work already updated both for its own new step.

## 9. Future Extensions (explicitly out of scope for this spec)

- Multi-location shots (a single shot compositing two locations) — no evidence this is needed yet; today's one-shot-per-location-per-moment granularity matches how shots are already broken down.
- A programmatic check that flags when a scene's `**Location:**` name is suspiciously close-but-not-identical to another scene's (catching the "corridor" vs. "hospital corridor" drift risk automatically) — a real improvement, but a text-similarity heuristic is its own scoped feature, not a prerequisite for this one.
- Extending the same "condition video on the locked image" treatment to `generate-sfx`/`generate-music` — not applicable, neither takes an image reference today.
