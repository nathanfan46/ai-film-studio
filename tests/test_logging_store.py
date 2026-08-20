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
        response={"status": "completed", "signed_url": "https://example.com/tmp?sig=abc"},
        outcome="completed",
    )
    assert log_path.exists()
    assert log_path.parent == tmp_path / "99_logs" / "S01_SH07"
    data = json.loads(log_path.read_text())
    assert data["attempt"] == 2
    assert data["job"] == {"provider": "fal", "id": "j2"}
    assert data["request"]["api_key"] == "[REDACTED]"
    assert data["response"]["signed_url"] == "[REDACTED]"
    assert data["response"]["status"] == "completed"


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


def test_write_approval_log_timestamps_are_collision_resistant(tmp_path: Path):
    """Verify that multiple approval logs written in rapid succession create distinct files."""
    log_path_1 = write_approval_log(
        project_dir=tmp_path,
        scope="storyboard",
        target_ids=["S01_SH01"],
        estimated_cost=1.0,
    )
    log_path_2 = write_approval_log(
        project_dir=tmp_path,
        scope="sequence",
        target_ids=["S01_SH02"],
        estimated_cost=2.0,
    )
    # Verify both files exist and are distinct
    assert log_path_1.exists()
    assert log_path_2.exists()
    assert log_path_1 != log_path_2
    # Verify both files are in the approvals directory
    approvals_dir = tmp_path / "99_logs" / "approvals"
    assert log_path_1.parent == approvals_dir
    assert log_path_2.parent == approvals_dir
    # Verify there are exactly 2 files in the directory
    files = list(approvals_dir.glob("*.json"))
    assert len(files) == 2
