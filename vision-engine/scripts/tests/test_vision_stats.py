"""Tests for vision-stats.py CLI tool.

TDD: these tests define the expected behavior before implementation.
Run with: python3 -m pytest scripts/tests/test_vision_stats.py -v
"""
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import ratelimit

# Import vision-analyze for config loading utilities
_spec = importlib.util.spec_from_file_location(
    "vision_analyze", Path(__file__).parent.parent / "vision-analyze.py"
)
vision_analyze = importlib.util.module_from_spec(_spec)
sys.modules["vision_analyze"] = vision_analyze
_spec.loader.exec_module(vision_analyze)

# Import vision-stats (will fail until implemented)
_stats_spec = importlib.util.spec_from_file_location(
    "vision_stats", Path(__file__).parent.parent / "vision-stats.py"
)
vision_stats = importlib.util.module_from_spec(_stats_spec)
sys.modules["vision_stats"] = vision_stats
_stats_spec.loader.exec_module(vision_stats)


# ─── Fixtures ───

@pytest.fixture(autouse=True)
def redirect_ratelimit(monkeypatch, tmp_path):
    """Redirect ratelimit data dir to tmp_path."""
    monkeypatch.setattr(ratelimit, "_BASE_DIR", str(tmp_path))


@pytest.fixture
def sample_log(tmp_path):
    """Create a sample log.jsonl with known data."""
    log_file = tmp_path / "log.jsonl"
    entries = [
        {"role": "quick", "model": "google-free/gemini-3.6-flash", "provider": "google-free",
         "status": "success", "latency_ms": 5000, "tokens_in": 2000, "tokens_out": 500,
         "ts": "2026-07-27T10:00:00Z",
         "attempts": [{"model": "google-free/gemini-3.6-flash", "status": "success"}]},

        {"role": "quick", "model": "google-free/gemini-3.6-flash", "provider": "google-free",
         "status": "success", "latency_ms": 3000, "tokens_in": 1500, "tokens_out": 300,
         "ts": "2026-07-27T10:01:00Z",
         "attempts": [{"model": "google-free/gemini-3.6-flash", "status": "success"}]},

        {"role": "comprehensive", "model": "google/gemini-3.1-pro-preview", "provider": "google",
         "status": "success", "latency_ms": 18000, "tokens_in": 3000, "tokens_out": 800,
         "ts": "2026-07-27T10:02:00Z",
         "attempts": [{"model": "google-free/gemini-3.6-flash", "status": "quota_exceeded:requests:60s"},
                      {"model": "google/gemini-3.1-pro-preview", "status": "success"}]},

        {"role": "quick", "model": None, "provider": None,
         "status": "error", "latency_ms": None, "tokens_in": None, "tokens_out": None,
         "error_kind": "all_models_failed", "ts": "2026-07-27T10:03:00Z",
         "attempts": [{"model": "alibailian/qwen3.7-plus", "status": "timeout"}]},
    ]
    log_file.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries))
    return log_file


@pytest.fixture
def sample_config(tmp_path):
    """Create a minimal config with two models."""
    config = {
        "schema_version": "4.0",
        "capability_provider_whitelist": {},
        "roles": {},
        "providers": {
            "google-free": {
                "base_url": "https://example.com",
                "api_format": "google",
                "api_key_env": "FAKE_KEY",
                "models": [{
                    "name": "gemini-3.6-flash",
                    "capabilities": ["general"],
                    "priority": 1,
                    "quotas": [
                        {"metric": "requests", "window_seconds": 60, "limit": 5},
                        {"metric": "requests", "window_seconds": 86400, "limit": 20},
                        {"metric": "tokens", "window_seconds": 60, "limit": 250000},
                    ],
                }]
            },
            "google": {
                "base_url": "https://example.com",
                "api_format": "google",
                "api_key_env": "FAKE_KEY2",
                "models": [{
                    "name": "gemini-3.1-pro-preview",
                    "capabilities": ["general"],
                    "priority": 2,
                    "quotas": [
                        {"metric": "requests", "window_seconds": 60, "limit": 25},
                    ],
                }]
            },
        }
    }
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config))
    return config_file


# ─── summary ───

