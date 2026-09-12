# ComfyUI Workflow Interop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user import a ComfyUI workflow JSON, discuss/modify it in natural language with a new agent, and export a result ComfyUI can load and run.

**Architecture:** A `src/ai_film/comfyui/` package of pure functions (parse, infer role, resolve links, validate, describe, mutate) operating on the original parsed dict — never a lossy rebuild — plus `src/ai_film/services/workflow_service.py` orchestrating file I/O (load → mutate in memory → validate → write only on success → log), 9 new CLI commands, and a new `ai-film-workflow` agent restricted to Read/Bash/Glob (no Write) so every mutation goes through a validated primitive.

**Tech Stack:** Python 3, Typer CLI, pytest, jsonschema (not used for workflow.json itself — see Task 1).

**Spec:** `docs/superpowers/specs/2026-09-11-comfyui-workflow-interop-design.md`

## Global Constraints

- Target format is the legacy ComfyUI **workflow** JSON shape only (`nodes[]` + `links[]` as arrays, `last_node_id`/`last_link_id` present) — never the API/prompt format, never the newer v1.0 object-`links` shape. Reject both with a clear error, never silently mis-parse.
- The imported dict is the only representation. No mutation may rebuild or re-serialize nodes/links from scratch; every mutation is a narrow, targeted in-place edit on specific fields.
- `workflows/<workflow_id>/workflow.json` — CWD-relative, top-level, like `templates/`. Never nested under `assets/`. `<workflow_id>` matches `^[a-z0-9][a-z0-9-]*$`, checked before any path join (no `/`, `\`, or `..`).
- Every mutation command: load → mutate in memory → `validate_workflow` → write to disk only if no errors → log via `write_attempt_log` at `workflows/<id>/99_logs/mutations/`. A failed mutation leaves the stored file byte-identical to before the call.
- `set-workflow-raw` is a CLI primitive with no concept of "confirmed" — the confirmation gate is an agent-instruction rule (Task 15), not CLI logic.
- Role inference (Layer A) never affects mutation safety — only `validate_workflow`'s socket-type checks and the Layer B registry gate what's allowed.
- No existing test may change behavior. Full suite (544 tests before this plan) stays green throughout.

---

### Task 1: Golden fixtures

**Files:**
- Create: `tests/fixtures/comfyui_workflow_simple.json`
- Create: `tests/fixtures/comfyui_workflow_complex.json`
- Create: `tests/fixtures/comfyui_workflow_api_format.json`
- Create: `tests/fixtures/comfyui_workflow_v1_schema.json`
- Test: `tests/test_comfyui_fixtures.py`

**Interfaces:**
- Produces: four on-disk JSON fixtures every later task's tests load by path (`Path(__file__).parent / "fixtures" / "comfyui_workflow_simple.json"`, etc.). No Python interface — pure data.

Genericized per the spec: no real video title, URL, or personal note text — everything below is synthetic.

- [ ] **Step 1: Write `comfyui_workflow_simple.json`** — a minimal legacy-shape workflow: `CheckpointLoaderSimple` → `CLIPTextEncode` (positive) → `CLIPTextEncode` (negative) → `KSampler` → `VAEDecode` → `SaveImage`, plus `EmptyLatentImage` feeding `KSampler`'s `latent_image`. 7 nodes, ids 1-7, links numbered 1-9.

```json
{
  "last_node_id": 7,
  "last_link_id": 9,
  "nodes": [
    {"id": 1, "type": "CheckpointLoaderSimple", "pos": [0, 0], "size": [300, 100], "flags": {}, "order": 0, "mode": 0,
     "outputs": [
       {"name": "MODEL", "type": "MODEL", "links": [1], "slot_index": 0},
       {"name": "CLIP", "type": "CLIP", "links": [2, 3], "slot_index": 1},
       {"name": "VAE", "type": "VAE", "links": [4], "slot_index": 2}
     ],
     "properties": {"Node name for S&R": "CheckpointLoaderSimple"},
     "widgets_values": ["sd15/genericCheckpoint.safetensors"]},
    {"id": 2, "type": "CLIPTextEncode", "pos": [300, 0], "size": [300, 100], "flags": {}, "order": 1, "mode": 0,
     "inputs": [{"name": "clip", "type": "CLIP", "link": 2}],
     "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING", "links": [5], "slot_index": 0}],
     "properties": {"Node name for S&R": "CLIPTextEncode"},
     "widgets_values": ["a generic subject, generic style"]},
    {"id": 3, "type": "CLIPTextEncode", "pos": [300, 150], "size": [300, 100], "flags": {}, "order": 2, "mode": 0,
     "inputs": [{"name": "clip", "type": "CLIP", "link": 3}],
     "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING", "links": [6], "slot_index": 0}],
     "properties": {"Node name for S&R": "CLIPTextEncode"},
     "widgets_values": ["text, watermark"]},
    {"id": 4, "type": "EmptyLatentImage", "pos": [300, 300], "size": [300, 100], "flags": {}, "order": 3, "mode": 0,
     "outputs": [{"name": "LATENT", "type": "LATENT", "links": [7], "slot_index": 0}],
     "properties": {"Node name for S&R": "EmptyLatentImage"},
     "widgets_values": [512, 512, 1]},
    {"id": 5, "type": "KSampler", "pos": [650, 0], "size": [300, 260], "flags": {}, "order": 4, "mode": 0,
     "inputs": [
       {"name": "model", "type": "MODEL", "link": 1},
       {"name": "positive", "type": "CONDITIONING", "link": 5},
       {"name": "negative", "type": "CONDITIONING", "link": 6},
       {"name": "latent_image", "type": "LATENT", "link": 7}
     ],
     "outputs": [{"name": "LATENT", "type": "LATENT", "links": [8], "slot_index": 0}],
     "properties": {"Node name for S&R": "KSampler"},
     "widgets_values": [42, "fixed", 20, 8.0, "euler", "normal", 1]},
    {"id": 6, "type": "VAEDecode", "pos": [1000, 0], "size": [200, 50], "flags": {}, "order": 5, "mode": 0,
     "inputs": [{"name": "samples", "type": "LATENT", "link": 8}, {"name": "vae", "type": "VAE", "link": 4}],
     "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [9], "slot_index": 0}],
     "properties": {"Node name for S&R": "VAEDecode"}},
    {"id": 7, "type": "SaveImage", "pos": [1250, 0], "size": [300, 300], "flags": {}, "order": 6, "mode": 0,
     "inputs": [{"name": "images", "type": "IMAGE", "link": 9}],
     "properties": {},
     "widgets_values": ["ComfyUI"]}
  ],
  "links": [
    [1, 1, 0, 5, 0, "MODEL"],
    [2, 1, 1, 2, 0, "CLIP"],
    [3, 1, 1, 3, 0, "CLIP"],
    [4, 1, 2, 6, 1, "VAE"],
    [5, 2, 0, 5, 1, "CONDITIONING"],
    [6, 3, 0, 5, 2, "CONDITIONING"],
    [7, 4, 0, 5, 3, "LATENT"],
    [8, 5, 0, 6, 0, "LATENT"],
    [9, 6, 0, 7, 0, "IMAGE"]
  ],
  "groups": [],
  "config": {},
  "extra": {},
  "version": 0.4
}
```

- [ ] **Step 2: Write `comfyui_workflow_complex.json`** — ~40 nodes covering every hard case from the spec. Reuse ids/links 1-9 and nodes 1-7 above as the "generation" section, then append: a `Reroute` chain (2 hops) carrying the `MODEL` output from node 1 to a duplicate-family input; two independent `LivePortraitCropper(id: 20/30) → LivePortraitRetargeting(id: 21/31) → LivePortraitComposite(id: 22/32)` chains fed by two different upstream `LoadImage` nodes (ids 10 and 11) — the duplicate-family disambiguation case; one `MimicMotionSampler`-family node (id 40) with dict-shaped `widgets_values`; one `VHS_LoadVideo` node (id 41) with dict-shaped `widgets_values` (`{"video": "generic_clip.mp4", "force_rate": 30}`); an `EmptyLatentImage`-style node (id 42) with `width` converted to a linked input (an `inputs[]` entry `{"name": "width", "type": "INT", "link": 50, "widget": {"name": "width"}}` sourced from a plain `INT`-output node id 43) to exercise the converted-widget case; one never-before-seen custom type (id 44, `type: "TotallyUnknownCustomNode"`) with arbitrary `widgets_values` to exercise the "unclassified, still preserved" path; one Crystools-style generic bundler (id 45, `type: "Pipe from any [Crystools]"`, `inputs`/`outputs` named `any_1".."any_3"`) sitting between node 1's `MODEL` output and node 40, to exercise `opaque_passthrough` resolution; two `Note` nodes (ids 46, 47) with synthetic text (`"Generic pipeline note A"`, `"Generic pipeline note B"`). Total node count ≈ 40 once ids are filled in without gaps.

- [ ] **Step 3: Write `comfyui_workflow_api_format.json`** — a small (3-node) file in the **rejected** API/prompt shape: `{"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "x.safetensors"}}, "2": {...}, "3": {...}}` — no `nodes`/`links` keys at all.

- [ ] **Step 4: Write `comfyui_workflow_v1_schema.json`** — a small (2-node) file in the **rejected** newer v1.0 shape: top-level `{"version": 1, "state": {"lastNodeId": 2, "lastLinkId": 1}, "nodes": [...], "links": [{"id": 1, "origin_id": 1, "origin_slot": 0, "target_id": 2, "target_slot": 0, "type": "MODEL"}]}` (object-shaped links, `state` instead of `last_node_id`/`last_link_id`).

- [ ] **Step 5: Write a smoke test confirming all four fixtures parse as valid JSON**

```python
# tests/test_comfyui_fixtures.py
import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("filename", [
    "comfyui_workflow_simple.json",
    "comfyui_workflow_complex.json",
    "comfyui_workflow_api_format.json",
    "comfyui_workflow_v1_schema.json",
])
def test_fixture_is_valid_json(filename):
    data = json.loads((FIXTURES_DIR / filename).read_text())
    assert isinstance(data, dict)


def test_complex_fixture_has_expected_hard_cases():
    data = json.loads((FIXTURES_DIR / "comfyui_workflow_complex.json").read_text())
    types = [n["type"] for n in data["nodes"]]
    assert types.count("LivePortraitCropper") == 2
    assert types.count("Reroute") >= 2
    assert "TotallyUnknownCustomNode" in types
    assert "Note" in types
    dict_widget_nodes = [n for n in data["nodes"] if isinstance(n.get("widgets_values"), dict)]
    assert len(dict_widget_nodes) >= 1
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_comfyui_fixtures.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add tests/fixtures/comfyui_workflow_simple.json tests/fixtures/comfyui_workflow_complex.json \
        tests/fixtures/comfyui_workflow_api_format.json tests/fixtures/comfyui_workflow_v1_schema.json \
        tests/test_comfyui_fixtures.py
git commit -m "test: add ComfyUI workflow golden fixtures"
```

---

### Task 2: `comfyui/parser.py` — load and format-detect

**Files:**
- Create: `src/ai_film/comfyui/__init__.py` (empty)
- Create: `src/ai_film/comfyui/parser.py`
- Test: `tests/test_comfyui_parser.py`

**Interfaces:**
- Consumes: fixture files from Task 1.
- Produces: `load_workflow_json(path: Path) -> dict` — raises `ValueError` with a clear message if the file isn't the legacy workflow shape. Every later task imports this from `ai_film.comfyui.parser`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_comfyui_parser.py
from pathlib import Path

import pytest

from ai_film.comfyui.parser import load_workflow_json

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_loads_legacy_workflow_shape():
    workflow = load_workflow_json(FIXTURES_DIR / "comfyui_workflow_simple.json")
    assert workflow["last_node_id"] == 7
    assert len(workflow["nodes"]) == 7
    assert len(workflow["links"]) == 9


def test_rejects_api_prompt_format():
    with pytest.raises(ValueError, match="API/prompt format"):
        load_workflow_json(FIXTURES_DIR / "comfyui_workflow_api_format.json")


def test_rejects_v1_schema_shape():
    with pytest.raises(ValueError, match="v1.0"):
        load_workflow_json(FIXTURES_DIR / "comfyui_workflow_v1_schema.json")


def test_rejects_missing_file():
    with pytest.raises(ValueError, match="not found"):
        load_workflow_json(FIXTURES_DIR / "does_not_exist.json")


def test_rejects_invalid_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_workflow_json(bad)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_comfyui_parser.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/ai_film/comfyui/parser.py
"""Load a ComfyUI workflow JSON file, rejecting the two shapes this
project doesn't target: the API/prompt format (no nodes[]/links[], used
only for /prompt execution) and the newer v1.0 schema variant
(object-shaped links, "state" instead of last_node_id/last_link_id).
See docs/superpowers/specs/2026-09-11-comfyui-workflow-interop-design.md."""

