# Character Turnaround (Reference Sets) — Design

## Goal

> Character Lock stops producing "one nice picture of the character" and
> starts producing a small set of clean, reusable identity-conditioning
> assets — the same character from front, three-quarter, side, and back —
> so shot generation and motion transfer have something to anchor against
> besides a single front-ish pose.

This is not a documentation artifact (the attached professional-style
"character reference sheet" — palette swatches, wardrobe breakdown,
personality blurb, cinematic close-up — is inspiration, not the output).
The actual deliverable is machine-usable: a handful of independently
addressable PNGs a resolver hands to image/video generation exactly the
way `reference.png` already is today.

## Non-Goals

| Item | Decision | Why |
|---|---|---|
| A single generated grid/contact-sheet image, cropped into per-angle files | Rejected | Requires image-cropping infrastructure this project has none of, and text-in-image panel layouts from pure prompting are unreliable to crop deterministically. Verified via research that this is how most *consumer* "character sheet" tools work, but it optimizes for a presentable single image, not for independently addressable production assets. |
| ControlNet / OpenPose / pose-skeleton conditioning | Rejected | This project has zero pose-conditioning infrastructure today (no ControlNet nodes, no skeleton input anywhere). Adding it to get more reliable turnaround angles would turn a contained feature into a new image-generation subsystem. The existing reference-conditioned generation (attach an image, vary the text) already has a proven track record in this codebase — see camera-variant candidate generation — and is reused as-is. |
| Color palette swatches, wardrobe/accessory breakdown, structured "character overview" fields | Deferred | Explicitly a V2 nice-to-have per the original request; no consumer for this data exists anywhere in the pipeline today. |
| Automated visual consistency checking (comparing turnaround candidates against the primary reference pixel-wise or via embedding similarity) | Rejected for V1 | No image-comparison infrastructure exists in this codebase. The practical, no-new-infrastructure mitigation is a strengthened generation prompt (explicit "preserve exact outfit/hairstyle/proportions, do not redesign" instruction, extending the existing reference-legend technique) plus the existing human/agent candidate-review loop — the same mechanism that already catches a bad primary-reference candidate today. |
| A required, always-generated turnaround set for every character | Rejected | Offered per character, skippable. A background NPC on screen for two seconds doesn't need three extra generation calls; a protagonist appearing across many scenes and angles does. |
| Changing `shot.json`'s `characters[].reference` field type | Rejected — and this is the key simplification of this design | See "Why `characters[].reference` never changes shape" below. |
| Retroactively regenerating already-locked primary references to meet the new "no background" prompt rule | Rejected | This spec changes prompt *guidance* going forward. An existing project's already-locked characters are unaffected unless a human explicitly re-runs the Character agent for them. |

## Why `characters[].reference` never changes shape

The original request proposed a schema union (`reference: string | {front, three_quarter, ...}`). Working through the actual data flow shows this isn't necessary: `characters[].reference` in a shot is always, in the end, **one resolved path** — whichever single image should condition that shot's generation. The multi-angle *set* only needs to exist upstream of that resolution, at the character-asset level, as a filesystem convention:

```
assets/characters/mara/reference.png                    # primary / front — unchanged, always present
assets/characters/mara/turnaround/three_quarter/reference.png
assets/characters/mara/turnaround/side/reference.png
assets/characters/mara/turnaround/back/reference.png
```

No manifest JSON is introduced — "does this angle exist" is answered by `Path.exists()`, exactly like the primary reference already is. `SHOT_SCHEMA` needs exactly one additive change: an optional, unconstrained `characters[].orientation` string field (conventionally `front`/`three_quarter`/`side`/`back` for V1, but never schema-enforced — see "The angle vocabulary is open-ended" below), recording which angle Storyboard judged the shot needs. `characters[].reference` itself stays a plain string, populated by a new resolver instead of a hardcoded path — every existing consumer (image generation's reference conditioning, `check-stale`'s `source_assets` tracking, motion-transfer) needs zero changes, since they only ever read a string path from that field regardless of how it was chosen.

## Architecture