class TestSummary:
    def test_summary_counts_success_and_failure(self, sample_log, sample_config):
        result = vision_stats.cmd_summary(str(sample_log), str(sample_config))
        models = {r["model"]: r for r in result["models"]}
        assert models["google-free/gemini-3.6-flash"]["success"] == 2
        assert models["google-free/gemini-3.6-flash"]["tokens_in"] == 3500
        assert models["google-free/gemini-3.6-flash"]["tokens_out"] == 800
        assert result["total_success"] == 3
        assert result["total_error"] == 1

    def test_summary_roles_distribution(self, sample_log, sample_config):
        result = vision_stats.cmd_summary(str(sample_log), str(sample_config))
        models = {r["model"]: r for r in result["models"]}
        assert models["google-free/gemini-3.6-flash"]["roles"]["quick"] == 2
        assert models["google/gemini-3.1-pro-preview"]["roles"]["comprehensive"] == 1

    def test_summary_fallback_stats(self, sample_log, sample_config):
        result = vision_stats.cmd_summary(str(sample_log), str(sample_config))
        # google-free was quota_exceeded once (in the comprehensive attempt)
        fb = result["fallback_stats"]
        assert any(f["model"] == "google-free/gemini-3.6-flash" and f["quota_exceeded"] >= 1 for f in fb)


# ─── quota ───

class TestQuota:
    def test_quota_shows_usage(self, sample_config):
        # Record some usage
        q60 = {"metric": "requests", "window_seconds": 60, "limit": 5}
        ratelimit.record_usage("google-free__gemini-3.6-flash", q60, 1)
        ratelimit.record_usage("google-free__gemini-3.6-flash", q60, 1)

        result = vision_stats.cmd_quota(str(sample_config))
        entries = {r["key"]: r for r in result}
        key = "google-free/gemini-3.6-flash|requests|60s"
        assert key in entries
        assert entries[key]["current"] == 2
        assert entries[key]["limit"] == 5
        assert entries[key]["pct"] == 40.0

    def test_quota_empty_window(self, sample_config):
        result = vision_stats.cmd_quota(str(sample_config))
        entries = {r["key"]: r for r in result}
        key = "google-free/gemini-3.6-flash|requests|60s"
        assert entries[key]["current"] == 0
        assert entries[key]["pct"] == 0.0


# ─── set (manual correction) ───

class TestSetQuota:
    def test_set_requests_count(self, sample_config):
        vision_stats.cmd_set("google-free/gemini-3.6-flash", "requests", 86400, 15)

        # Verify by reading the ratelimit file directly
        path = ratelimit._data_path("requests", 86400, "google-free__gemini-3.6-flash")
        data = json.loads(path.read_text())
        assert len(data["timestamps"]) == 15

    def test_set_tokens_count(self, sample_config):
        vision_stats.cmd_set("google-free/gemini-3.6-flash", "tokens", 60, 100000)

        path = ratelimit._data_path("tokens", 60, "google-free__gemini-3.6-flash")
        data = json.loads(path.read_text())
        total = sum(n for _, n in data["entries"])
        assert total == 100000

    def test_set_zero(self, sample_config):
        vision_stats.cmd_set("google-free/gemini-3.6-flash", "requests", 60, 0)

        path = ratelimit._data_path("requests", 60, "google-free__gemini-3.6-flash")
        data = json.loads(path.read_text())
        assert data["timestamps"] == []


# ─── reset ───

class TestReset:
    def test_reset_deletes_files(self, sample_config):
        # Create some ratelimit data
        q = {"metric": "requests", "window_seconds": 60, "limit": 5}
        ratelimit.record_usage("google-free__gemini-3.6-flash", q, 1)
        ratelimit.set_cooldown("google-free__gemini-3.6-flash", 60)

        deleted = vision_stats.cmd_reset("google-free/gemini-3.6-flash")
        assert deleted > 0

        # Verify files gone
        base = Path(ratelimit._BASE_DIR)
        remaining = [f for f in base.iterdir() if "google-free__gemini-3.6-flash" in f.name]
        assert len(remaining) == 0


# ─── clean ───

class TestClean:
    def test_clean_removes_stale_model_data(self, sample_config):
        # Create data for a model NOT in config
        q = {"metric": "requests", "window_seconds": 60, "limit": 5}
        ratelimit.record_usage("xfmass__xoppaddleocrv16", q, 1)

        result = vision_stats.cmd_clean(str(sample_config))
        assert "xfmass/xoppaddleocrv16" in result["stale_models"]

    def test_clean_expired_cooldown(self, sample_config):
        # Set a cooldown that's already expired
        ratelimit.set_cooldown("google-free__gemini-3.6-flash", 60, _now=time.time() - 120)

        result = vision_stats.cmd_clean(str(sample_config))
        assert result["expired_cooldowns"] >= 1