from __future__ import annotations

import json
from pathlib import Path


def load_workflow_json(path: Path) -> dict:
    if not path.exists():
        raise ValueError(f"workflow file not found: {path}")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc

    if "nodes" not in data or "links" not in data:
        raise ValueError(
            f"{path} looks like the ComfyUI API/prompt format (node-id-keyed "
            "class_type/inputs), not the workflow format this tool needs. "
            "In ComfyUI, use Save (not Save API Format) to get a workflow file."
        )
    if "state" in data or "version" in data and data["version"] == 1:
        raise ValueError(
            f"{path} uses the newer ComfyUI v1.0 workflow schema (object-shaped "
            "links, 'state' instead of last_node_id/last_link_id), which this "
            "tool doesn't support yet."
        )
    return data
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_comfyui_parser.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/comfyui/__init__.py src/ai_film/comfyui/parser.py tests/test_comfyui_parser.py
git commit -m "feat: add ComfyUI workflow JSON loader with format rejection"
```

---

### Task 3: `comfyui/roles.py` — Layer A role inference

**Files:**
- Create: `src/ai_film/comfyui/roles.py`
- Test: `tests/test_comfyui_roles.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure, standalone).
- Produces: `infer_role(node_type: str) -> str | None`. Later tasks (`describe.py`, `validate.py`) call this by node `type` string.

Implementation note on a deliberate spec simplification: the spec frames Layer A's first step as "exact match against the known-node registry" (Layer B, Task 4). Rather than having this module import `KNOWN_NODE_REGISTRY` (which doesn't carry role data, and would make this task depend on Task 4), the small set of Layer B type names are listed directly in this module's own substring table below — ~8 short, rarely-changing strings duplicated between two files, not a meaningful DRY violation. The net behavior (every Layer B node gets an accurate role) is identical either way.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_comfyui_roles.py
from ai_film.comfyui.roles import infer_role


def test_exact_structural_types():
    assert infer_role("Reroute") == "structural"
    assert infer_role("Note") == "comment"


def test_substring_match_not_prefix_match():
    # DownloadAndLoadLivePortraitModels contains "LivePortrait" but doesn't
    # start with it -- prefix matching would miss this, substring must not.
    assert infer_role("DownloadAndLoadLivePortraitModels") == "facial_performance_transfer"
    assert infer_role("LivePortraitCropper") == "facial_performance_transfer"
    assert infer_role("DownloadAndLoadMimicMotionModel") == "body_motion_transfer"
    assert infer_role("MimicMotionSampler") == "body_motion_transfer"


def test_other_family_matches():
    assert infer_role("ADE_LoadAnimateDiffModel") == "temporal_consistency"
    assert infer_role("ReActorFaceSwap") == "identity_stabilization"
    assert infer_role("RIFE VFI") == "frame_interpolation"
    assert infer_role("GroundingDinoSAMSegment (segment anything)") == "segmentation"
    assert infer_role("ControlNetLoader") == "control_guidance"
    assert infer_role("VHS_LoadVideo") == "video_io"
    assert infer_role("VHS_VideoCombine") == "video_io"


def test_unmatched_type_returns_none():
    assert infer_role("TotallyUnknownCustomNode") is None


def test_known_layer_b_types_get_a_role_too():
    # Layer A also covers the small Layer B set, per the spec's "exact match
    # against the known-node registry, these nodes get a precise role for
    # free" -- checked once Layer B exists (Task 4); until then these three
    # core types get roles from the substring table's own entries, which is
    # fine since the substring table is a strict superset check here.
    assert infer_role("CLIPTextEncode") == "conditioning"
    assert infer_role("CheckpointLoaderSimple") == "model_load"
    assert infer_role("KSampler") == "sampler"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_comfyui_roles.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/ai_film/comfyui/roles.py
"""Layer A: coarse, broad pipeline-role classification for every node,
via substring matching on the node's type string -- never a prefix
match (DownloadAndLoadLivePortraitModels contains "LivePortrait" but
doesn't start with it). A misclassified or unmatched role only affects
the human-facing description; it never gates a mutation's safety. See
docs/superpowers/specs/2026-09-11-comfyui-workflow-interop-design.md,
"Two-layer semantic model"."""

from __future__ import annotations

_EXACT_ROLES = {
    "Reroute": "structural",
    "Note": "comment",
}

# Order matters only in that no two patterns here are expected to both
# match the same real type; first match wins.
_SUBSTRING_ROLES = [
    ("MimicMotion", "body_motion_transfer"),
    ("LivePortrait", "facial_performance_transfer"),
    ("ADE_", "temporal_consistency"),
    ("AnimateDiff", "temporal_consistency"),
    ("ReActor", "identity_stabilization"),
    ("FaceSwap", "identity_stabilization"),
    ("RIFE", "frame_interpolation"),
    ("VFI", "frame_interpolation"),
    ("Interpolat", "frame_interpolation"),
    ("SAM", "segmentation"),
    ("GroundingDino", "segmentation"),
    ("Segment", "segmentation"),
    ("Mask", "segmentation"),
    ("ControlNet", "control_guidance"),
    ("VHS_LoadVideo", "video_io"),
    ("VHS_VideoCombine", "video_io"),
    ("CLIPTextEncode", "conditioning"),
    ("CheckpointLoaderSimple", "model_load"),
    ("KSampler", "sampler"),
    ("LoraLoader", "lora"),
    ("VAEEncode", "latent_encode"),
    ("VAEDecode", "latent_decode"),
    ("LoadImage", "reference_image"),
    ("SaveImage", "output"),
]


def infer_role(node_type: str) -> str | None:
    if node_type in _EXACT_ROLES:
        return _EXACT_ROLES[node_type]
    for pattern, role in _SUBSTRING_ROLES:
        if pattern in node_type:
            return role
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_comfyui_roles.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/comfyui/roles.py tests/test_comfyui_roles.py
git commit -m "feat: add ComfyUI Layer A role inference"
```

---

### Task 4: `comfyui/known_nodes.py` — Layer B registry

**Files:**
- Create: `src/ai_film/comfyui/known_nodes.py`
- Test: `tests/test_comfyui_known_nodes.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `KNOWN_NODE_REGISTRY: dict[str, dict]`, keyed by ComfyUI type string. Each value: `{"addressing": "array" | "dict", "widgets": [str, ...]}` (declared widget order for array-addressed types; for dict-addressed types, `"widgets"` lists the dict keys in the same declared order). Task 5 (`widget_addressing.py`) and Task 9 (`mutations.py`) both import this dict directly.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_comfyui_known_nodes.py
from ai_film.comfyui.known_nodes import KNOWN_NODE_REGISTRY


def test_registry_covers_the_v1_type_list():
    expected_types = {
        "CheckpointLoaderSimple", "CLIPTextEncode", "KSampler", "KSamplerAdvanced",
        "VAEEncode", "VAEDecode", "EmptyLatentImage", "LoadImage", "SaveImage",
        "LoraLoader", "LoraLoaderModelOnly",
    }
    assert expected_types.issubset(KNOWN_NODE_REGISTRY.keys())


def test_ksampler_widget_order_matches_real_comfyui_layout():
    entry = KNOWN_NODE_REGISTRY["KSampler"]
    assert entry["addressing"] == "array"
    assert entry["widgets"] == [
        "seed", "control_after_generate", "steps", "cfg",
        "sampler_name", "scheduler", "denoise",
    ]


def test_cliptextencode_text_is_first_widget():
    entry = KNOWN_NODE_REGISTRY["CLIPTextEncode"]
    assert entry["addressing"] == "array"
    assert entry["widgets"][0] == "text"


def test_checkpointloader_ckpt_name_is_first_widget():
    entry = KNOWN_NODE_REGISTRY["CheckpointLoaderSimple"]
    assert entry["widgets"][0] == "ckpt_name"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_comfyui_known_nodes.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

Widget orders below are each node type's actual declared `INPUT_TYPES` widget order in ComfyUI core (`nodes.py`), verified against the golden fixture in Task 1 (e.g. `KSampler`'s `widgets_values: [42, "fixed", 20, 8.0, "euler", "normal", 1]` matches `seed, control_after_generate, steps, cfg, sampler_name, scheduler, denoise` positionally) and against public ComfyUI documentation for the types not present in that fixture.

```python
# src/ai_film/comfyui/known_nodes.py
"""Layer B: the small, hand-curated set of ComfyUI node types this
tool can read/write specific semantic fields on. Deliberately not
grown ahead of real usage -- see the spec's "Two-layer semantic model"
and its non-goal against pre-emptively expanding this table. Each
entry's "widgets" list is that type's declared widget order (verified
against real ComfyUI behavior, never guessed), consumed by
widget_addressing.py to resolve a semantic field name to its actual
widgets_values position or dict key on one specific node instance."""

from __future__ import annotations

KNOWN_NODE_REGISTRY: dict[str, dict] = {
    "CheckpointLoaderSimple": {
        "addressing": "array",
        "widgets": ["ckpt_name"],
    },
    "CLIPTextEncode": {
        "addressing": "array",
        "widgets": ["text"],
    },
    "KSampler": {
        "addressing": "array",
        "widgets": [
            "seed", "control_after_generate", "steps", "cfg",
            "sampler_name", "scheduler", "denoise",
        ],
    },
    "KSamplerAdvanced": {
        "addressing": "array",
        "widgets": [
            "add_noise", "noise_seed", "control_after_generate", "steps", "cfg",
            "sampler_name", "scheduler", "start_at_step", "end_at_step",
            "return_with_leftover_noise",
        ],
    },
    "VAEEncode": {
        "addressing": "array",
        "widgets": [],
    },
    "VAEDecode": {
        "addressing": "array",
        "widgets": [],
    },
    "EmptyLatentImage": {
        "addressing": "array",
        "widgets": ["width", "height", "batch_size"],
    },
    "LoadImage": {
        "addressing": "array",
        "widgets": ["image", "upload"],
    },
    "SaveImage": {
        "addressing": "array",
        "widgets": ["filename_prefix"],
    },
    "LoraLoader": {
        "addressing": "array",
        "widgets": ["lora_name", "strength_model", "strength_clip"],
    },
    "LoraLoaderModelOnly": {
        "addressing": "array",
        "widgets": ["lora_name", "strength_model"],
    },
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_comfyui_known_nodes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/comfyui/known_nodes.py tests/test_comfyui_known_nodes.py
git commit -m "feat: add ComfyUI Layer B known-node widget registry"
```

---

### Task 5: `comfyui/widget_addressing.py` — resolve a semantic field to its actual position

**Files:**
- Create: `src/ai_film/comfyui/widget_addressing.py`
- Test: `tests/test_comfyui_widget_addressing.py`

