"""Tests for logger.py: ALLOWED_FIELDS filtering and attempts recording."""
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

# Add scripts/ to path
sys.path.insert(0, str(Path(__file__).parent.parent))
import ratelimit
import logger as logger_mod


@pytest.fixture(autouse=True)
def fake_clock(monkeypatch):
    class Clock:
        def __init__(self):
            self.now = 1_700_000_000.0

        def __call__(self):
            return self.now

    clock = Clock()
    monkeypatch.setattr(ratelimit.time, "time", clock)
    return clock


class TestAllowedFields:
    def test_attempts_field_is_allowed(self):
        """`attempts` must be in ALLOWED_FIELDS so full fallback chain is recorded."""
        assert "attempts" in logger_mod.ALLOWED_FIELDS

    def test_basic_fields_allowed(self):
        """Sanity check: core fields are in the whitelist."""
        for f in ("ts", "role", "model", "provider", "status", "latency_ms",
                  "image", "dropped_count", "error_kind", "attempts"):
            assert f in logger_mod.ALLOWED_FIELDS, f"{f} missing from ALLOWED_FIELDS"

    def test_log_event_filters_unknown_fields(self, tmp_path):
        """Fields not in ALLOWED_FIELDS are silently dropped."""
        log_file = tmp_path / "test.log"
        logger_mod.log_event(str(log_file), 50, 3,
                             role="quick", model="m", status="success",
                             secret_key="sk-should-NOT-be-logged",
                             internal_state={"foo": "bar"})
        line = log_file.read_text().strip()
        entry = json.loads(line)
        assert "secret_key" not in entry
        assert "internal_state" not in entry
        assert entry["role"] == "quick"
        assert entry["model"] == "m"

    def test_log_event_records_attempts_array(self, tmp_path):
        """Full attempts array (fallback chain) is recorded as-is."""
        log_file = tmp_path / "test.log"
        attempts = [
            {"model": "google-free/gemini-3.6-flash", "status": "rpd_limited"},
            {"model": "google-free/gemini-3.5-flash-lite", "status": "rpd_limited"},
            {"model": "google/gemini-3.1-pro-preview", "status": "success", "latency_ms": 15000},
        ]
        logger_mod.log_event(str(log_file), 50, 3,
                             role="comprehensive", model="google/gemini-3.1-pro-preview",
                             status="success", attempts=attempts)
        line = log_file.read_text().strip()
        entry = json.loads(line)
        assert entry["attempts"] == attempts
        assert entry["status"] == "success"

    def test_log_event_handles_missing_optional_fields(self, tmp_path):
        """Calling log_event with no attempts should not fail."""
        log_file = tmp_path / "test.log"
        logger_mod.log_event(str(log_file), 50, 3,
                             role="quick", model="m", status="success")
        line = log_file.read_text().strip()
        entry = json.loads(line)
        assert "attempts" not in entry  # not passed → not in log
        assert entry["status"] == "success"

    def test_log_event_appends_to_existing_file(self, tmp_path):
        """Multiple log_event calls append, don't overwrite."""
        log_file = tmp_path / "test.log"
        logger_mod.log_event(str(log_file), 50, 3, role="r1", model="m1", status="success")
        logger_mod.log_event(str(log_file), 50, 3, role="r2", model="m2", status="error",
                             error_kind="quota_exceeded")
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2
        e1, e2 = json.loads(lines[0]), json.loads(lines[1])
        assert e1["role"] == "r1" and e2["role"] == "r2"
        assert e2["error_kind"] == "quota_exceeded"

    def test_log_event_continues_on_os_error(self, tmp_path, monkeypatch):
        """If mkdir fails (e.g., path is a regular file, not a directory), log_event should not raise."""
        # Create a regular file at a path that we'll try to use as a parent dir
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        bad_path = blocker / "log.jsonl"  # parent path is a file, mkdir will fail
        # Should not raise
        logger_mod.log_event(str(bad_path), 50, 3, role="r", model="m", status="success")
        # File should not exist (write was silently skipped)
        assert not bad_path.exists()
