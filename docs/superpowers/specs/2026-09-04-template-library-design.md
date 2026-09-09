# Template Library & Shot Auto-Draft — Design

## Goal

> A template captures reusable creative intent from a reference video —
> not a promise to reproduce the reference video.

Let an approved reference-video analysis (see
`docs/superpowers/specs/2026-09-04-reference-video-analysis-design.md`)
be saved as a named, reusable template that any future project can point
at. A new project supplies its own story, characters, and environment;
the template supplies the camera language, blocking, and pacing pattern
a shot sequence should follow. Drafting shots from a template means the
human writes less prose per shot — it does not mean skipping the
existing storyboard approval gate.

## Spec 1 relationship

This spec builds directly on Spec 1's output artifact
(`assets/reference-video/video_analysis_brief.json`, once a human has
approved it). Spec 1 is unchanged by this document. Read Spec 1 first —
this spec assumes its per-scene fields (`description`, `subject`,
`subject_motion`, `camera`, `motion_transfer_candidate`) as given.

## Non-Goals

| Item | Decision | Why |
|---|---|---|
| Camera geometry (orbit radius, angular velocity, FOV, height) | Rejected | Not reliably extractable from 2D reference footage without full structure-from-motion, and no fal model in this project's catalog accepts a structured camera path as input anyway — see Spec 1's "motion signal is advisory" discussion and the earlier `MOTION_TRANSFER` spec's rejection of designing against unconfirmed provider capabilities. |
| Automatic full-video generation from a template ("one-click" video) | Rejected | Would bypass the candidate loop and cost gate that every other generation path in this project goes through. Templates pre-fill shot fields for review, they don't replace review. |
| Music/BGM structure fields (`build_drop_hold` etc.) | Deferred | No existing shot.json or project field models cross-shot music structure today. Inventing one now, before any downstream code reads it, is the same speculative-field mistake Spec 1 avoided by skipping transcript extraction. |
| Cloud/shared template registry, versioning, template marketplace | Out of scope | This is a local file convention for one user's own reuse across their own projects, not a distribution mechanism. |
| Automatic scene-count-to-shot-count mapping algorithm | Left to agent judgment, not engine logic | A template's shot-pattern count and a new story's natural shot count will often differ. Deciding how to compress/expand a pattern is a creative judgment call, not a deterministic transform — see "Applying a template" below. |

## Architecture

```
assets/reference-video/video_analysis_brief.json   (Spec 1, approved)
              │
              ▼
   ai-film-reference-analyst: "save as reusable template?"
              │  (agent generalizes subject/scene text — drops
              │   identity specifics like "Superman", keeps
              │   structural/semantic content — writes a draft file)
              ▼
   ai-film save-template --from <draft.json> --id <template-id>
              │  (engine: schema validation + file placement only)
              ▼
   templates/<template-id>/template.json    (+ optional keyframes/)
              │
              │   ... used by a later, unrelated project ...
              ▼
   ai-film-director / ai-film-storyboard: reads template.json when the
   project config names one, uses its shot_patterns to pre-fill new
   shots' camera / action / description alongside that project's own
   story, characters, and environment
              │
              ▼
   Normal storyboard candidate loop → human approval → generation
   (unchanged — this feature only changes how a shot draft starts)
```

Two commands, both local/deterministic/zero-cost, following the same
engine-does-mechanics / agent-does-judgment split as Spec 1:

- `save-template` — validate + copy a file. No text generation, no
  generalization logic. The agent decides what the template's content
  should say; the CLI only checks it's well-formed and places it.
- `list-templates` / `show-template` — read-only enumeration, so an
  agent (or the human) can see what's available without grepping the
  filesystem by hand.

## File Layout

Templates live outside any single project, at the repo root:

```
templates/
  hero-orbit/
    template.json
    keyframes/               # optional, copied from the source brief
      shot1_start.jpg
      shot3_mid.jpg
```

`templates/` is not listed in `.gitignore`'s allowlist
(`!/…` entries), so it's already excluded by the existing `/*` catch-all
— consistent with how film projects are excluded today. Templates are
derived from a user's own reference footage and are personal working
data, not source code; they get the same treatment as project
directories, automatically, with no `.gitignore` change needed —
confirmed empirically (`git check-ignore -v templates/hero-orbit/template.json`
reports it ignored by the `/*` rule at `.gitignore:11`), the same
empirical-check discipline the `.gitignore` rewrite itself used.