```
Character Lock (existing, unchanged)
        │
        ▼
  Primary reference locked (reference.png)
        │
        ▼
  NEEDS_INPUT: "Generate turnaround angles for continuity? (three-quarter / side / back)"
        │
   ┌────┴────┐
   │ skip    │ yes
   ▼         ▼
 done    For each angle (three_quarter, side, back):
              generate-candidates --target character:<name>:turnaround:<angle>
                  (auto-conditioned on the primary reference.png)
              review / edit-candidate / select-candidate   (existing loop, unchanged)
              → assets/characters/<name>/turnaround/<angle>/reference.png
        │
        ▼
  Character Reference Set locked
        │
        ▼ (used later, per shot)
Storyboard: resolve_character_reference(project_dir, name, orientation)
        │
        ▼
characters[].reference = <resolved path>   (unchanged field, unchanged type)
```

## Candidate-loop target extension

`candidate_store.py`'s `target_dir` currently parses `character:<name>` strictly as 2 parts. Extend it to also accept the 4-part form `character:<name>:turnaround:<angle>`:

```python
if kind == "character":
    if len(parts) == 2:
        return project_dir / "assets" / "characters" / parts[1]
    if len(parts) == 4 and parts[2] == "turnaround":
        return project_dir / "assets" / "characters" / parts[1] / "turnaround" / parts[3]
    raise ValueError(f"malformed character target {target!r}")
```

This is the only engine change needed to reuse the *entire* existing candidate loop (`generate-candidates`/`review`/`edit-candidate`/`select-candidate`) for turnaround angles — `select_candidate`'s character/env branch already writes to `directory / "reference.png"` generically, so it needs no change at all once `target_dir` resolves the new shape correctly. `scope_for_target` needs no change either (`kind == "character"` already maps to scope `"bibles"` regardless of part count, which is correct — turnaround generation is a bibles-scope cost, same as the primary reference).

**The angle vocabulary is open-ended at the schema/mechanism level, not closed.** `target_dir`'s new parsing branch, `select_candidate`, and the resolver are all generic over whatever angle *string* is used — nothing enforces a fixed set. `characters[].orientation` stays `{"type": "string"}` in the schema (unconstrained, matching how `camera` is already a free-form object) specifically so a future angle like `left_three_quarter` never requires a schema change — only generating a `character:<name>:turnaround:left_three_quarter` target and setting `orientation` to match.

What *is* fixed for V1 is narrower: `ai-film-character.md`'s post-lock offer only proposes three default angles (`three_quarter`, `side`, `back`) rather than prompting for an open-ended list — keeping the agent's own UX simple — and `resolve_character_reference`'s fallback logic doesn't need a translation table because Storyboard writes `orientation` using the same angle-name strings the character's turnaround directories use directly. Both of these are agent/UX conveniences, not schema constraints, and can grow independently later without touching the mechanism described here.

## Generation: reference conditioning and the isolation requirement

**Every locked character image — primary and every turnaround angle — must be an isolated, clean character reference with no environment, no scenery, no cinematic lighting, no story context.** This is a real, go-forward change to the *existing* primary-reference prompt guidance in `ai-film-character.md` (which currently says only "matching the cinematic tone from `story.md`," with no explicit exclusion of background/scene elements) — not just new behavior for turnaround angles.

Concrete prompt requirement, appended to every character-locking prompt (primary and turnaround alike):

> "isolated character reference, clean neutral background, no environment, no scenery, no background elements, full body, consistent studio-style lighting"

Neutral/white background is preferred over transparency for V1 — more stable for reference-conditioned generation than alpha channels, and needs no new format handling anywhere in the pipeline.

**Turnaround generation is reference-conditioned on the primary `reference.png`**, the same mechanism `characters[].reference` already uses for shot generation (attach the image, the model preserves identity/appearance from it) and the same mechanism camera-variant candidate generation already proved out for varying one text axis while holding a reference fixed. `generate_candidates_cmd` currently only auto-gathers `reference_paths` for `shot:` targets; extend it so a `character:<name>:turnaround:<angle>` target auto-attaches `assets/characters/<name>/reference.png` as a reference path, the same way a shot target auto-gathers its character/environment references — the agent still supplies `--prompt` (built from the character bible + the target angle + the isolation requirement above + an explicit "preserve exact outfit, hairstyle, proportions — do not redesign" instruction), but does not need to manually pass `--reference` since the engine already knows which reference a turnaround target implies.

