from __future__ import annotations

import mimetypes
import os
from pathlib import Path

import requests

from ai_film.errors import ProviderError
from ai_film.models import Capability, GenerationJob, JobStatus

FAL_QUEUE_BASE = "https://queue.fal.run"
FAL_STORAGE_BASE = "https://rest.fal.ai"

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


def upload_file(path: str) -> str:
    """Upload a local file to fal's storage and return a URL usable as an
    `image_url`/`image_urls` input. Paths already given as a URL are
    returned unchanged."""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    file_path = Path(path)
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    initiate = requests.post(
        f"{FAL_STORAGE_BASE}/storage/upload/initiate",
        params={"storage_type": "fal-cdn-v3"},
        json={"file_name": file_path.name, "content_type": content_type},
        headers=_headers(),
        timeout=30,
    )
    if initiate.status_code >= 400:
        raise ProviderError(f"fal upload initiate failed ({initiate.status_code}): {initiate.text}")
    body = initiate.json()
    put_response = requests.put(
        body["upload_url"],
        data=file_path.read_bytes(),
        headers={"Content-Type": content_type},
        timeout=60,
    )
    if put_response.status_code >= 400:
        raise ProviderError(f"fal upload failed ({put_response.status_code}): {put_response.text}")
    return body["file_url"]


def download(url: str, output_path: str) -> int:
    response = requests.get(url, timeout=60)
    if response.status_code >= 400:
        raise ProviderError(f"fal artifact download failed ({response.status_code})")
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return len(response.content)