## `template.json` schema

```json
{
  "schema_version": "1.0",
  "id": "hero-orbit",
  "name": "Hero Orbit Reveal",
  "created_at": "2026-09-04T12:00:00Z",
  "source_note": "Reference clip of a hero introduction, orbiting camera reveal",
  "shot_patterns": [
    {
      "order": 0,
      "pattern_name": "establish",
      "camera": "wide shot, static, low energy",
      "subject_motion": "N/A — establishing shot, no subject in frame",
      "framing": "wide, subject not yet visible",
      "suggested_duration_seconds": 3,
      "reference_keyframe": "keyframes/shot1_start.jpg"
    },
    {
      "order": 1,
      "pattern_name": "reveal_orbit",
      "camera": "camera orbits clockwise around the subject, medium shot tightening to close-up, ending on a low-angle hold",
      "subject_motion": "subject stands centered, minimal movement, holds a strong pose",
      "framing": "subject locked to frame center throughout",
      "suggested_duration_seconds": 5,
      "reference_keyframe": "keyframes/shot3_mid.jpg"
    },
    {
      "order": 2,
      "pattern_name": "impact_hold",
      "camera": "static, low angle, held for the final beat",
      "subject_motion": "subject fully still, heroic pose held",
      "framing": "close-up, subject fills most of frame",
      "suggested_duration_seconds": 2,
      "reference_keyframe": null
    }
  ]
}
```

Field notes:

- `subject` deliberately does **not** appear as a persistent identity
  field (no "a man in a red cape", no "Superman"). `subject_motion` and
  `framing` describe *behavior and composition*, which is the reusable
  part; the new project's own `characters[]` supplies who's actually in
  frame. This is the generalization step the agent performs when saving
  — Spec 1's brief keeps the literal subject description, the template
  strips it.
- `camera` and `subject_motion` reuse Spec 1's field vocabulary
  verbatim, which in turn reuses `shot.json`'s own vocabulary (see Spec
  1's schema notes) — the same text can flow brief → template → draft
  shot with no re-interpretation step in between.
- `reference_keyframe` is optional and purely for the agent's own visual
  grounding when drafting a new shot's prompt (composition/pacing
  reference) — it is never passed as an image reference into an actual
  fal generation call. Leaking the original reference video's specific
  character/setting appearance into a new project's generation would
  defeat the entire "characters/environment are the variable" premise.
- No numeric camera-geometry fields, no music fields — see Non-Goals.

## `save-template`

```
ai-film save-template --from <path-to-draft.json> --id hero-orbit [--force]
```

- `--from` points at a file the agent has already written (typically
  under the calling project's own directory, e.g.
  `assets/reference-video/template_draft.json`) containing the
  generalized `shot_patterns` array and top-level `name`/`source_note`.
- Validates the draft against the schema above (add
  `TEMPLATE_SCHEMA` to `schema.py`, `jsonschema` is already a
  dependency).
