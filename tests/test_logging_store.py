import json
from pathlib import Path

from ai_film.logging_store import redact, write_approval_log, write_attempt_log


def test_redact_masks_secret_shaped_keys():
    payload = {
        "model": "veo-3",
        "headers": {"Authorization": "Bearer xyz", "Content-Type": "json"},
        "api_key": "sk-123",
        "nested": {"signed_url": "https://example.com/tmp?sig=abc"},
    }
    redacted = redact(payload)
    assert redacted["headers"]["Authorization"] == "[REDACTED]"
    assert redacted["headers"]["Content-Type"] == "json"
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["nested"]["signed_url"] == "[REDACTED]"
    assert redacted["model"] == "veo-3"


def test_write_attempt_log_creates_per_shot_file_with_attempt_and_job(tmp_path: Path):
    log_path = write_attempt_log(
        project_dir=tmp_path,
        shot_id="S01_SH07",
        stage="image",
        attempt=2,
        job={"provider": "fal", "id": "j2"},
        request={"model": "nano-banana", "api_key": "secret"},
        response={"status": "completed"},
        outcome="completed",
    )
    assert log_path.exists()
    assert log_path.parent == tmp_path / "99_logs" / "S01_SH07"
    data = json.loads(log_path.read_text())
    assert data["attempt"] == 2
    assert data["job"] == {"provider": "fal", "id": "j2"}
    assert data["request"]["api_key"] == "[REDACTED]"


def test_write_approval_log_creates_file_under_approvals(tmp_path: Path):
    log_path = write_approval_log(
        project_dir=tmp_path,
        scope="storyboard",
        target_ids=["S01_SH01", "S01_SH02"],
        estimated_cost=4.82,
    )
    assert log_path.parent == tmp_path / "99_logs" / "approvals"
    data = json.loads(log_path.read_text())
    assert data["scope"] == "storyboard"
    assert data["target_ids"] == ["S01_SH01", "S01_SH02"]
    assert data["estimated_cost"] == 4.82
