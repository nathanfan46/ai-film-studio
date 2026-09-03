# Motion-Transfer (Pose-Guided) Video Generation — Design

## Problem

Text-to-video models routinely collapse precise, counted, repetitive
choreography ("three quick rhythmic knock-fist pulses near the temple,
then open into a pointing gesture") into a single generic approximation.
Text-prompt description is the wrong tool for "reproduce this exact dance
move" — this has already cost at least one wasted `generate-video` call in
production use of this project, with more likely as shot count grows.

fal.ai hosts motion-transfer models built for exactly this: given a
driving video (the real reference motion) and a character reference
image, they retarget the driving video's motion onto that character.

## Goals

- Let a shot opt into motion-transfer as its video-generation method,
  instead of the normal prompt-driven `generate-video` path, when a human
  has supplied a real reference clip for that shot's choreography.
- Reuse this project's existing artifact/versioning/cost-gate machinery
  rather than inventing a parallel one — the capability layer
  (`MOTION_TRANSFER`) stays orthogonal to the media-artifact layer
  (`generation.video`).
- Keep the production-format contract (target resolution/fps,
  `strict_format`) a single, shared policy — motion-transfer must not
  introduce a second, competing notion of "the right format."
- Never silently bake an unwanted audio track into a video artifact — this
  project's audio (dialogue/sfx/music) is always attached deliberately,
  later, via `generate-lipsync`/`mux-audio`.

## Non-goals (v1)

- **No multi-candidate/variant generation for motion-transfer.** A human
  asked about this directly during design: getting several seed variants
  of the same driving-video+reference-image pair to pick from, the way
  `generate-candidates`/`review`/`select-candidate` already works for
  images. That's a real, buildable follow-up, not part of this design —
  ship the single-generation path first and confirm it actually reproduces
  reference motion well before building a picker around it. It would also
  need `review_gallery.py` to grow a `<video>` rendering path (it only
  renders `<img>` tags today) — noted here as a forward-pointer so
  whoever builds that follow-up isn't surprised by the gap, not something
  this design touches.
- **No support for `fal-ai/wan-motion`.** Two real motion-transfer models
  were evaluated (see the provider table below); `kling-video/v2.6/
  standard/motion-control` was chosen for its explicit `character_orientation`
  control, documented as better suited to complex motion. Adding
  `wan-motion` later is cheap once this pattern exists (same
  `MODEL_TO_APP_ID`-dict trick already used for the `video` capability) —
  not designed here.
- **No driving-video reformatting.** Only the character reference image
  is resized toward the target aspect ratio before upload (see "Format
  contract," below) — the driving video is uploaded as-is. It's a motion
  source, not an appearance/framing source, and there's no established
  precedent in this codebase for transforming one.

## Provider capability (verified against fal's real OpenAPI schema)

`fal-ai/kling-video/v2.6/standard/motion-control` — $0.07/second.

| Field | Required | Notes |
|---|---|---|
| `image_url` | yes | "Reference image URL. The characters, backgrounds, and other elements in the generated video are based on this reference image." Max 10MB, 340-3850px, aspect ratio 0.40-2.50. |
| `video_url` | yes | "Reference video URL. The character actions in the generated video will be consistent with this reference video." Max 100MB, 340-3850px, 3-30.05s duration. |
| `character_orientation` | yes | `"video"` (orientation follows the driving video — "better for complex motions," max 30s) or `"image"` (follows the reference image / camera moves, max 10s). This design always sends `"video"`. |
| `prompt` | no | Up to 2500 chars. Optional; this design leaves it empty unless a future caller supplies one. |
| `keep_original_sound` | no, default `true` | Whether the output keeps the driving video's own audio. **This design always sends `false`** — see "Audio" below. |

Output: `video` (a File object — `url`/`content_type`/`file_name`/`file_size`), same shape `FalVideoProvider.get_result()` already parses via `body["video"]["url"]`.

## Design

### 1. Same artifact slot as `generate-video`

```
MOTION_TRANSFER (capability)
       ↓
generation.video.artifact   ← same slot generate-video writes
       ↓
render / generate-lipsync / mux-audio / media-review
```

Not a separate `generation.motion_transfer` stage. Every downstream
consumer already only reads `generation.video.artifact` — this needs zero
changes to `render.py`, `generate-lipsync`, `mux-audio`, or media review.
This was the strongest point of the design going in and stays unchanged.

`generate-motion-transfer` reuses `run_generation_stage(stage="video",
scope="storyboard", ...)` verbatim — the same versioning/history/archive
bookkeeping every other video regeneration already gets. Idempotency
matches `generate-video`, not `generate-lipsync`: an existing completed
artifact is skipped unless `--force` is passed (`generate-lipsync`'s
always-supersede semantics don't apply here — motion-transfer is a primary
generation method for the shot, not a refinement pass over an existing
clip).

### 2. Driving character selection

`shot.json`'s `characters` array has no existing ordering contract in the
schema (`"characters": {"type": "array"}` — no semantic "primary
character" concept exists anywhere in this codebase today; confirmed by
reading `schema.py` and every existing reference-gathering function).
Rather than inventing implicit meaning for array order, this design states
it explicitly, scoped to motion-transfer only:

> **`shot.characters[0]` is the driving character for motion-transfer.**
> It must have a locked reference image, using the same field every other
> reference-image consumer in this codebase already reads:
> `shot.characters[0].reference` (a project-root-relative path, exactly
> like `_character_and_environment_references` in `cli.py` already
> resolves every other character/environment reference — see `path /
> c["reference"]`). This is a motion-transfer-specific rule, not a
> schema-wide "first character is primary" claim — the general
> `characters` array remains unordered everywhere else in the codebase.

`generate-motion-transfer` fails clearly, before any spend, if
`characters` is empty or `characters[0].reference` is unset — same
fail-fast-before-the-cost-gate posture `generate-lipsync` already has for
its own prerequisites.

### 3. `driving_video` shot field and path resolution

```json
"driving_video": {
  "path": "05_video/reference_clips/dance.mp4"
}
```

Optional top-level object, sibling to `characters`/`environment`, added to
`SHOT_SCHEMA`. **`path` is project-root-relative**, identical to every
other artifact/reference path convention in this codebase (`characters[i]
.reference`, `environment.reference`, `generation.video.artifact.path`
—none of these are shot.json-relative or CLI-cwd-relative, and
`driving_video.path` doesn't become the exception). Resolved the same way
`_character_and_environment_references` resolves references: `path /
shot_data["driving_video"]["path"]`.

`generate-motion-transfer` validates the resolved file **exists on disk
before the cost/approval gate is checked** — a missing driving-video file
is a free, instant, pre-spend failure, not something discovered after
`approve-generation` already ran or (worse) after a paid `submit()` call.

### 4. Local paths → provider URLs: no new upload logic

`FalMotionTransferProvider.submit()` calls the exact same shared helper
every other fal provider in this codebase already uses —
`client.upload_file(path: str) -> str` (`providers/fal/client.py`). Both
`image_url` and `video_url` are produced by two `client.upload_file(...)`
calls. No new upload/URL-conversion code is introduced anywhere.

### 5. Format contract: one policy, not two

**Motion-transfer resolves the shot's target format through the exact
same helper `generate-video` uses (`_resolve_target_format` in `cli.py`),
and validates its result through the exact same post-generation helper
(`_apply_video_format_validation` in `generation_service.py`) — this is a
hard invariant, not a suggestion:**

> Motion transfer MUST resolve the standard video format contract through
> the same helper used by `generate-video`; provider-specific generation
> parameters MUST NOT introduce a second format policy.

Concretely:

- **Request-time influence:** kling-motion-control's schema has no
  `resolution`/`aspect_ratio` field at all — same situation this project
  already solved once for `h3-max`/the `hailuo` family in
  `providers/fal/video.py`: the only lever is the reference image's own
  aspect ratio (`image_url`'s docs: "characters, backgrounds... are based
  on this reference image" — plausibly, though not as explicitly
  documented as h3-max's "output aspect ratio follows this image," the
  aspect ratio too). This design resizes the reference image toward the
  shot's target aspect ratio before upload by **importing and reusing
  `_resize_reference_for_target` from `providers/fal/video.py` directly**
  (`from ai_film.providers.fal.video import _resize_reference_for_target`)
  rather than duplicating it — that function invokes real ffmpeg
  subprocess logic, past the threshold where this codebase's "small
  helper duplicated per module" convention applies (that convention is
  for two-line ffprobe/ratio-math wrappers, not a real
  temp-file-and-subprocess function). Resizing is applied unconditionally
  when a target was resolved, mirroring `MODELS_REQUIRING_RESIZED_REFERENCE`'s
  existing gate — it's safe even if kling's output turns out not to
  strictly follow the image's aspect ratio, the same "can't hurt, might
  help" reasoning already applied to `hailuo`/`h3-max`.
- **Post-generation validation:** identical to `generate-video` — probe
  the actual artifact, record `requested_format`/`actual_format` always,
  and gate only on aspect-ratio deviation beyond the same 2% tolerance,
  only when `strict_format=true`. Resolution/fps differences never block
  generation in either mode, for the same reason they don't for
  `generate-video`: no provider this project uses can hit an exact target
  pixel size, and `render.py`'s unconditional normalization is what
  actually enforces the final exact size — motion-transfer doesn't get
  its own normalization step, it flows through the same one every other
  shot's video does.
- **Open, unverified assumption (flagged, not resolved):** whether
  kling-motion-control's output aspect ratio actually follows `image_url`
  as closely as h3-max's does is not explicitly documented the way
  h3-max's is — this needs a real generation to confirm, the same
  "flagged, not resolved" posture the render-format-contract spec already
  took for `_resize_reference_for_target`'s general effectiveness.

### 6. Audio: `keep_original_sound=false` is an invariant, not a config option

`FalMotionTransferProvider.submit()` always sends
`"keep_original_sound": false` — hardcoded, never a caller-supplied
parameter. This project already has a dedicated, deliberate place audio
gets attached to a shot's video (`generate-lipsync` for dialogue,
`mux-audio` for sfx/off-screen voice); auto-inheriting a stock reference
clip's own soundtrack would introduce a second, accidental audio source
into `generation.video`, creating exactly the kind of downstream ambiguity
(which track is "the" audio for this shot?) this project's whole
audio-mux design was built to avoid.

## Components

- **`models.py`**: `Capability.MOTION_TRANSFER = "motion_transfer"`;
  `MotionTransferRequest` dataclass (`image_path: str`,
  `driving_video_path: str`, `model: str`, `character_orientation: str =
  "video"`, `prompt: str = ""`, `output_path: str = ""`,
  `target_width: int = 0`, `target_height: int = 0`, `target_fps: int =
  0`) — `keep_original_sound` is NOT a field here; it's hardcoded in the
  provider (Section 6). Reuses `VideoGenerationResult` for output — no new
  result type.
- **`providers/fal/motion_transfer.py`**: `FalMotionTransferProvider`;
  `MODEL_TO_APP_ID = {"kling-motion-control":
  "fal-ai/kling-video/v2.6/standard/motion-control"}`; imports
  `_resize_reference_for_target` from `providers/fal/video.py` (Section
  5); a local `_probe_duration`-style helper (this module's own copy,
  matching the established per-module ffprobe-wrapper convention) to
  record the real output duration rather than trusting any static value,
  since the artifact's duration is entirely provider-determined here (no
  `duration_seconds` request field exists on this endpoint at all).