- Copies it to `templates/<id>/template.json`, and copies any files
  referenced by `reference_keyframe` paths (resolved relative to the
  draft file's own directory) into `templates/<id>/keyframes/`.
- Refuses to overwrite an existing `templates/<id>/` unless `--force` is
  passed — same idempotency convention as every generation command in
  this project.

## `list-templates` / `show-template`

```
ai-film list-templates
# hero-orbit          Hero Orbit Reveal        (3 shot patterns)
# dramatic-walk       Dramatic Walk Entrance   (4 shot patterns)

ai-film show-template --id hero-orbit
# prints the full template.json, pretty-printed
```

Read-only, no project context needed (these enumerate `templates/` at
the repo root, not anything under a specific project). Exists so an
agent can discover and inspect available templates via a normal command
call instead of using Read/Glob on a path it has to guess.

## Applying a template

No new engine command for this — it's a judgment call, made by
`ai-film-director` and `ai-film-storyboard` during their existing
brainstorm/breakdown steps, not a deterministic transform:

1. If the project's `config.json` names a template — a new optional
   key, a plain string id: `"template": "hero-orbit"`, set during
   `/ai-film-setup` or added later by hand — the agent runs `ai-film
   show-template --id <id>` before drafting shots. No object wrapper,
   no version pin, no per-project overrides field: this is optional
   context a project points at, not a dependency it locks to. If a
   richer shape (overrides, version pinning) is ever needed, that's a
   migration to do then, on real demand, not speculatively now.
2. **`shot_patterns` describes the reference's sequence structure as an
   example, not a fixed shot count every consuming project must match.**
   The agent maps its own scene/shot breakdown to the template's
   `shot_patterns` **in order**. If the story naturally produces the
   same number of shots as the template has patterns, this is a direct
   1:1 seed: shot N's `camera`/`action`/description draft starts from
   `shot_patterns[N]`'s `camera`/`subject_motion`/`framing`, rewritten
   to name the project's actual characters and environment in place of
   the template's generic subject language.
3. If counts differ, the agent uses judgment to compress or expand —
   e.g. two consecutive template patterns can seed one longer shot, or
   one pattern can be split across two shots if the story needs more
   beats there. This is explicitly not specified further here (see
   Non-Goals) — it's the same kind of creative judgment the director
   agent already exercises when breaking a scene into shots without any
   template at all.
4. However the shots come out, they still go through the existing
   storyboard candidate loop and human approval before any image or
   video generation is triggered. A template shot draft is a better
   starting point for that review, not a bypass of it.

### Precedence

A template is a default creative constraint, not an absolute one.
When the story or the human's explicit direction conflicts with what a
template pattern suggests, precedence is:

```
explicit user/story requirement  >  template  >  agent default
```

Example: `shot_patterns[1]` says "subject locked to frame center
throughout," but the story for this shot is "the hero gets knocked to
the left side of frame." The story wins — the agent drafts the shot
off-center and does not force the template's framing. A template that
couldn't be overridden this way would turn from "saves you from writing
prompts" into "a new constraint you have to fight," which defeats its
purpose.

## Agent changes

- **`ai-film-reference-analyst`** (Spec 1): after the human approves the
  enriched brief, ask once: "Save this as a reusable template for future
  projects?" If yes, generalize each scene's `subject`/`description`
  into pattern-focused `camera`/`subject_motion`/`framing` text (drop
  identity specifics), write the draft JSON, and run `save-template`.
  Skippable — most single-use analyses won't become templates.
- **`ai-film-director`** / **`ai-film-storyboard`**: when
  `config.json` names a template, read it via `show-template` and use
  it as described in "Applying a template" above. No template named →
  behave exactly as today. Purely additive, same as Spec 1's
  integration point.
- **`ai-film-setup`**: gains an optional prompt — "Use a saved template
  for this project's shots? (`list-templates` to see options)" — writing
  the choice into `config.json`. Skippable; defaults to none.

## Asset staleness tracking

The real motivation for this section: change a locked character's
reference image, and today nothing tells you which already-generated
shots were built against the old one. This is the concrete substance
behind "fork a project and know what needs regenerating" — not a fork
mechanism itself (forking a project is just copying its directory,
already possible with no new engine code), but the piece that makes a
fork (or any in-place asset change) actionable instead of a guess.

**Mechanism:** `_image_references`-style resolution (the existing helper
that gathers `characters[].reference`/`environment.reference` paths for
an image generation call) additionally computes each resolved path's
`sha256` at generation time and records it in that shot's
`generation.image` record under a new `source_assets` key:

```json
"generation": {
  "image": {
    "status": "completed",
    "source_assets": [
      {"path": "assets/characters/mara/reference.png", "sha256": "3f2a..."},
      {"path": "assets/environments/snow_mountain/reference.png", "sha256": "9b1c..."}
    ]
  }
}
```

No new version-numbering scheme — a content hash needs no separate
"current version" pointer to maintain. Note: `shot.json`'s existing
`artifact.sha256` field is schema-reserved but currently always written
as `None` in practice (`_image_artifact`/`_video_or_audio_artifact` in
`generation_service.py` never compute it) — this feature is the first
thing to actually populate a sha256 field in this data model, not a
reuse of an already-working mechanism. The schema already having a slot
for it is still worth noting: content-hash identity is a natural fit
here, not a foreign concept being bolted on.

**`ai-film check-stale --path <project>`** — read-only, walks every shot
with a completed image generation, recomputes the *current* `sha256` of
each `source_assets` path, and reports any mismatch:

```
ai-film check-stale --path <project>
2 shots reference a changed asset:

S01_SH01
  assets/characters/mara/reference.png changed since generation

S01_SH03
  assets/characters/mara/reference.png changed since generation
```

**Non-goals for this addition**, to keep it proportionate to what it's
actually for:

- **No automatic regeneration.** `check-stale` only reports — never
  queues or triggers work itself. Matches this project's standing
  human-approval-before-spend philosophy; a report is not a command.
- **No diff of *what* changed about the asset**, just changed-or-not. A
  boolean signal is what "should I look at this shot again" needs; a
  full diff view is a speculative feature nothing downstream asks for.
- **Scoped to `generation.image` only in v1.** That's the stage that
  directly consumes `characters[]`/`environment` references. Video/voice/
  sfx/music generation typically build on the already-provenance-tracked
  storyboard image rather than the raw character reference a second
  time — extend to those stages later if a real need shows up, not
  speculatively now.

## Sharing a template (export/import)

A template already lives outside any single project
(`templates/<id>/`), so "share it with someone else" is almost already
just "send them that directory" — these two commands exist to make that
convenient and to validate what's received, not to add a new mechanism:

```
ai-film export-template --id hero-orbit --output hero-orbit.zip
ai-film import-template --from hero-orbit.zip [--id <override-id>] [--force]
```

- `export-template` zips `templates/<id>/` (its `template.json` and
  `keyframes/`) as-is — no transformation, no metadata stripped or
  added.
- `import-template` extracts into `templates/<id>/` (or `--id`'s
  override, if the recipient wants a different local name) and
  validates the extracted `template.json` against the same
  `TEMPLATE_SCHEMA` `save-template` already uses — a corrupted or
  hand-edited-into-invalidity archive is rejected before it lands, not
  after. Same `--force`-gated overwrite protection as `save-template`.

**Non-goals**, explicitly, to head off scope creep back toward the
Production Graph / hosted-sharing ideas this spec's design conversation
raised and set aside:

- **No hosted registry, gallery, or discovery surface.** This is a file
  you hand someone, same as email-ing a zip — consistent with the
  existing Non-Goals row ruling out a cloud/shared template registry.
- **No bundling of characters, environments, or whole projects in this
  spec.** Those are a different asset class living inside a project
  directory, not `templates/`'s project-independent home. The identical
  export/import *mechanism* would apply to them, but scoping it now
  would mean designing three asset classes' worth of edge cases instead
  of one — a natural, small follow-on once this pattern has shipped and
  proven itself for templates.
- **The recipient still needs their own working `ai-film-studio` install
  and their own `FAL_KEY`** to do anything with an imported template
  beyond inspecting it. This shares a creative recipe, not compute or a
  hosted environment.

## Testing

- `save-template` / `list-templates` / `show-template`: standard CLI
  tests — schema validation rejects a malformed draft, `--force`
  behavior, keyframe file copying, listing output format.
- Schema tests for `TEMPLATE_SCHEMA` in `test_schema.py`, following the
  existing pattern for `SHOT_SCHEMA`.
- No integration test involving actual template *application* by an
  agent — that logic lives in the `.md` skill files, which this
  project's test suite does not execute (consistent with how
  `ai-film-director`/`ai-film-storyboard`'s existing brainstorm logic
  isn't unit-tested today either).
- `check-stale`: a shot with unchanged `source_assets` reports clean; a
  shot whose recorded hash no longer matches the file on disk is
  reported stale; a shot with no `source_assets` at all (generated before
  this feature existed, or never completed image generation) is silently
  skipped, not reported as an error.
- `export-template`/`import-template`: round-trip test (export then
  import into a fresh location reproduces the same `template.json` and
  keyframe files byte-for-byte); import rejects a zip whose
  `template.json` fails `TEMPLATE_SCHEMA` validation; `--force`
  behavior matches `save-template`'s.

## Open Items For The Plan

- `config.json`'s `"template"` key shape is decided (plain string, see
  "Applying a template" above) — no longer an open item.
- Whether `list-templates` should error or print "no templates yet" on
  an empty `templates/` directory — trivial UX detail, not a design
  decision.