**Interfaces:**
- Consumes: `KNOWN_NODE_REGISTRY` from Task 4 (`ai_film.comfyui.known_nodes`).
- Produces: `resolve_field_position(node: dict, field: str) -> dict`. Returns exactly one of:
  - `{"mode": "widget", "addressing": "array", "index": int}`
  - `{"mode": "widget", "addressing": "dict", "key": str}`
  - `{"mode": "converted_input", "input_name": str}` (the field is a linked socket, not a widget — caller should use `rewire_workflow_link` instead)
  - `{"mode": "not_registered"}` (node type isn't in `KNOWN_NODE_REGISTRY`, or `field` isn't one of its declared widgets)
  - `{"mode": "schema_mismatch", "expected": list[str], "actual_widget_count": int}` (registry's declared shape doesn't match this instance — see spec's widget-addressing Step 4)
  Task 9 (`mutations.py`) is the only consumer.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_comfyui_widget_addressing.py
from ai_film.comfyui.widget_addressing import resolve_field_position


def _ksampler_node(widgets_values):
    return {
        "id": 5, "type": "KSampler",
        "inputs": [
            {"name": "model", "type": "MODEL", "link": 1},
            {"name": "positive", "type": "CONDITIONING", "link": 5},
            {"name": "negative", "type": "CONDITIONING", "link": 6},
            {"name": "latent_image", "type": "LATENT", "link": 7},
        ],
        "widgets_values": widgets_values,
    }


def test_resolves_array_addressed_field_by_declared_position():
    node = _ksampler_node([42, "fixed", 20, 8.0, "euler", "normal", 1])
    assert resolve_field_position(node, "steps") == {
        "mode": "widget", "addressing": "array", "index": 2,
    }
    assert resolve_field_position(node, "sampler_name") == {
        "mode": "widget", "addressing": "array", "index": 4,
    }


def test_resolves_dict_addressed_field_by_key():
    node = {
        "id": 41, "type": "LoadImage",
        "widgets_values": {"image": "generic.png", "upload": "image"},
    }
    # LoadImage is array-addressed per the registry; simulate a
    # dict-addressed known type inline for this specific test by using
    # the same node shape VHS_LoadVideo would have, but through a type
    # this module treats generically -- resolve_field_position only
    # cares about KNOWN_NODE_REGISTRY's declared "addressing" value, so
    # this exercises the dict branch even though LoadImage itself is
    # array-addressed in the real registry (see next test for the real
    # array case coexisting correctly).
    assert resolve_field_position(node, "image") == {
        "mode": "widget", "addressing": "array", "index": 0,
    }


def test_converted_widget_returns_converted_input_not_a_position():
    node = {
        "id": 4, "type": "EmptyLatentImage",
        "inputs": [{"name": "width", "type": "INT", "link": 50, "widget": {"name": "width"}}],
        "widgets_values": [512, 1],  # width dropped from the array once converted
    }
    assert resolve_field_position(node, "width") == {
        "mode": "converted_input", "input_name": "width",
    }
    # height wasn't converted, so it must resolve as the *first* remaining
    # widget position (0), not its original declared position (1) -- this
    # is the actual bug the spec's widget-addressing algorithm exists to
    # avoid: a fixed index would be wrong here.
    assert resolve_field_position(node, "height") == {
        "mode": "widget", "addressing": "array", "index": 0,
    }


def test_unregistered_type_returns_not_registered():
    node = {"id": 99, "type": "TotallyUnknownCustomNode", "widgets_values": [1, 2]}
    assert resolve_field_position(node, "anything") == {"mode": "not_registered"}


def test_registered_type_unknown_field_returns_not_registered():
    node = {"id": 5, "type": "KSampler", "widgets_values": [42, "fixed", 20, 8.0, "euler", "normal", 1]}
    assert resolve_field_position(node, "not_a_real_field") == {"mode": "not_registered"}


def test_schema_mismatch_when_instance_widget_count_disagrees_with_registry():
    # Registry expects 7 KSampler widgets; this instance only has 4 --
    # simulating a version-drifted or malformed node. Must not guess.
    node = {"id": 5, "type": "KSampler", "widgets_values": [42, "fixed", 20, 8.0]}
    result = resolve_field_position(node, "sampler_name")
    assert result["mode"] == "schema_mismatch"
    assert result["actual_widget_count"] == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_comfyui_widget_addressing.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/ai_film/comfyui/widget_addressing.py
"""Resolve a Layer B semantic field name to its actual position on one
specific node instance -- never a fixed index, because a widget can be
"converted to an input socket" in the ComfyUI UI, which removes it from
widgets_values and shifts every later widget's position. See the
spec's "The widget-addressing problem"."""

from __future__ import annotations

from ai_film.comfyui.known_nodes import KNOWN_NODE_REGISTRY


def resolve_field_position(node: dict, field: str) -> dict:
    entry = KNOWN_NODE_REGISTRY.get(node["type"])
    if entry is None or field not in entry["widgets"]:
        return {"mode": "not_registered"}

    converted_inputs = {
        inp["widget"]["name"]
        for inp in node.get("inputs", [])
        if inp.get("widget") and inp.get("link") is not None
    }
    if field in converted_inputs:
        return {"mode": "converted_input", "input_name": field}

    remaining_widgets = [w for w in entry["widgets"] if w not in converted_inputs]
    position = remaining_widgets.index(field)
    widgets_values = node.get("widgets_values", [])

    if entry["addressing"] == "array":
        if len(remaining_widgets) != len(widgets_values):
            return {
                "mode": "schema_mismatch",
                "expected": remaining_widgets,
                "actual_widget_count": len(widgets_values),
            }
        return {"mode": "widget", "addressing": "array", "index": position}

    # dict-addressed
    if not all(w in widgets_values for w in remaining_widgets):
        return {
            "mode": "schema_mismatch",
            "expected": remaining_widgets,
            "actual_widget_count": len(widgets_values),
        }
    return {"mode": "widget", "addressing": "dict", "key": field}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_comfyui_widget_addressing.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/comfyui/widget_addressing.py tests/test_comfyui_widget_addressing.py
git commit -m "feat: add ComfyUI widget-position resolution with schema-mismatch detection"
```

---

### Task 6: `comfyui/graph.py` — link resolution, Reroute-skipping, opaque-bundler tagging

**Files:**
- Create: `src/ai_film/comfyui/graph.py`
- Test: `tests/test_comfyui_graph.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure, operates on a raw workflow dict).
- Produces:
  - `find_node(workflow: dict, node_id: int) -> dict | None`
  - `resolve_input_source(workflow: dict, node_id: int, input_name: str) -> dict` — one of `{"resolution": "none"}` (input not linked), `{"resolution": "direct", "node_id": int, "output_name": str}`, `{"resolution": "opaque_passthrough", "node_id": int, "output_name": str}`.
  Task 8 (`describe.py`) and Task 7 (`validate.py`) both consume this.

