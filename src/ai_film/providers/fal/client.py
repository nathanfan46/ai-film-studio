from __future__ import annotations

import os
from pathlib import Path

import requests

from ai_film.errors import ProviderError
from ai_film.models import Capability, GenerationJob, JobStatus

FAL_QUEUE_BASE = "https://queue.fal.run"

_STATUS_MAP = {
    "IN_QUEUE": JobStatus.QUEUED,
    "IN_PROGRESS": JobStatus.RUNNING,
    "COMPLETED": JobStatus.COMPLETED,
}


def _headers() -> dict:
    key = os.environ.get("FAL_KEY")
    if not key:
        raise ProviderError("FAL_KEY environment variable is not set")
    return {"Authorization": f"Key {key}", "Content-Type": "application/json"}


def submit(app_id: str, input_data: dict, capability: Capability) -> tuple[GenerationJob, str, str]:
    response = requests.post(
        f"{FAL_QUEUE_BASE}/{app_id}", json=input_data, headers=_headers(), timeout=30
    )
    if response.status_code >= 400:
        raise ProviderError(f"fal submit failed ({response.status_code}): {response.text}")
    body = response.json()
    job = GenerationJob(provider="fal", id=body["request_id"], capability=capability)
    return job, body["status_url"], body["response_url"]


def poll(status_url: str) -> JobStatus:
    response = requests.get(status_url, headers=_headers(), timeout=30)
    if response.status_code >= 400:
        raise ProviderError(f"fal status check failed ({response.status_code}): {response.text}")
    status = response.json().get("status", "IN_QUEUE")
    if status not in _STATUS_MAP:
        raise ProviderError(f"fal job failed with status: {status}")
    return _STATUS_MAP[status]


def result(response_url: str) -> dict:
    response = requests.get(response_url, headers=_headers(), timeout=30)
    if response.status_code >= 400:
        raise ProviderError(f"fal result fetch failed ({response.status_code}): {response.text}")
    return response.json()


def download(url: str, output_path: str) -> int:
    response = requests.get(url, timeout=60)
    if response.status_code >= 400:
        raise ProviderError(f"fal artifact download failed ({response.status_code})")
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return len(response.content)