- **`providers/mock/motion_transfer.py`**: `MockMotionTransferProvider`,
  matching every other mock provider's shape (`_submit_calls` counter,
  writes placeholder bytes, no network).
- **`providers/registry.py`**: `Capability.MOTION_TRANSFER: {"fal":
  FalMotionTransferProvider, "mock": MockMotionTransferProvider}`.
- **`providers/fal/catalog.py`**: `ModelInfo("fal", "kling-motion-control",
  Capability.MOTION_TRANSFER, "Kling v2.6 Motion Control (driving-video +
  reference-image -> character performs that motion, $0.07/s)")`.
- **`services/generation_service.py`**: `generate_motion_transfer()`,
  built on `run_generation_stage(stage="video", ...)`, calling
  `_apply_video_format_validation` exactly as `generate_video` does
  (Section 5).
- **`cli.py`**: `generate-motion-transfer --shot <id> [--force]` command.
  No `--driving-video` flag — reads `driving_video.path` from the shot
  (Section 3). Validates driving-video-exists and
  first-character-has-reference before the cost gate (Section 2/3).
- **`schema.py`**: optional `driving_video` object (Section 3).
- **`project.py`**: `DEFAULT_CONFIG["providers"]["motion_transfer"] =
  {"provider": "fal", "model": "kling-motion-control", "parameters": {}}`.
  Existing projects whose `config.json` predates this feature don't have
  this key — `_stage_config` (or a motion-transfer-specific variant of
  it) must fall back to this same default rather than raising `KeyError`,
  the identical forward-compatibility posture the render-format-contract
  work already established for `config.json`'s `render` section.