## The resolver

```python
def resolve_character_reference(project_dir: Path, character_name: str, orientation: str | None) -> str:
    if orientation:
        candidate = project_dir / "assets" / "characters" / character_name / "turnaround" / orientation / "reference.png"
        if candidate.exists():
            return project_relative_path(str(candidate), project_dir)
    return f"assets/characters/{character_name}/reference.png"
```

Pure, filesystem-truth-based, no manifest to keep in sync. `orientation=None` or "no matching angle locked yet" both fall through to the primary reference — this is how backward compatibility is automatic: a project that never generates any turnaround set behaves exactly as today, because every resolution attempt falls through to the same `reference.png` path Storyboard already hardcodes.

## Storyboard integration

`ai-film-storyboard.md` gains one new judgment call per character per shot: alongside deciding `camera` (shot framing — `wide`/`medium`/`close-up`/etc., an independent axis, unchanged), decide `orientation` (which way the character is facing *in the world*, not how the camera frames them) when it's not simply "front" — e.g., a shot written as the character walking away from camera implies `orientation: "back"`; an over-the-shoulder shot implies `orientation: "three_quarter"` or `"side"` depending on blocking. When in doubt or when the shot doesn't call for anything unusual, omit `orientation` entirely (defaults to front via the resolver's fallback) — this is not a field Storyboard needs to fill in on every shot, only when it materially matters.

Storyboard calls `resolve_character_reference(...)` instead of hardcoding `assets/characters/<name>/reference.png`, and writes both the resolved `reference` path and the `orientation` judgment (when set) into `characters[].{reference,orientation}`.

## `ai-film-character.md` flow change

After the primary reference is locked (existing Step, unchanged), add one new step:

1. `NEEDS_INPUT`, `type: confirmation`: "Generate a turnaround set (three-quarter, side, back) for continuity across angles? This costs 3 more generation rounds." Skippable — a `HUMAN_RESPONSE` of no ends the agent's work for this character exactly as today.
2. If yes: for each of the three angles in order, run the existing generate/review/edit/select loop against `character:<name>:turnaround:<angle>`, using a prompt built from the character bible + the angle + the isolation and consistency-preservation requirements above.
3. Report completion as "Character Reference Set locked" (front + however many angles were completed) rather than just "character locked," when any turnaround angle was completed.

## Testing

- `candidate_store.py`: `target_dir`/`scope_for_target` accept the new 4-part turnaround target shape; reject a malformed one (wrong segment count, wrong literal `"turnaround"` segment) with a clear `ValueError`, matching the existing malformed-target error style.
- `select_candidate` on a turnaround target writes to `assets/characters/<name>/turnaround/<angle>/reference.png` with no code change required — a regression test proving this generic behavior actually holds for the new target shape (not just asserted from reading the code).
- `generate_candidates_cmd`: a turnaround target auto-attaches the primary `reference.png` as a reference path without the caller passing one explicitly; a plain `character:<name>` target's behavior is completely unchanged (no reference auto-attached, matching today).
- `resolve_character_reference`: returns the turnaround path when it exists; falls back to the primary reference when the angle file doesn't exist, when `orientation` is `None`, and when the character has no turnaround set at all (a project that never used this feature).
- End-to-end: a shot with `orientation: "back"` set, where the character's turnaround set includes a locked back angle, resolves to that file; the same shot before the back angle is locked (or before any turnaround exists) resolves to the primary reference — proving the fallback is real, not just documented.
- No existing test's behavior changes — the whole design is additive.

## Success criteria

A user locks a character, is offered (and can decline) a turnaround set, generates three-quarter/side/back candidates conditioned on the same locked identity, and later, when Storyboard writes a shot where the character is seen from behind, that shot's `characters[].reference` automatically resolves to the locked back-angle image instead of the front reference — with zero effect on any existing project that never uses this feature at all.