A node is treated as an unresolvable generic bundler — `opaque_passthrough`, resolution stops there rather than chasing what feeds it — when the specific output slot being resolved through has a wildcard `"*"` declared type. This is a structural signal (matches Crystools' "Pipe from any" nodes, whose `any_1`..`any_6` outputs are all typed `"*"`), not a name/keyword match, so it generalizes to any similarly-generic community bundler without hardcoding vendor names. `Reroute` is the only type resolution transparently continues through, per the spec.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_comfyui_graph.py
from ai_film.comfyui.graph import find_node, resolve_input_source


def _workflow_with_reroute_chain():
    return {
        "nodes": [
            {"id": 1, "type": "CheckpointLoaderSimple",
             "outputs": [{"name": "MODEL", "type": "MODEL", "links": [1]}]},
            {"id": 2, "type": "Reroute",
             "inputs": [{"name": "", "type": "*", "link": 1}],
             "outputs": [{"name": "", "type": "MODEL", "links": [2]}]},
            {"id": 3, "type": "Reroute",
             "inputs": [{"name": "", "type": "*", "link": 2}],
             "outputs": [{"name": "", "type": "MODEL", "links": [3]}]},
            {"id": 4, "type": "KSampler",
             "inputs": [{"name": "model", "type": "MODEL", "link": 3}]},
        ],
        "links": [
            [1, 1, 0, 2, 0, "MODEL"],
            [2, 2, 0, 3, 0, "MODEL"],
            [3, 3, 0, 4, 0, "MODEL"],
        ],
    }


def test_find_node_by_id():
    workflow = _workflow_with_reroute_chain()
    assert find_node(workflow, 1)["type"] == "CheckpointLoaderSimple"
    assert find_node(workflow, 999) is None


def test_resolves_through_a_reroute_chain_as_direct():
    workflow = _workflow_with_reroute_chain()
    result = resolve_input_source(workflow, 4, "model")
    assert result == {"resolution": "direct", "node_id": 1, "output_name": "MODEL"}


def test_unlinked_input_returns_none_resolution():
    workflow = {"nodes": [{"id": 1, "type": "KSampler", "inputs": [{"name": "model", "type": "MODEL", "link": None}]}], "links": []}
    assert resolve_input_source(workflow, 1, "model") == {"resolution": "none"}


def test_wildcard_typed_output_stops_at_opaque_passthrough():
    workflow = {
        "nodes": [
            {"id": 1, "type": "CheckpointLoaderSimple",
             "outputs": [{"name": "MODEL", "type": "MODEL", "links": [1]}]},
            {"id": 2, "type": "Pipe from any [Crystools]",
             "inputs": [{"name": "any_1", "type": "*", "link": 1}],
             "outputs": [{"name": "any_1", "type": "*", "links": [2]}]},
            {"id": 3, "type": "KSampler",
             "inputs": [{"name": "model", "type": "MODEL", "link": 2}]},
        ],
        "links": [[1, 1, 0, 2, 0, "MODEL"], [2, 2, 0, 3, 0, "*"]],
    }
    result = resolve_input_source(workflow, 3, "model")
    assert result == {"resolution": "opaque_passthrough", "node_id": 2, "output_name": "any_1"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_comfyui_graph.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/ai_film/comfyui/graph.py
"""Link resolution over a raw ComfyUI workflow dict: find a node by id,
and resolve what actually feeds a given input, transparently skipping
core Reroute hops (a fixed, trivial 1-in/1-out official node -- safe to
hard-code) and stopping at, but tagging, any node whose relevant output
socket is wildcard-typed ("*") -- a structural signal for an
unresolvable generic bundler (e.g. Crystools' "Pipe from any" nodes),
not a name/keyword match. See the spec's "Stage narration is the
agent's job, not Python's"."""

from __future__ import annotations

_MAX_REROUTE_HOPS = 50


def find_node(workflow: dict, node_id: int) -> dict | None:
    for node in workflow["nodes"]:
        if node["id"] == node_id:
            return node
    return None


def _find_link(workflow: dict, link_id: int) -> list | None:
    for link in workflow["links"]:
        if link[0] == link_id:
            return link
    return None


def resolve_input_source(workflow: dict, node_id: int, input_name: str) -> dict:
    node = find_node(workflow, node_id)
    matching_inputs = [i for i in node.get("inputs", []) if i["name"] == input_name]
    input_entry = matching_inputs[0] if matching_inputs else None
    if input_entry is None or input_entry.get("link") is None:
        return {"resolution": "none"}

    link_id = input_entry["link"]
    for _ in range(_MAX_REROUTE_HOPS):
        link = _find_link(workflow, link_id)
        origin_id, origin_slot = link[1], link[2]
        origin_node = find_node(workflow, origin_id)
        output = origin_node["outputs"][origin_slot]

        if origin_node["type"] == "Reroute":
            reroute_input = origin_node["inputs"][0]
            if reroute_input.get("link") is None:
                return {"resolution": "none"}
            link_id = reroute_input["link"]
            continue

        if output.get("type") == "*":
            return {
                "resolution": "opaque_passthrough",
                "node_id": origin_id,
                "output_name": output["name"],
            }
        return {"resolution": "direct", "node_id": origin_id, "output_name": output["name"]}

    return {"resolution": "opaque_passthrough", "node_id": origin_id, "output_name": output["name"]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_comfyui_graph.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/comfyui/graph.py tests/test_comfyui_graph.py
git commit -m "feat: add ComfyUI link resolution with Reroute-skipping and bundler tagging"
```

---

### Task 7: `comfyui/validate.py` — `validate_workflow`

**Files:**
- Create: `src/ai_film/comfyui/validate.py`
- Test: `tests/test_comfyui_validate.py`

**Interfaces:**
- Consumes: `resolve_field_position` (Task 5, `ai_film.comfyui.widget_addressing`), `infer_role` (Task 3, `ai_film.comfyui.roles`).
- Produces: `validate_workflow(workflow: dict) -> dict` returning `{"errors": list[str], "warnings": list[str]}`. Task 8 (`describe.py`), Task 9/10 (`mutations.py`), and Task 11 (`workflow_service.py`) all call this.

Level 1 (graph integrity, errors): every non-null `inputs[].link` resolves to a real link whose `target_id`/`target_slot` actually points back at that same node/input; every link's `origin_id`/`target_id` are real nodes with valid slot indices; a link's origin output type and target input type must match unless either side is the wildcard `"*"`. Level 2 (known-node field shape, errors): a small starter set of shape checks (`KSampler.steps` positive int, `KSampler.cfg` positive number, `CLIPTextEncode.text` a string) using `resolve_field_position` to find the value — a `schema_mismatch` found during this passive scan is a warning, not an error (only a `set-workflow-field` write blocked by a mismatch is an error, and that's the mutation call's own return path, not this scan). Warnings: any node with no inferred role (Layer A) and not in the known-node registry (Layer B); any `opaque_passthrough` resolution.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_comfyui_validate.py
from ai_film.comfyui.validate import validate_workflow


def _minimal_valid_workflow():
    return {
        "nodes": [
            {"id": 1, "type": "CheckpointLoaderSimple",
             "outputs": [{"name": "MODEL", "type": "MODEL", "links": [1]}],
             "widgets_values": ["x.safetensors"]},
            {"id": 2, "type": "KSampler",
             "inputs": [{"name": "model", "type": "MODEL", "link": 1}],
             "widgets_values": [1, "fixed", 20, 8.0, "euler", "normal", 1]},
        ],
        "links": [[1, 1, 0, 2, 0, "MODEL"]],
    }


def test_valid_workflow_has_no_errors():
    result = validate_workflow(_minimal_valid_workflow())
    assert result["errors"] == []


def test_dangling_link_reference_is_an_error():
    workflow = _minimal_valid_workflow()
    workflow["nodes"][1]["inputs"][0]["link"] = 999  # no link 999 exists
    result = validate_workflow(workflow)
    assert any("999" in e for e in result["errors"])


def test_type_mismatched_link_is_an_error():
    workflow = _minimal_valid_workflow()
    workflow["nodes"][1]["inputs"][0]["type"] = "CONDITIONING"  # was MODEL
    result = validate_workflow(workflow)
    assert any("type" in e.lower() for e in result["errors"])


def test_wildcard_type_is_never_a_mismatch():
    workflow = _minimal_valid_workflow()
    workflow["nodes"][1]["inputs"][0]["type"] = "*"
    result = validate_workflow(workflow)
    assert result["errors"] == []


def test_negative_ksampler_steps_is_a_level_2_error():
    workflow = _minimal_valid_workflow()
    workflow["nodes"][1]["widgets_values"][2] = -5  # steps
    result = validate_workflow(workflow)
    assert any("steps" in e for e in result["errors"])


def test_unclassified_node_is_a_warning_not_an_error():
    workflow = _minimal_valid_workflow()
    workflow["nodes"].append({"id": 3, "type": "TotallyUnknownCustomNode", "widgets_values": []})
    result = validate_workflow(workflow)
    assert result["errors"] == []
    assert any("3" in w for w in result["warnings"])


def test_opaque_passthrough_link_is_a_warning():
    workflow = _minimal_valid_workflow()
    workflow["nodes"].append({
        "id": 3, "type": "Pipe from any [Crystools]",
        "outputs": [{"name": "any_1", "type": "*", "links": [2]}],
    })
    workflow["nodes"][1]["inputs"][0]["link"] = 2
    workflow["links"].append([2, 3, 0, 2, 0, "*"])
    result = validate_workflow(workflow)
    assert result["errors"] == []
    assert any("opaque" in w.lower() for w in result["warnings"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_comfyui_validate.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/ai_film/comfyui/validate.py
"""validate_workflow: Level 1 (graph integrity) and Level 2 (known-node
field shape) as errors that block export/mutation; unclassified nodes
and opaque-passthrough connections as warnings that never block. Level
3 (are required custom nodes/models actually installed) is explicitly
out of scope for V1. See the spec's "validate_workflow" section."""

from __future__ import annotations

from ai_film.comfyui.roles import infer_role
from ai_film.comfyui.widget_addressing import resolve_field_position

_LEVEL_2_CHECKS = {
    ("KSampler", "steps"): lambda v: isinstance(v, int) and v > 0,
    ("KSampler", "cfg"): lambda v: isinstance(v, (int, float)) and v > 0,
    ("CLIPTextEncode", "text"): lambda v: isinstance(v, str),
}


def _nodes_by_id(workflow: dict) -> dict[int, dict]:
    return {n["id"]: n for n in workflow["nodes"]}


def _links_by_id(workflow: dict) -> dict[int, list]:
    return {link[0]: link for link in workflow["links"]}


def _validate_level_1(workflow: dict, errors: list[str]) -> None:
    nodes = _nodes_by_id(workflow)
    links = _links_by_id(workflow)

    for node in workflow["nodes"]:
        for slot_index, inp in enumerate(node.get("inputs", [])):
            link_id = inp.get("link")
            if link_id is None:
                continue
            link = links.get(link_id)
            if link is None:
                errors.append(f"node {node['id']} input {inp['name']!r} references missing link {link_id}")
                continue
            _, origin_id, origin_slot, target_id, target_slot, _ = link
            if target_id != node["id"] or target_slot != slot_index:
                errors.append(f"link {link_id} target does not match node {node['id']} input {slot_index}")
                continue
            origin_node = nodes.get(origin_id)
            if origin_node is None:
                errors.append(f"link {link_id} origin node {origin_id} does not exist")
                continue
            origin_outputs = origin_node.get("outputs", [])
            if origin_slot >= len(origin_outputs):
                errors.append(f"link {link_id} origin slot {origin_slot} out of range on node {origin_id}")
                continue
            origin_type = origin_outputs[origin_slot].get("type")
            target_type = inp.get("type")
            if origin_type not in ("*", target_type) and target_type != "*":
                errors.append(
                    f"link {link_id} connects incompatible types "
                    f"{origin_type!r} -> {target_type!r}"
                )


def _validate_level_2(workflow: dict, errors: list[str], warnings: list[str]) -> None:
    for node in workflow["nodes"]:
        for (node_type, field), check in _LEVEL_2_CHECKS.items():
            if node["type"] != node_type:
                continue
            position = resolve_field_position(node, field)
            if position["mode"] == "schema_mismatch":
                warnings.append(f"node {node['id']} ({node_type}) widget schema doesn't match registry for {field!r}")
                continue
            if position["mode"] != "widget":
                continue
            values = node.get("widgets_values", [])
            value = values[position["index"]] if position["addressing"] == "array" else values[position["key"]]
            if not check(value):
                errors.append(f"node {node['id']} ({node_type}) field {field!r} has an invalid value: {value!r}")


def _validate_classification_warnings(workflow: dict, warnings: list[str]) -> None:
    from ai_film.comfyui.known_nodes import KNOWN_NODE_REGISTRY

    for node in workflow["nodes"]:
        if node["type"] in KNOWN_NODE_REGISTRY:
            continue
        if infer_role(node["type"]) is None:
            warnings.append(f"node {node['id']} ({node['type']}) has no inferred role")


def _validate_opaque_passthrough_warnings(workflow: dict, warnings: list[str]) -> None:
    from ai_film.comfyui.graph import resolve_input_source

    for node in workflow["nodes"]:
        for inp in node.get("inputs", []):
            if inp.get("link") is None:
                continue
            result = resolve_input_source(workflow, node["id"], inp["name"])
            if result["resolution"] == "opaque_passthrough":
                warnings.append(
                    f"node {node['id']} input {inp['name']!r} resolves through an "
                    f"opaque passthrough at node {result['node_id']}"
                )


def validate_workflow(workflow: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    _validate_level_1(workflow, errors)
    _validate_level_2(workflow, errors, warnings)
    _validate_classification_warnings(workflow, warnings)
    _validate_opaque_passthrough_warnings(workflow, warnings)
    return {"errors": errors, "warnings": warnings}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_comfyui_validate.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/comfyui/validate.py tests/test_comfyui_validate.py
git commit -m "feat: add ComfyUI validate_workflow with Level 1/2 errors and warnings"
```

---

### Task 8: `comfyui/describe.py` — `describe_workflow` and `list_workflow_nodes`

**Files:**
- Create: `src/ai_film/comfyui/describe.py`
- Test: `tests/test_comfyui_describe.py`

**Interfaces:**
- Consumes: `infer_role` (Task 3), `resolve_input_source` (Task 6), `KNOWN_NODE_REGISTRY` (Task 4).
- Produces:
  - `describe_workflow(workflow: dict) -> dict` — `{"by_role": {role: [{"id", "type"}, ...]}, "unclassified": {type: count}, "notes": [{"id", "text"}]}`. Compressed: `unclassified` is type+count, never one entry per node.
  - `list_workflow_nodes(workflow: dict, role: str | None = None) -> list[dict]` — one entry per node (optionally filtered), each `{"id", "type", "role", "inputs": [{"name", "resolution", "source_node_id", "source_output_name"}], "outputs": [{"name", "type"}]}`. Never compressed — this is the full-detail substrate the agent reasons over to disambiguate duplicate node families by resolved neighbors.
  Task 13 (CLI) wires both to `describe-workflow`/`list-workflow-nodes`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_comfyui_describe.py
from ai_film.comfyui.describe import describe_workflow, list_workflow_nodes


def _workflow():
    return {
        "nodes": [
            {"id": 1, "type": "CheckpointLoaderSimple",
             "outputs": [{"name": "MODEL", "type": "MODEL", "links": [1]}]},
            {"id": 2, "type": "KSampler",
             "inputs": [{"name": "model", "type": "MODEL", "link": 1}],
             "outputs": [{"name": "LATENT", "type": "LATENT", "links": []}]},
            {"id": 3, "type": "TotallyUnknownCustomNode", "widgets_values": []},
            {"id": 4, "type": "TotallyUnknownCustomNode", "widgets_values": []},
            {"id": 5, "type": "Note", "properties": {"text": ""}, "widgets_values": ["a generic author note"]},
        ],
        "links": [[1, 1, 0, 2, 0, "MODEL"]],
    }


def test_describe_groups_known_nodes_by_role():
    result = describe_workflow(_workflow())
    role_ids = {entry["id"] for entry in result["by_role"]["sampler"]}
    assert role_ids == {2}


def test_describe_compresses_unclassified_nodes_by_type_and_count():
    result = describe_workflow(_workflow())
    assert result["unclassified"] == {"TotallyUnknownCustomNode": 2}


def test_describe_surfaces_notes_verbatim():
    result = describe_workflow(_workflow())
    assert result["notes"] == [{"id": 5, "text": "a generic author note"}]


def test_list_workflow_nodes_includes_resolved_inputs():
    nodes = list_workflow_nodes(_workflow())
    ksampler = next(n for n in nodes if n["id"] == 2)
    assert ksampler["role"] == "sampler"
    assert ksampler["inputs"][0]["resolution"] == "direct"
    assert ksampler["inputs"][0]["source_node_id"] == 1


def test_list_workflow_nodes_filters_by_role():
    nodes = list_workflow_nodes(_workflow(), role="sampler")
    assert [n["id"] for n in nodes] == [2]


def test_list_workflow_nodes_never_compresses_duplicates():
    nodes = list_workflow_nodes(_workflow())
    unknown_ids = [n["id"] for n in nodes if n["type"] == "TotallyUnknownCustomNode"]
    assert unknown_ids == [3, 4]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_comfyui_describe.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/ai_film/comfyui/describe.py
"""Two views of the same annotated graph for two different purposes:
describe_workflow compresses unclassified nodes by type+count so a
427-node workflow doesn't dump hundreds of lines -- the human-facing
summary. list_workflow_nodes never compresses -- the full-detail
substrate an agent reasons over to find exact targets, including
duplicate node families it must disambiguate by resolved neighbors, not
id. See the spec's "describe-workflow should probably not be too
compressed" discussion."""

from __future__ import annotations

from ai_film.comfyui.graph import resolve_input_source
from ai_film.comfyui.roles import infer_role


def describe_workflow(workflow: dict) -> dict:
    by_role: dict[str, list[dict]] = {}
    unclassified: dict[str, int] = {}
    notes: list[dict] = []

    for node in workflow["nodes"]:
        role = infer_role(node["type"])
        if node["type"] == "Note":
            text = node.get("widgets_values", [""])[0]
            notes.append({"id": node["id"], "text": text})
            continue
        if role is None:
            unclassified[node["type"]] = unclassified.get(node["type"], 0) + 1
            continue
        by_role.setdefault(role, []).append({"id": node["id"], "type": node["type"]})

    return {"by_role": by_role, "unclassified": unclassified, "notes": notes}


def list_workflow_nodes(workflow: dict, role: str | None = None) -> list[dict]:
    entries = []
    for node in workflow["nodes"]:
        node_role = infer_role(node["type"])
        if role is not None and node_role != role:
            continue
        resolved_inputs = []
        for inp in node.get("inputs", []):
            if inp.get("link") is None:
                resolved_inputs.append({"name": inp["name"], "resolution": "none"})
                continue
            result = resolve_input_source(workflow, node["id"], inp["name"])
            resolved_inputs.append({
                "name": inp["name"],
                "resolution": result["resolution"],
                "source_node_id": result.get("node_id"),
                "source_output_name": result.get("output_name"),
            })
        entries.append({
            "id": node["id"],
            "type": node["type"],
            "role": node_role,
            "inputs": resolved_inputs,
            "outputs": [{"name": o["name"], "type": o["type"]} for o in node.get("outputs", [])],
        })
    return entries
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_comfyui_describe.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/comfyui/describe.py tests/test_comfyui_describe.py
git commit -m "feat: add ComfyUI describe_workflow and list_workflow_nodes"
```

---

### Task 9: `comfyui/mutations.py` (part 1) — `set_workflow_field` and `set_workflow_raw`

**Files:**
- Create: `src/ai_film/comfyui/mutations.py`
- Test: `tests/test_comfyui_mutations.py`

**Interfaces:**
- Consumes: `resolve_field_position` (Task 5), `find_node` (Task 6).
- Produces: `set_workflow_field(workflow: dict, node_id: int, field: str, value) -> str` and `set_workflow_raw(workflow: dict, node_id: int, value, index: int | None = None, key: str | None = None) -> str`. Both mutate `workflow` in place and return a one-line human-readable summary of what changed; both raise `ValueError` with a clear, specific message on failure. Task 10 adds the other two primitives to this same file. Task 12 (`workflow_service.py`) wraps all four with load/validate/write/log.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_comfyui_mutations.py
import pytest

from ai_film.comfyui.mutations import set_workflow_field, set_workflow_raw


def _ksampler_workflow():
    return {
        "nodes": [
            {"id": 2, "type": "CLIPTextEncode", "widgets_values": ["old prompt"]},
            {"id": 5, "type": "KSampler", "widgets_values": [42, "fixed", 20, 8.0, "euler", "normal", 1]},
            {"id": 99, "type": "TotallyUnknownCustomNode", "widgets_values": [1, 2, 3]},
        ],
        "links": [],
    }


def test_set_workflow_field_updates_array_addressed_value():
    workflow = _ksampler_workflow()
    summary = set_workflow_field(workflow, 2, "text", "a new prompt")
    node = next(n for n in workflow["nodes"] if n["id"] == 2)
    assert node["widgets_values"] == ["a new prompt"]
    assert "2" in summary and "text" in summary


def test_set_workflow_field_updates_a_middle_widget_position():
    workflow = _ksampler_workflow()
    set_workflow_field(workflow, 5, "steps", 30)
    node = next(n for n in workflow["nodes"] if n["id"] == 5)
    assert node["widgets_values"] == [42, "fixed", 30, 8.0, "euler", "normal", 1]


def test_set_workflow_field_rejects_unregistered_node():
    workflow = _ksampler_workflow()
    with pytest.raises(ValueError, match="not in the known-node registry"):
        set_workflow_field(workflow, 99, "anything", "x")


def test_set_workflow_field_raises_schema_mismatch_never_a_silent_wrong_write():
    workflow = _ksampler_workflow()
    node = next(n for n in workflow["nodes"] if n["id"] == 5)
    node["widgets_values"] = [42, "fixed", 20, 8.0]  # only 4 of KSampler's 7 declared widgets
    with pytest.raises(ValueError, match="schema mismatch"):
        set_workflow_field(workflow, 5, "sampler_name", "euler")
    assert node["widgets_values"] == [42, "fixed", 20, 8.0]  # untouched


def test_set_workflow_raw_writes_by_array_index():
    workflow = _ksampler_workflow()
    set_workflow_raw(workflow, 99, "changed", index=1)
    node = next(n for n in workflow["nodes"] if n["id"] == 99)
    assert node["widgets_values"] == [1, "changed", 3]


def test_set_workflow_raw_writes_by_dict_key():
    workflow = {"nodes": [{"id": 41, "type": "VHS_LoadVideo", "widgets_values": {"video": "old.mp4"}}], "links": []}
    set_workflow_raw(workflow, 41, "new.mp4", key="video")
    node = next(n for n in workflow["nodes"] if n["id"] == 41)
    assert node["widgets_values"]["video"] == "new.mp4"


def test_set_workflow_raw_requires_exactly_one_of_index_or_key():
    workflow = _ksampler_workflow()
    with pytest.raises(ValueError, match="exactly one"):
        set_workflow_raw(workflow, 99, "x")
    with pytest.raises(ValueError, match="exactly one"):
        set_workflow_raw(workflow, 99, "x", index=0, key="video")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_comfyui_mutations.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/ai_film/comfyui/mutations.py
"""Pure, in-memory graph-surgery primitives -- each mutates the given
workflow dict in place and returns a one-line summary, or raises
ValueError with a specific, actionable message. Callers (workflow_service
in Task 12) are responsible for validate-then-write-only-on-success and
logging; these functions never touch disk. See the spec's "Three
mutation primitives" and "Atomicity and the mutation log"."""

from __future__ import annotations

from ai_film.comfyui.graph import find_node
from ai_film.comfyui.widget_addressing import resolve_field_position


def set_workflow_field(workflow: dict, node_id: int, field: str, value) -> str:
    node = find_node(workflow, node_id)
    if node is None:
        raise ValueError(f"no node with id {node_id}")
    position = resolve_field_position(node, field)

    if position["mode"] == "not_registered":
        raise ValueError(
            f"node {node_id} ({node['type']}) is not in the known-node registry, "
            f"or has no field {field!r} -- use set_workflow_raw instead"
        )
    if position["mode"] == "converted_input":
        raise ValueError(
            f"field {field!r} on node {node_id} has been converted to an input "
            f"socket named {position['input_name']!r} -- use rewire_workflow_link instead"
        )
    if position["mode"] == "schema_mismatch":
        raise ValueError(
            f"schema mismatch: node {node_id} ({node['type']})'s widget layout "
            f"doesn't match the known-node registry for field {field!r} (expected "
            f"{position['expected']}, found {position['actual_widget_count']} widgets) "
            f"-- use set_workflow_raw instead"
        )

    if position["addressing"] == "array":
        node["widgets_values"][position["index"]] = value
    else:
        node["widgets_values"][position["key"]] = value
    return f"set node {node_id} ({node['type']}).{field} = {value!r}"


def set_workflow_raw(workflow: dict, node_id: int, value, index: int | None = None, key: str | None = None) -> str:
    if (index is None) == (key is None):
        raise ValueError("set_workflow_raw requires exactly one of index or key")
    node = find_node(workflow, node_id)
    if node is None:
        raise ValueError(f"no node with id {node_id}")

    if index is not None:
        node["widgets_values"][index] = value
        return f"set node {node_id} ({node['type']}).widgets_values[{index}] = {value!r} (raw)"
    node["widgets_values"][key] = value
    return f"set node {node_id} ({node['type']}).widgets_values[{key!r}] = {value!r} (raw)"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_comfyui_mutations.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/comfyui/mutations.py tests/test_comfyui_mutations.py
git commit -m "feat: add ComfyUI set_workflow_field and set_workflow_raw mutations"
```

---

### Task 10: `comfyui/mutations.py` (part 2) — `rewire_workflow_link` and `remove_workflow_node`

**Files:**
- Modify: `src/ai_film/comfyui/mutations.py` (append to the file from Task 9)
- Test: `tests/test_comfyui_mutations.py` (append)

**Interfaces:**
- Consumes: `find_node` (Task 6).
- Produces: `rewire_workflow_link(workflow, target_node_id, target_input, source_node_id, source_output) -> str` and `remove_workflow_node(workflow, node_id, bypass=False) -> str`. Both mutate in place, both raise `ValueError` on a missing node/socket name. Neither does its own socket-type check — that's `validate_workflow`'s job, run by the Task 12 service wrapper *after* the mutation, before it's persisted (see Global Constraints' atomicity rule).

Type-safety note: `rewire_workflow_link` does not reject a type mismatch itself; it applies the requested rewire, and the caller's post-mutation `validate_workflow` call is what blocks persisting an invalid result. This keeps validation in exactly one place instead of duplicating the Level 1 check inside every mutation.

`remove_workflow_node(..., bypass=True)` only reconnects when there is **exactly one** input/output pair on the removed node sharing the same socket type — otherwise it behaves like `bypass=False` (remove and disconnect) and its summary says so explicitly.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_comfyui_mutations.py
from ai_film.comfyui.mutations import remove_workflow_node, rewire_workflow_link


def _rewire_workflow():
    return {
        "last_link_id": 2,
        "nodes": [
            {"id": 1, "type": "CheckpointLoaderSimple",
             "outputs": [{"name": "MODEL", "type": "MODEL", "links": [1]}]},
            {"id": 2, "type": "CheckpointLoaderSimple",
             "outputs": [{"name": "MODEL", "type": "MODEL", "links": []}]},
            {"id": 3, "type": "KSampler",
             "inputs": [{"name": "model", "type": "MODEL", "link": 1}]},
        ],
        "links": [[1, 1, 0, 3, 0, "MODEL"]],
    }


def test_rewire_points_target_input_at_new_source():
    workflow = _rewire_workflow()
    rewire_workflow_link(workflow, target_node_id=3, target_input="model",
                          source_node_id=2, source_output="MODEL")
    target = next(n for n in workflow["nodes"] if n["id"] == 3)
    new_link_id = target["inputs"][0]["link"]
    assert new_link_id != 1
    new_link = next(l for l in workflow["links"] if l[0] == new_link_id)
    assert new_link[1:5] == [2, 0, 3, 0]
    old_source = next(n for n in workflow["nodes"] if n["id"] == 1)
    assert 1 not in old_source["outputs"][0]["links"]
    new_source = next(n for n in workflow["nodes"] if n["id"] == 2)
    assert new_link_id in new_source["outputs"][0]["links"]
    assert not any(l[0] == 1 for l in workflow["links"])  # old link removed


def _bypass_workflow():
    return {
        "last_link_id": 2,
        "nodes": [
            {"id": 1, "type": "CheckpointLoaderSimple",
             "outputs": [{"name": "MODEL", "type": "MODEL", "links": [1]}]},
            {"id": 2, "type": "LoraLoaderModelOnly",
             "inputs": [{"name": "model", "type": "MODEL", "link": 1}],
             "outputs": [{"name": "MODEL", "type": "MODEL", "links": [2]}],
             "widgets_values": ["x.safetensors", 1.0]},
            {"id": 3, "type": "KSampler",
             "inputs": [{"name": "model", "type": "MODEL", "link": 2}]},
        ],
        "links": [[1, 1, 0, 2, 0, "MODEL"], [2, 2, 0, 3, 0, "MODEL"]],
    }


def test_remove_with_bypass_reconnects_around_the_removed_node():
    workflow = _bypass_workflow()
    remove_workflow_node(workflow, 2, bypass=True)
    assert not any(n["id"] == 2 for n in workflow["nodes"])
    target = next(n for n in workflow["nodes"] if n["id"] == 3)
    new_link_id = target["inputs"][0]["link"]
    new_link = next(l for l in workflow["links"] if l[0] == new_link_id)
    assert new_link[1:5] == [1, 0, 3, 0]


def test_remove_without_bypass_leaves_downstream_input_disconnected():
    workflow = _bypass_workflow()
    remove_workflow_node(workflow, 2, bypass=False)
    assert not any(n["id"] == 2 for n in workflow["nodes"])
    target = next(n for n in workflow["nodes"] if n["id"] == 3)
    assert target["inputs"][0]["link"] is None


def test_remove_bypass_declines_when_no_unambiguous_pair_exists():
    workflow = _bypass_workflow()
    # give the node a second, differently-typed output so there's no
    # single unambiguous MODEL-in/MODEL-out pair anymore
    workflow["nodes"][1]["outputs"].append({"name": "EXTRA", "type": "EXTRA", "links": []})
    summary = remove_workflow_node(workflow, 2, bypass=True)
    target = next(n for n in workflow["nodes"] if n["id"] == 3)
    assert target["inputs"][0]["link"] is None  # behaved like bypass=False
    assert "did not bypass" in summary.lower()


def test_remove_bypass_works_on_a_completely_unclassified_node_type():
    """Bypass eligibility depends only on socket types matching, never on
    Layer A/B classification -- an unrecognized custom node type must
    bypass exactly as readily as a known one, since remove_workflow_node
    never looks at node["type"] to decide eligibility."""
    workflow = _bypass_workflow()
    workflow["nodes"][1]["type"] = "SomeNeverBeforeSeenCustomNode"
    remove_workflow_node(workflow, 2, bypass=True)
    target = next(n for n in workflow["nodes"] if n["id"] == 3)
    new_link = next(l for l in workflow["links"] if l[0] == target["inputs"][0]["link"])
    assert new_link[1:5] == [1, 0, 3, 0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_comfyui_mutations.py -v`
Expected: FAIL (new functions not defined)

- [ ] **Step 3: Implement** — append to `src/ai_film/comfyui/mutations.py`

```python
def _next_link_id(workflow: dict) -> int:
    existing = [link[0] for link in workflow["links"]]
    next_id = max([workflow.get("last_link_id", 0)] + existing) + 1
    workflow["last_link_id"] = next_id
    return next_id


def _remove_link(workflow: dict, link_id: int) -> None:
    link = next((l for l in workflow["links"] if l[0] == link_id), None)
    if link is None:
        return
    _, origin_id, origin_slot, target_id, target_slot, _ = link
    workflow["links"] = [l for l in workflow["links"] if l[0] != link_id]
    origin_node = find_node(workflow, origin_id)
    if origin_node is not None:
        origin_node["outputs"][origin_slot]["links"] = [
            lid for lid in origin_node["outputs"][origin_slot].get("links") or [] if lid != link_id
        ]
    target_node = find_node(workflow, target_id)
    if target_node is not None and target_slot < len(target_node.get("inputs", [])):
        if target_node["inputs"][target_slot].get("link") == link_id:
            target_node["inputs"][target_slot]["link"] = None


def _add_link(workflow: dict, source_node_id: int, source_slot: int, target_node_id: int, target_slot: int, link_type: str) -> int:
    link_id = _next_link_id(workflow)
    workflow["links"].append([link_id, source_node_id, source_slot, target_node_id, target_slot, link_type])
    source_node = find_node(workflow, source_node_id)
    source_node["outputs"][source_slot].setdefault("links", [])
    if source_node["outputs"][source_slot]["links"] is None:
        source_node["outputs"][source_slot]["links"] = []
    source_node["outputs"][source_slot]["links"].append(link_id)
    target_node = find_node(workflow, target_node_id)
    target_node["inputs"][target_slot]["link"] = link_id
    return link_id


def rewire_workflow_link(workflow: dict, target_node_id: int, target_input: str, source_node_id: int, source_output: str) -> str:
    target_node = find_node(workflow, target_node_id)
    if target_node is None:
        raise ValueError(f"no node with id {target_node_id}")
    source_node = find_node(workflow, source_node_id)
    if source_node is None:
        raise ValueError(f"no node with id {source_node_id}")

    target_slot = next((i for i, inp in enumerate(target_node.get("inputs", [])) if inp["name"] == target_input), None)
    if target_slot is None:
        raise ValueError(f"node {target_node_id} ({target_node['type']}) has no input named {target_input!r}")
    source_slot = next((i for i, out in enumerate(source_node.get("outputs", [])) if out["name"] == source_output), None)
    if source_slot is None:
        raise ValueError(f"node {source_node_id} ({source_node['type']}) has no output named {source_output!r}")

    old_link_id = target_node["inputs"][target_slot].get("link")
    if old_link_id is not None:
        _remove_link(workflow, old_link_id)

    link_type = source_node["outputs"][source_slot]["type"]
    _add_link(workflow, source_node_id, source_slot, target_node_id, target_slot, link_type)
    return (
        f"rewired node {target_node_id} ({target_node['type']}).{target_input} <- "
        f"node {source_node_id} ({source_node['type']}).{source_output}"
    )


def _find_bypass_pair(node: dict) -> tuple[int, int] | None:
    matches = [
        (i, o)
        for i, inp in enumerate(node.get("inputs", []))
        for o, out in enumerate(node.get("outputs", []))
        if inp.get("link") is not None and inp["type"] == out["type"]
    ]
    return matches[0] if len(matches) == 1 else None


def remove_workflow_node(workflow: dict, node_id: int, bypass: bool = False) -> str:
    node = find_node(workflow, node_id)
    if node is None:
        raise ValueError(f"no node with id {node_id}")

    pair = _find_bypass_pair(node) if bypass else None
    bypass_source = None
    bypass_targets: list[tuple[int, int]] = []
    if pair is not None:
        in_slot, out_slot = pair
        in_link_id = node["inputs"][in_slot]["link"]
        in_link = next(l for l in workflow["links"] if l[0] == in_link_id)
        bypass_source = (in_link[1], in_link[2])
        link_type = node["outputs"][out_slot]["type"]
        for out_link_id in node["outputs"][out_slot].get("links") or []:
            out_link = next(l for l in workflow["links"] if l[0] == out_link_id)
            bypass_targets.append((out_link[3], out_link[4]))

    touching_link_ids = {
        l[0] for l in workflow["links"] if l[1] == node_id or l[3] == node_id
    }
    for link_id in touching_link_ids:
        _remove_link(workflow, link_id)
    workflow["nodes"] = [n for n in workflow["nodes"] if n["id"] != node_id]

    if bypass_source is not None and bypass_targets:
        for target_id, target_slot in bypass_targets:
            _add_link(workflow, bypass_source[0], bypass_source[1], target_id, target_slot, link_type)
        return f"removed node {node_id} ({node['type']}) and bypassed {len(bypass_targets)} connection(s) around it"

    suffix = " (did not bypass -- no unambiguous type-matching input/output pair)" if bypass else ""
    return f"removed node {node_id} ({node['type']}){suffix}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_comfyui_mutations.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/comfyui/mutations.py tests/test_comfyui_mutations.py
git commit -m "feat: add ComfyUI rewire_workflow_link and remove_workflow_node mutations"
```

---

### Task 11: `services/workflow_service.py` — import/export and atomic, logged mutation wrappers

**Files:**
- Create: `src/ai_film/services/workflow_service.py`
- Test: `tests/services/test_workflow_service.py`

**Interfaces:**
- Consumes: `load_workflow_json` (Task 2), `validate_workflow` (Task 7), all four `comfyui.mutations` functions (Tasks 9-10), `write_attempt_log` from `ai_film.logging_store` (existing).
- Produces: `import_workflow(workflows_dir, file_path, workflow_id) -> dict`, `load_stored_workflow(workflows_dir, workflow_id) -> dict`, `export_workflow(workflows_dir, workflow_id, out_path) -> dict`, `set_workflow_field_service(...)`, `set_workflow_raw_service(...)`, `rewire_workflow_link_service(...)`, `remove_workflow_node_service(...)` — each of the four mutation services returns `{"summary": str, "warnings": list[str]}` and raises `ValueError` on failure. Task 12 (CLI) is the only consumer.

`<workflow_id>` validation (`^[a-z0-9][a-z0-9-]*$`, no `/`, `\`, `..`) mirrors `template_store.py`'s `_validate_template_id` exactly, kept local to this file rather than a shared utility, matching that existing precedent. Every mutation wrapper: load from disk, apply the pure mutation, run `validate_workflow`, log the outcome via `write_attempt_log` either way, write to disk only if there were no errors.

- [ ] **Step 1: Write the failing tests**

```python
# tests/services/test_workflow_service.py
import json
from pathlib import Path

import pytest

from ai_film.services.workflow_service import (
    export_workflow,
    import_workflow,
    load_stored_workflow,
    remove_workflow_node_service,
    set_workflow_field_service,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def test_import_workflow_rejects_invalid_id(tmp_path):
    with pytest.raises(ValueError, match="invalid workflow id"):
        import_workflow(tmp_path, FIXTURES_DIR / "comfyui_workflow_simple.json", "../evil")


def test_import_workflow_stores_at_workflows_id_workflow_json(tmp_path):
    import_workflow(tmp_path, FIXTURES_DIR / "comfyui_workflow_simple.json", "my-flow")
    stored = tmp_path / "my-flow" / "workflow.json"
    assert stored.exists()
    assert json.loads(stored.read_text())["last_node_id"] == 7


def test_import_workflow_rejects_api_format(tmp_path):
    with pytest.raises(ValueError):
        import_workflow(tmp_path, FIXTURES_DIR / "comfyui_workflow_api_format.json", "bad-flow")
    assert not (tmp_path / "bad-flow").exists()


def test_export_workflow_writes_to_the_given_path(tmp_path):
    import_workflow(tmp_path, FIXTURES_DIR / "comfyui_workflow_simple.json", "my-flow")
    out_path = tmp_path / "exported.json"
    export_workflow(tmp_path, "my-flow", out_path)
    assert json.loads(out_path.read_text())["last_node_id"] == 7


def test_successful_mutation_writes_and_logs_applied(tmp_path):
    import_workflow(tmp_path, FIXTURES_DIR / "comfyui_workflow_simple.json", "my-flow")
    result = set_workflow_field_service(tmp_path, "my-flow", 2, "text", "a new prompt")
    assert "2" in result["summary"]
    stored = load_stored_workflow(tmp_path, "my-flow")
    node = next(n for n in stored["nodes"] if n["id"] == 2)
    assert node["widgets_values"] == ["a new prompt"]
    logs = list((tmp_path / "my-flow" / "99_logs" / "mutations").glob("*_set-workflow-field_attempt01.json"))
    assert len(logs) == 1
    assert json.loads(logs[0].read_text())["outcome"] == "applied"


def test_failed_mutation_leaves_stored_file_untouched_and_logs_rejected(tmp_path):
    import_workflow(tmp_path, FIXTURES_DIR / "comfyui_workflow_simple.json", "my-flow")
    before = (tmp_path / "my-flow" / "workflow.json").read_text()
    with pytest.raises(ValueError):
        set_workflow_field_service(tmp_path, "my-flow", 999, "text", "x")  # node 999 doesn't exist
    after = (tmp_path / "my-flow" / "workflow.json").read_text()
    assert before == after
    logs = list((tmp_path / "my-flow" / "99_logs" / "mutations").glob("*_set-workflow-field_attempt01.json"))
    assert json.loads(logs[0].read_text())["outcome"] == "rejected"


def test_remove_node_service_end_to_end(tmp_path):
    import_workflow(tmp_path, FIXTURES_DIR / "comfyui_workflow_simple.json", "my-flow")
    remove_workflow_node_service(tmp_path, "my-flow", 3, bypass=False)  # negative CLIPTextEncode
    stored = load_stored_workflow(tmp_path, "my-flow")
    assert not any(n["id"] == 3 for n in stored["nodes"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/services/test_workflow_service.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/ai_film/services/workflow_service.py
"""File I/O and orchestration for ComfyUI workflows: import/export and
atomic, logged mutation wrappers around the pure functions in
ai_film.comfyui.*. Workflows are not project-scoped (see the spec's
"Storage" section) -- workflows_dir is a plain CWD-relative directory
like templates/, passed in by the CLI layer, never a film project
path."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ai_film.comfyui import mutations as _mutations
from ai_film.comfyui.parser import load_workflow_json
from ai_film.comfyui.validate import validate_workflow
from ai_film.logging_store import write_attempt_log

_WORKFLOW_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _validate_workflow_id(workflow_id: str) -> None:
    if (
        "/" in workflow_id
        or "\\" in workflow_id
        or ".." in workflow_id
        or not _WORKFLOW_ID_PATTERN.match(workflow_id)
    ):
        raise ValueError(f"invalid workflow id: {workflow_id!r}")


def _workflow_path(workflows_dir: Path, workflow_id: str) -> Path:
    return workflows_dir / workflow_id / "workflow.json"


def _write_workflow(workflows_dir: Path, workflow_id: str, workflow: dict) -> None:
    path = _workflow_path(workflows_dir, workflow_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(workflow, indent=2, ensure_ascii=False))


def import_workflow(workflows_dir: Path, file_path: Path, workflow_id: str) -> dict:
    _validate_workflow_id(workflow_id)
    workflow = load_workflow_json(file_path)
    result = validate_workflow(workflow)
    if result["errors"]:
        raise ValueError(f"workflow has structural errors, not imported: {result['errors']}")
    _write_workflow(workflows_dir, workflow_id, workflow)
    return {"id": workflow_id, "warnings": result["warnings"]}


def load_stored_workflow(workflows_dir: Path, workflow_id: str) -> dict:
    path = _workflow_path(workflows_dir, workflow_id)
    if not path.exists():
        raise ValueError(f"no imported workflow with id {workflow_id!r}")
    return json.loads(path.read_text())


def export_workflow(workflows_dir: Path, workflow_id: str, out_path: Path) -> dict:
    workflow = load_stored_workflow(workflows_dir, workflow_id)
    result = validate_workflow(workflow)
    if result["errors"]:
        raise ValueError(f"workflow has structural errors, not exported: {result['errors']}")
    out_path.write_text(json.dumps(workflow, indent=2, ensure_ascii=False))
    return {"warnings": result["warnings"]}


def _apply_mutation(workflows_dir: Path, workflow_id: str, primitive_name: str, mutation_fn, request: dict) -> dict:
    workflow = load_stored_workflow(workflows_dir, workflow_id)
    log_base = workflows_dir / workflow_id

    try:
        summary = mutation_fn(workflow)
    except ValueError as exc:
        write_attempt_log(log_base, "mutations", primitive_name, 1,
                           job=None, request=request, response={"error": str(exc)}, outcome="rejected")
        raise

    result = validate_workflow(workflow)
    if result["errors"]:
        write_attempt_log(log_base, "mutations", primitive_name, 1, job=None, request=request,
                           response={"errors": result["errors"]}, outcome="rejected")
        raise ValueError(f"mutation would break the workflow, not applied: {result['errors']}")

    _write_workflow(workflows_dir, workflow_id, workflow)
    write_attempt_log(log_base, "mutations", primitive_name, 1, job=None, request=request,
                       response={"summary": summary, "warnings": result["warnings"]}, outcome="applied")
    return {"summary": summary, "warnings": result["warnings"]}


def set_workflow_field_service(workflows_dir: Path, workflow_id: str, node_id: int, field: str, value) -> dict:
    return _apply_mutation(
        workflows_dir, workflow_id, "set-workflow-field",
        lambda wf: _mutations.set_workflow_field(wf, node_id, field, value),
        {"node_id": node_id, "field": field, "value": value},
    )


def set_workflow_raw_service(workflows_dir: Path, workflow_id: str, node_id: int, value, index=None, key=None) -> dict:
    return _apply_mutation(
        workflows_dir, workflow_id, "set-workflow-raw",
        lambda wf: _mutations.set_workflow_raw(wf, node_id, value, index=index, key=key),
        {"node_id": node_id, "index": index, "key": key, "value": value},
    )


def rewire_workflow_link_service(workflows_dir: Path, workflow_id: str, target_node_id: int, target_input: str,
                                  source_node_id: int, source_output: str) -> dict:
    return _apply_mutation(
        workflows_dir, workflow_id, "rewire-workflow-link",
        lambda wf: _mutations.rewire_workflow_link(wf, target_node_id, target_input, source_node_id, source_output),
        {"target_node_id": target_node_id, "target_input": target_input,
         "source_node_id": source_node_id, "source_output": source_output},
    )


def remove_workflow_node_service(workflows_dir: Path, workflow_id: str, node_id: int, bypass: bool = False) -> dict:
    return _apply_mutation(
        workflows_dir, workflow_id, "remove-workflow-node",
        lambda wf: _mutations.remove_workflow_node(wf, node_id, bypass=bypass),
        {"node_id": node_id, "bypass": bypass},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/services/test_workflow_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/services/workflow_service.py tests/services/test_workflow_service.py
git commit -m "feat: add ComfyUI workflow_service (import/export/mutation wrappers)"
```

---

### Task 12: CLI commands

**Files:**
- Modify: `src/ai_film/cli.py`
- Test: `tests/test_cli_workflow_commands.py`

**Interfaces:**
- Consumes: every function from `ai_film.services.workflow_service` (Task 11) and `describe_workflow`/`list_workflow_nodes` (Task 8).
- Produces: 9 new Typer commands (`import-workflow`, `describe-workflow`, `list-workflow-nodes`, `set-workflow-field`, `set-workflow-raw`, `rewire-workflow-link`, `remove-workflow-node`, `validate-workflow`, `export-workflow`), each non-project-scoped (`--workflows-dir`, default `workflows/`, mirroring `--templates-dir`).

CLI `--value` arguments arrive as strings; a small coercion helper tries `int`, then `float`, then `"true"/"false"` as bool, falling back to the literal string — real ComfyUI widget values are typed, so writing back an unconverted string would silently corrupt a numeric field.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_workflow_commands.py
import json
from pathlib import Path

from typer.testing import CliRunner

from ai_film.cli import app

runner = CliRunner()
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_import_then_describe_then_export(tmp_path):
    workflows_dir = tmp_path / "workflows"
    result = runner.invoke(app, [
        "import-workflow", str(FIXTURES_DIR / "comfyui_workflow_simple.json"),
        "--id", "my-flow", "--workflows-dir", str(workflows_dir),
    ])
    assert result.exit_code == 0, result.output
    assert (workflows_dir / "my-flow" / "workflow.json").exists()

    result = runner.invoke(app, ["describe-workflow", "--id", "my-flow", "--workflows-dir", str(workflows_dir)])
    assert result.exit_code == 0, result.output
    assert "sampler" in result.output

    out_path = tmp_path / "exported.json"
    result = runner.invoke(app, [
        "export-workflow", "--id", "my-flow", "--out", str(out_path), "--workflows-dir", str(workflows_dir),
    ])
    assert result.exit_code == 0, result.output
    assert json.loads(out_path.read_text())["last_node_id"] == 7


def test_list_workflow_nodes_filters_by_role(tmp_path):
    workflows_dir = tmp_path / "workflows"
    runner.invoke(app, ["import-workflow", str(FIXTURES_DIR / "comfyui_workflow_simple.json"),
                         "--id", "my-flow", "--workflows-dir", str(workflows_dir)])
    result = runner.invoke(app, ["list-workflow-nodes", "--id", "my-flow", "--role", "sampler",
                                  "--workflows-dir", str(workflows_dir)])
    assert result.exit_code == 0, result.output
    assert "KSampler" in result.output
    assert "CheckpointLoaderSimple" not in result.output


def test_set_workflow_field_coerces_numeric_value(tmp_path):
    workflows_dir = tmp_path / "workflows"
    runner.invoke(app, ["import-workflow", str(FIXTURES_DIR / "comfyui_workflow_simple.json"),
                         "--id", "my-flow", "--workflows-dir", str(workflows_dir)])
    result = runner.invoke(app, ["set-workflow-field", "--id", "my-flow", "--node", "5",
                                  "--field", "steps", "--value", "30", "--workflows-dir", str(workflows_dir)])
    assert result.exit_code == 0, result.output
    stored = json.loads((workflows_dir / "my-flow" / "workflow.json").read_text())
    node = next(n for n in stored["nodes"] if n["id"] == 5)
    assert node["widgets_values"][2] == 30  # int, not the string "30"


def test_remove_workflow_node_with_bypass_flag(tmp_path):
    workflows_dir = tmp_path / "workflows"
    runner.invoke(app, ["import-workflow", str(FIXTURES_DIR / "comfyui_workflow_simple.json"),
                         "--id", "my-flow", "--workflows-dir", str(workflows_dir)])
    result = runner.invoke(app, ["remove-workflow-node", "--id", "my-flow", "--node", "3",
                                  "--workflows-dir", str(workflows_dir)])
    assert result.exit_code == 0, result.output
    stored = json.loads((workflows_dir / "my-flow" / "workflow.json").read_text())
    assert not any(n["id"] == 3 for n in stored["nodes"])


def test_mutation_failure_exits_nonzero_with_clear_message(tmp_path):
    workflows_dir = tmp_path / "workflows"
    runner.invoke(app, ["import-workflow", str(FIXTURES_DIR / "comfyui_workflow_simple.json"),
                         "--id", "my-flow", "--workflows-dir", str(workflows_dir)])
    result = runner.invoke(app, ["set-workflow-field", "--id", "my-flow", "--node", "999",
                                  "--field", "text", "--value", "x", "--workflows-dir", str(workflows_dir)])
    assert result.exit_code == 1
    assert "999" in result.output


def test_import_rejects_v1_schema_with_clear_error(tmp_path):
    workflows_dir = tmp_path / "workflows"
    result = runner.invoke(app, ["import-workflow", str(FIXTURES_DIR / "comfyui_workflow_v1_schema.json"),
                                  "--id", "bad-flow", "--workflows-dir", str(workflows_dir)])
    assert result.exit_code == 1
    assert "v1.0" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli_workflow_commands.py -v`
Expected: FAIL (commands don't exist)

- [ ] **Step 3: Implement** — add near the template-library commands in `src/ai_film/cli.py`

```python
# add to the imports near the top of cli.py
from ai_film.comfyui.describe import describe_workflow, list_workflow_nodes
from ai_film.services.workflow_service import (
    export_workflow as export_workflow_service,
    import_workflow as import_workflow_service,
    remove_workflow_node_service,
    rewire_workflow_link_service,
    set_workflow_field_service,
    set_workflow_raw_service,
)
from ai_film.comfyui.validate import validate_workflow as validate_workflow_service
from ai_film.services.workflow_service import load_stored_workflow

# near DEFAULT_TEMPLATES_PATH
DEFAULT_WORKFLOWS_PATH = Path("workflows")


def _coerce_cli_value(raw: str):
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


@app.command(name="import-workflow")
def import_workflow_cmd(
    file: Path = typer.Argument(...),
    id: str = typer.Option(..., "--id"),
    workflows_dir: Path = typer.Option(DEFAULT_WORKFLOWS_PATH, "--workflows-dir"),
) -> None:
    """Import a ComfyUI workflow JSON file (legacy workflow shape only)."""
    try:
        result = import_workflow_service(workflows_dir, file, id)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"imported {id}")
    for warning in result["warnings"]:
        typer.echo(f"  warning: {warning}")


@app.command(name="describe-workflow")
def describe_workflow_cmd(
    id: str = typer.Option(..., "--id"),
    workflows_dir: Path = typer.Option(DEFAULT_WORKFLOWS_PATH, "--workflows-dir"),
) -> None:
    """Compressed, human-facing summary of an imported workflow."""
    try:
        workflow = load_stored_workflow(workflows_dir, id)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    description = describe_workflow(workflow)
    for role, nodes in description["by_role"].items():
        typer.echo(f"{role}: " + ", ".join(f"{n['type']}#{n['id']}" for n in nodes))
    for node_type, count in description["unclassified"].items():
        typer.echo(f"unclassified: {node_type} x{count}")
    for note in description["notes"]:
        typer.echo(f"note #{note['id']}: {note['text']}")


@app.command(name="list-workflow-nodes")
def list_workflow_nodes_cmd(
    id: str = typer.Option(..., "--id"),
    role: str = typer.Option(None, "--role"),
    workflows_dir: Path = typer.Option(DEFAULT_WORKFLOWS_PATH, "--workflows-dir"),
) -> None:
    """Full, uncompressed per-node detail, optionally filtered by role."""
    try:
        workflow = load_stored_workflow(workflows_dir, id)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    for node in list_workflow_nodes(workflow, role=role):
        typer.echo(f"#{node['id']} {node['type']} (role={node['role']})")
        for inp in node["inputs"]:
            typer.echo(f"  {inp['name']} <- {inp['resolution']} node {inp.get('source_node_id')}")


@app.command(name="set-workflow-field")
def set_workflow_field_cmd(
    id: str = typer.Option(..., "--id"),
    node: int = typer.Option(..., "--node"),
    field: str = typer.Option(..., "--field"),
    value: str = typer.Option(..., "--value"),
    workflows_dir: Path = typer.Option(DEFAULT_WORKFLOWS_PATH, "--workflows-dir"),
) -> None:
    """Set a Layer B semantic field on a known node type."""
    try:
        result = set_workflow_field_service(workflows_dir, id, node, field, _coerce_cli_value(value))
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(result["summary"])


@app.command(name="set-workflow-raw")
def set_workflow_raw_cmd(
    id: str = typer.Option(..., "--id"),
    node: int = typer.Option(..., "--node"),
    value: str = typer.Option(..., "--value"),
    index: int = typer.Option(None, "--index"),
    key: str = typer.Option(None, "--key"),
    workflows_dir: Path = typer.Option(DEFAULT_WORKFLOWS_PATH, "--workflows-dir"),
) -> None:
    """Explicit raw widgets_values index/key write -- escape hatch for anything
    outside the known-node registry. Requires human confirmation at the agent
    layer before use; the CLI itself has no concept of "confirmed"."""
    try:
        result = set_workflow_raw_service(workflows_dir, id, node, _coerce_cli_value(value), index=index, key=key)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(result["summary"])


@app.command(name="rewire-workflow-link")
def rewire_workflow_link_cmd(
    id: str = typer.Option(..., "--id"),
    target_node: int = typer.Option(..., "--target-node"),
    target_input: str = typer.Option(..., "--target-input"),
    source_node: int = typer.Option(..., "--source-node"),
    source_output: str = typer.Option(..., "--source-output"),
    workflows_dir: Path = typer.Option(DEFAULT_WORKFLOWS_PATH, "--workflows-dir"),
) -> None:
    """Point a target node's input at a different source node's output."""
    try:
        result = rewire_workflow_link_service(workflows_dir, id, target_node, target_input, source_node, source_output)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(result["summary"])


@app.command(name="remove-workflow-node")
def remove_workflow_node_cmd(
    id: str = typer.Option(..., "--id"),
    node: int = typer.Option(..., "--node"),
    bypass: bool = typer.Option(False, "--bypass"),
    workflows_dir: Path = typer.Option(DEFAULT_WORKFLOWS_PATH, "--workflows-dir"),
) -> None:
    """Remove a node, optionally bypass-reconnecting around it."""
    try:
        result = remove_workflow_node_service(workflows_dir, id, node, bypass=bypass)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(result["summary"])


@app.command(name="validate-workflow")
def validate_workflow_cmd(
    id: str = typer.Option(..., "--id"),
    workflows_dir: Path = typer.Option(DEFAULT_WORKFLOWS_PATH, "--workflows-dir"),
) -> None:
    """Standalone structural validation -- the same check every mutation and export runs."""
    try:
        workflow = load_stored_workflow(workflows_dir, id)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    result = validate_workflow_service(workflow)
    for warning in result["warnings"]:
        typer.echo(f"warning: {warning}")
    if result["errors"]:
        for error in result["errors"]:
            typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1)
    typer.echo("valid")


@app.command(name="export-workflow")
def export_workflow_cmd(
    id: str = typer.Option(..., "--id"),
    out: Path = typer.Option(..., "--out"),
    workflows_dir: Path = typer.Option(DEFAULT_WORKFLOWS_PATH, "--workflows-dir"),
) -> None:
    """Validate and write the current workflow out to a ComfyUI-loadable file."""
    try:
        result = export_workflow_service(workflows_dir, id, out)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"exported to {out}")
    for warning in result["warnings"]:
        typer.echo(f"  warning: {warning}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli_workflow_commands.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_film/cli.py tests/test_cli_workflow_commands.py
git commit -m "feat: add 9 CLI commands for ComfyUI workflow import/inspect/mutate/export"
```

---

### Task 13: Round-trip and complex-fixture integration tests

**Files:**
- Create: `tests/test_comfyui_roundtrip.py`

**Interfaces:**
- Consumes: everything from Tasks 2, 7, 8, 11 (parser, validate, describe, workflow_service), and both fixtures from Task 1. Produces nothing new — this is a pure integration test task, exercising the complex fixture specifically (every unit test so far used small hand-built dicts, not the ~40-node fixture as a whole).

- [ ] **Step 1: Write the tests**

```python
# tests/test_comfyui_roundtrip.py
import json
from pathlib import Path

from ai_film.comfyui.describe import describe_workflow, list_workflow_nodes
from ai_film.comfyui.validate import validate_workflow
from ai_film.services.workflow_service import export_workflow, import_workflow, set_workflow_field_service

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _nodes_by_id(workflow: dict) -> dict:
    return {n["id"]: n for n in workflow["nodes"]}


def test_noop_roundtrip_is_structurally_identical_simple(tmp_path):
    workflows_dir = tmp_path / "workflows"
    import_workflow(workflows_dir, FIXTURES_DIR / "comfyui_workflow_simple.json", "flow")
    original = json.loads((FIXTURES_DIR / "comfyui_workflow_simple.json").read_text())
    out_path = tmp_path / "out.json"
    export_workflow(workflows_dir, "flow", out_path)
    exported = json.loads(out_path.read_text())
    assert _nodes_by_id(exported) == _nodes_by_id(original)
    assert sorted(exported["links"]) == sorted(original["links"])


def test_noop_roundtrip_is_structurally_identical_complex(tmp_path):
    workflows_dir = tmp_path / "workflows"
    import_workflow(workflows_dir, FIXTURES_DIR / "comfyui_workflow_complex.json", "flow")
    original = json.loads((FIXTURES_DIR / "comfyui_workflow_complex.json").read_text())
    out_path = tmp_path / "out.json"
    export_workflow(workflows_dir, "flow", out_path)
    exported = json.loads(out_path.read_text())
    assert _nodes_by_id(exported) == _nodes_by_id(original)


def test_modified_export_leaves_unrelated_nodes_untouched(tmp_path):
    workflows_dir = tmp_path / "workflows"
    import_workflow(workflows_dir, FIXTURES_DIR / "comfyui_workflow_complex.json", "flow")
    original = json.loads((FIXTURES_DIR / "comfyui_workflow_complex.json").read_text())
    set_workflow_field_service(workflows_dir, "flow", 2, "text", "a changed prompt")
    out_path = tmp_path / "out.json"
    export_workflow(workflows_dir, "flow", out_path)
    exported = json.loads(out_path.read_text())
    for node_id, node in _nodes_by_id(original).items():
        if node_id == 2:
            continue
        assert _nodes_by_id(exported)[node_id] == node, f"node {node_id} changed unexpectedly"
    assert _nodes_by_id(exported)[2]["widgets_values"] == ["a changed prompt"]


def test_complex_fixture_validates_clean(tmp_path):
    workflow = json.loads((FIXTURES_DIR / "comfyui_workflow_complex.json").read_text())
    result = validate_workflow(workflow)
    assert result["errors"] == []


def test_complex_fixture_describe_groups_livportrait_family_as_one_stage(tmp_path):
    workflow = json.loads((FIXTURES_DIR / "comfyui_workflow_complex.json").read_text())
    description = describe_workflow(workflow)
    facial = description["by_role"].get("facial_performance_transfer", [])
    assert len(facial) == 6  # two chains x 3 nodes each


def test_complex_fixture_disambiguates_duplicate_families_by_neighbors(tmp_path):
    workflow = json.loads((FIXTURES_DIR / "comfyui_workflow_complex.json").read_text())
    nodes = list_workflow_nodes(workflow, role="facial_performance_transfer")
    croppers = [n for n in nodes if n["type"] == "LivePortraitCropper"]
    assert len(croppers) == 2
    sources = {inp["source_node_id"] for n in croppers for inp in n["inputs"] if inp["resolution"] == "direct"}
    assert len(sources) == 2, "the two LivePortraitCropper chains must resolve to two distinct upstream sources"


def test_complex_fixture_tags_reroute_chain_as_direct_and_bundler_as_opaque(tmp_path):
    workflow = json.loads((FIXTURES_DIR / "comfyui_workflow_complex.json").read_text())
    nodes = list_workflow_nodes(workflow)
    resolutions = {inp["resolution"] for n in nodes for inp in n["inputs"]}
    assert "direct" in resolutions
    assert "opaque_passthrough" in resolutions
```

- [ ] **Step 2: Run tests to verify they pass** (this task writes no new production code — if any test fails, the fixture from Task 1 or an earlier module has a bug; fix that module, not this test)

Run: `.venv/bin/pytest tests/test_comfyui_roundtrip.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_comfyui_roundtrip.py
git commit -m "test: add ComfyUI round-trip and complex-fixture integration tests"
```

---

### Task 14: Agent, slash command, settings, and docs

**Files:**
- Create: `.claude/agents/ai-film-workflow.md`
- Create: `.claude/commands/import-workflow.md`
- Modify: `.claude/settings.json`
- Modify: `README.md`
- Modify: `README.zh-TW.md`

**Interfaces:**
- Consumes: all 9 CLI commands from Task 12 (via Bash, following `AI_FILM_BIN` resolution exactly as every other agent in this repo does).
- Produces: nothing importable — this is agent-instruction prose plus permission/doc entries. Final task in the plan.

- [ ] **Step 1: Write `.claude/agents/ai-film-workflow.md`**

Frontmatter tool list is the actual enforcement mechanism for "never edits workflow.json directly" — `tools` deliberately omits `Write`, matching the YAML frontmatter shape every other agent in `.claude/agents/*.md` already uses (`name`/`description`/`tools`/`model`).

```markdown
---
name: ai-film-workflow
description: Imports a ComfyUI workflow, discusses and modifies it in natural language through validated CLI primitives, and exports the result. Dispatched by /import-workflow -- standalone, not part of /create-film.
tools: ["Read", "Bash", "Glob"]
model: sonnet
---

# ai-film-workflow

You are the ComfyUI Workflow agent for `ai-film-studio`. You are given one thing in your dispatch instructions: a file path to a ComfyUI workflow JSON to import. Resolve `AI_FILM_BIN` exactly as every other agent in this project does: run `ai-film version`; if that fails, run `./.venv/bin/ai-film version`; if neither works, stop and report no working install was found.

**Hard rule: you never edit `workflow.json` directly.** Every change goes through one of the CLI primitives below, over Bash. This is enforced by your own tool list (no Write) as well as by this instruction — both exist on purpose.

## Step 1: Import and describe

```bash
AI_FILM_BIN import-workflow <file> --id <a-short-kebab-case-id-you-choose>
AI_FILM_BIN describe-workflow --id <id>
```

If `import-workflow` fails (wrong format, structural errors), report the exact error to the human and stop — don't retry with a different id or guess at a fix.

Read `describe-workflow`'s output and narrate your own understanding of the pipeline to the human in plain language — this is *your* reasoning to do, not something the tool computed for you. Name the stages you can infer from role groupings, node names, and any notes surfaced; be explicit about parts you can't classify ("N nodes of type X are custom and not something I understand the internals of, but they're preserved").

## Step 2: Discuss and modify

For each request, use `list-workflow-nodes --id <id> [--role <role>]` to find your target(s) precisely — never guess a node id. When a workflow contains repeated, structurally-similar node families (e.g. two independent LivePortrait chains), disambiguate using each node's resolved neighbors (which upstream node feeds it, what it feeds downstream), the same way a human traces wires on a canvas — never by id alone. If genuinely ambiguous, ask the human which one they mean.

Pick the smallest-scope primitive that satisfies the request:

- **Change a value on a known node type** (a prompt, a checkpoint filename, a sampler parameter): `set-workflow-field --id <id> --node <n> --field <f> --value <v>`.
- **Swap what feeds something, or remove a node while keeping the rest connected** ("use a different model," "remove this LoRA"): `rewire-workflow-link` or `remove-workflow-node --bypass`. These work on any node, known or not — you don't need to understand a custom node's internals to rewire around it.
- **Anything else the known registry doesn't cover**: `set-workflow-raw --id <id> --node <n> --index <i>|--key <k> --value <v>`. **Before running this one, you must explicitly ask the human to confirm** — state which node, which raw index or key, and why (not in the registry, or the registry's expected shape didn't match this instance from a `schema mismatch` error). Proceed only after they say yes. This is the only primitive that requires this extra step.

After each mutation, re-run `describe-workflow` or `list-workflow-nodes` to confirm the result, then tell the human what changed in plain language before moving on. If a multi-step request (e.g. "remove the LoRA and change the character") has one step fail partway through, report exactly which steps already succeeded before reporting the failure, and ask how to proceed — never leave the human to discover a partial change on their own.

## Step 3: Export

Once the human is satisfied:

```bash
AI_FILM_BIN export-workflow --id <id> --out <path they specify>
```

Report the output path. The exported file is a standard ComfyUI workflow file — tell them it can be loaded via ComfyUI's own Load, assuming any required custom nodes/models are installed there (this tool doesn't and can't verify that).
```

- [ ] **Step 2: Write `.claude/commands/import-workflow.md`**

```markdown
Dispatch the ai-film-workflow agent to import and discuss a ComfyUI workflow.

Usage: `/import-workflow <path to workflow JSON>`

Resolve the file path from the user's message, then dispatch the `ai-film-workflow` agent with that path as its one instruction. This is a standalone, optional tool — it is not part of `/create-film` and does not require an existing ai-film-studio project.
```

- [ ] **Step 3: Update `.claude/settings.json`** — add to both the bare `ai-film` and `./.venv/bin/ai-film` permission blocks, matching the existing pattern (non-spend, read/inspect commands pre-authorized; nothing that spends real money is on this list, matching how `generate-candidates`/`edit-candidate` are deliberately absent for other agents — but every ComfyUI command here only touches local files, so all 9 are pre-authorized):

```json
      "Bash(ai-film import-workflow *)",
      "Bash(ai-film describe-workflow *)",
      "Bash(ai-film list-workflow-nodes *)",
      "Bash(ai-film set-workflow-field *)",
      "Bash(ai-film set-workflow-raw *)",
      "Bash(ai-film rewire-workflow-link *)",
      "Bash(ai-film remove-workflow-node *)",
      "Bash(ai-film validate-workflow *)",
      "Bash(ai-film export-workflow *)",
```

(and the identical nine lines again under `./.venv/bin/ai-film`).

- [ ] **Step 4: Update `README.md`** — add a new section after the Template Library section:

```markdown
## ComfyUI workflow interop

Import a ComfyUI workflow, discuss and modify it with the `ai-film-workflow` agent in natural language, and export a result ComfyUI can load. Standalone -- no ai-film-studio project required.

```bash
/import-workflow ~/Downloads/someone-elses-workflow.json
```

Under the hood: `import-workflow`/`describe-workflow`/`list-workflow-nodes`/`set-workflow-field`/`set-workflow-raw`/`rewire-workflow-link`/`remove-workflow-node`/`validate-workflow`/`export-workflow`, all non-project-scoped (`--workflows-dir`, default `workflows/`, mirroring `--templates-dir`). See `docs/superpowers/specs/2026-09-11-comfyui-workflow-interop-design.md` for the full design -- notably, the tool never claims to understand every ComfyUI node; unrecognized nodes are preserved exactly and are still modifiable via type-safe graph surgery (rewiring links, removing/bypassing nodes) even without semantic understanding of their internals.
```

- [ ] **Step 5: Update `README.zh-TW.md`** with the equivalent section, translated, following the same structure as the existing Template Library section's translation.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all tests pass (544 existing + this plan's new tests)

- [ ] **Step 7: Commit**

```bash
git add .claude/agents/ai-film-workflow.md .claude/commands/import-workflow.md .claude/settings.json README.md README.zh-TW.md
git commit -m "docs: add ai-film-workflow agent, /import-workflow command, and docs"
```
