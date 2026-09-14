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