## Testing

- Provider tests (`tests/providers/test_fal_providers.py`): confirm the
  exact request body — `image_url`, `video_url`, `character_orientation:
  "video"`, `keep_original_sound: false` (always, never overridable from
  the request object), optional `prompt` only when non-empty. A real-ffmpeg
  test confirming the reused `_resize_reference_for_target` import
  actually produces the target's exact dimensions when a target is
  resolved (this is the same function Task 4 of the render-format-contract
  plan already tested directly — this test confirms the *import*, not the
  function's own correctness a second time).
- Service tests (`tests/services/test_generation_service.py`):
  idempotency — **explicitly assert the provider's submit is never called
  a second time** (`provider._submit_calls == 0` after a second
  no-`--force` call, the same assertion style
  `test_generate_video_uses_locked_image_over_raw_references`-style tests
  already use) — not just that the output file is unchanged, which could
  pass even if the provider were wastefully re-invoked and its result
  discarded. `--force` regenerates and archives the prior artifact,
  identical to `generate-video`'s own force-regeneration tests.
- CLI tests (`tests/test_cli_generation_commands.py` or a new
  `test_cli_motion_transfer_commands.py`): missing `driving_video` field,
  missing/empty `characters`, missing `characters[0].reference`, driving
  video file doesn't exist on disk (pre-spend failure, provider never
  called), legacy-config fallback (a `config.json` with no
  `providers.motion_transfer` key still works with the documented
  defaults).
- Full suite must stay green throughout (400 passing as of this spec).

## Open risk, flagged not resolved

Whether kling-motion-control's output actually follows the resized
reference image's aspect ratio as closely as h3-max's documented behavior
does is unverified — this needs a real (paid) generation to confirm, not
just schema inspection. Recommend treating the first few real generations
against this model as a deliberate verification pass, the same posture
this project already took for `_resize_reference_for_target`'s general
effectiveness on `h3-max`/`hailuo`.
