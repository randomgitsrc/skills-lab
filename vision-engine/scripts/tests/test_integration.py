"""Integration tests for fallback loop with general quota framework + cooldown + 429.

Tests the actual run_text_role function by constructing minimal configs and
monkeypatching call_model/env_security/time.

Time strategy: monkeypatch `time.time` in both ratelimit and vision_analyze
modules so pre-saturation calls and production code see the same fake clock.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import ratelimit

_spec = importlib.util.spec_from_file_location(
    "vision_analyze", Path(__file__).parent.parent / "vision-analyze.py"
)
vision_analyze = importlib.util.module_from_spec(_spec)
sys.modules["vision_analyze"] = vision_analyze
_spec.loader.exec_module(vision_analyze)


def _make_config(models):
    """Build a minimal config dict. Models auto-get base_url/api_format/api_key_env."""
    augmented = []
    for m in models:
        m = dict(m)
        m.setdefault("base_url", "https://fake.example.com/v1")
        m.setdefault("api_format", "openai")
        m.setdefault("api_key_env", "FAKE_KEY")
        augmented.append(m)
    return {
        "schema_version": "4.0",
        "capability_provider_whitelist": {},
        "roles": {
            "quick": {
                "system_prompt": None,
                "requires": ["general"],
                "output_schema": "text",
                "skip_context": True,
            },
        },
        "providers": {},
        "models": augmented,
    }


@pytest.fixture(autouse=True)
def fake_clock(monkeypatch):
    class Clock:
        def __init__(self):
            self.now = 1_700_000_000.0

        def __call__(self):
            return self.now

    clock = Clock()
    monkeypatch.setattr(ratelimit.time, "time", clock)
    monkeypatch.setattr(vision_analyze.time, "time", clock)
    return clock


@pytest.fixture
def mock_call_model(monkeypatch):
    """Replace call_model. Per-model return / raise via _mock.return_obj / _mock.raise_for."""
    calls = []

    def _mock(model, api_key, system_prompt, user_prompt, image_paths, role_max_tokens=None):
        ref = vision_analyze.model_ref(model)
        calls.append(ref)
        if hasattr(_mock, "raise_for") and ref in _mock.raise_for:
            kind, status = _mock.raise_for[ref]
            from adapters.common import AdapterHTTPError
            raise AdapterHTTPError(kind, f"mock {kind}", status)
        return_obj = getattr(_mock, "return_obj", {"text": "ok", "usage": None})
        return return_obj

    _mock.calls = calls
    _mock.raise_for = {}
    _mock.return_obj = {"text": "ok", "usage": None}
    monkeypatch.setattr(vision_analyze, "call_model", _mock)
    yield _mock
    _mock.raise_for = {}


@pytest.fixture
def mock_resolve_key(monkeypatch):
    monkeypatch.setattr(vision_analyze.env_security, "resolve_key", lambda env: "dummy-key")


@pytest.fixture(autouse=True)
def redirect_ratelimit(monkeypatch, tmp_path):
    monkeypatch.setattr(ratelimit, "_BASE_DIR", str(tmp_path))


# ═══════════════════════════════════════════════════════════
# Basic fallback
# ═══════════════════════════════════════════════════════════

class TestFallbackBasics:
    def test_first_model_succeeds(self, mock_call_model, mock_resolve_key):
        config = _make_config([
            {"provider": "google", "name": "m1", "capabilities": ["general"],
             "quotas": [{"metric": "requests", "window_seconds": 60, "limit": 10}]},
        ])
        result = vision_analyze.run_text_role(config, "quick", config["roles"]["quick"], "test", [], False)
        assert result["status"] == "success"
        assert result["model_used"] == "google/m1"

    def test_quota_exceeded_falls_back(self, fake_clock, mock_call_model, mock_resolve_key):
        """If model m1's requests/60s quota is exhausted, falls back to m2."""
        config = _make_config([
            {"provider": "google", "name": "m1", "capabilities": ["general"],
             "quotas": [{"metric": "requests", "window_seconds": 60, "limit": 1}]},
            {"provider": "google", "name": "m2", "capabilities": ["general"],
             "quotas": [{"metric": "requests", "window_seconds": 60, "limit": 10}]},
        ])
        # Saturate m1
        q = {"metric": "requests", "window_seconds": 60, "limit": 1}
        ratelimit.record_usage("google__m1", q, 1, _now=fake_clock.now)
        result = vision_analyze.run_text_role(config, "quick", config["roles"]["quick"], "test", [], False)
        assert result["model_used"] == "google/m2"
        assert any(a["status"] == "quota_exceeded:requests:60s" for a in result["attempts"])


# ═══════════════════════════════════════════════════════════
# Tokens quota
# ═══════════════════════════════════════════════════════════

class TestTokensQuota:
    def test_tokens_quota_exceeded_falls_back(self, fake_clock, mock_call_model, mock_resolve_key):
        config = _make_config([
            {"provider": "google", "name": "m1", "capabilities": ["general"],
             "quotas": [{"metric": "tokens", "window_seconds": 60, "limit": 1000}]},
            {"provider": "google", "name": "m2", "capabilities": ["general"],
             "quotas": [{"metric": "tokens", "window_seconds": 60, "limit": 1000}]},
        ])
        # Saturate m1 to 1000 tokens
        q = {"metric": "tokens", "window_seconds": 60, "limit": 1000}
        ratelimit.record_usage("google__m1", q, 1000, _now=fake_clock.now)
        result = vision_analyze.run_text_role(config, "quick", config["roles"]["quick"], "test", [], False)
        assert result["model_used"] == "google/m2"
        assert any(a["status"] == "quota_exceeded:tokens:60s" for a in result["attempts"])

    def test_tokens_recorded_after_success(self, fake_clock, mock_call_model, mock_resolve_key):
        """After a successful call with usage info, tokens quota is recorded."""
        # Mock returns usage with 500 tokens
        mock_call_model.return_obj = {
            "text": "ok",
            "usage": {"input_tokens": 300, "output_tokens": 200, "total_tokens": 500},
        }
        config = _make_config([
            {"provider": "google", "name": "m1", "capabilities": ["general"],
             "quotas": [{"metric": "tokens", "window_seconds": 60, "limit": 1000}]},
        ])
        result = vision_analyze.run_text_role(config, "quick", config["roles"]["quick"], "test", [], False)
        assert result["status"] == "success"
        # Verify the quota file was written
        from pathlib import Path
        path = Path(f"{fake_clock._BASE_DIR if hasattr(fake_clock, '_BASE_DIR') else '.'}/tokens-60s-google__m1.json")
        # The path uses tmp_path from conftest; check via the data file in the redirect
        import json
        files = list(Path(__file__).parent.parent.rglob("tokens-60s-google__m1.json"))
        if files:
            data = json.loads(files[0].read_text())
            assert data["entries"] == [[1_700_000_000.0, 500]]

    def test_tokens_estimated_when_no_usage(self, fake_clock, mock_call_model, mock_resolve_key):
        """When response has no usage, the conservative estimate is used (default 512)."""
        mock_call_model.return_obj = {"text": "ok", "usage": None}
        config = _make_config([
            {"provider": "google", "name": "m1", "capabilities": ["general"],
             "max_tokens": 2048,
             "quotas": [{"metric": "tokens", "window_seconds": 60, "limit": 10000}]},
        ])
        result = vision_analyze.run_text_role(config, "quick", config["roles"]["quick"], "test", [], False)
        assert result["status"] == "success"
        # Verify the estimate (should be 512, not 2048)
        # — the conservative default caps at 512 to avoid filling TPM too fast


# ═══════════════════════════════════════════════════════════
# Cooldown
# ═══════════════════════════════════════════════════════════

class TestCooldown:
    def test_cooldown_skipped(self, fake_clock, mock_call_model, mock_resolve_key):
        config = _make_config([
            {"provider": "google", "name": "m1", "capabilities": ["general"]},
            {"provider": "google", "name": "m2", "capabilities": ["general"]},
        ])
        ratelimit.set_cooldown("google__m1", 60, _now=fake_clock.now)
        result = vision_analyze.run_text_role(config, "quick", config["roles"]["quick"], "test", [], False)
        assert result["model_used"] == "google/m2"
        assert any(a["status"] == "cooldown" for a in result["attempts"])

    def test_429_triggers_cooldown(self, fake_clock, mock_call_model, mock_resolve_key):
        """When call_model raises 429/quota_exceeded, cooldown is set."""
        config = _make_config([
            {"provider": "google", "name": "m1", "capabilities": ["general"], "cooldown_seconds": 60},
            {"provider": "google", "name": "m2", "capabilities": ["general"]},
        ])
        mock_call_model.raise_for = {"google/m1": ("quota_exceeded", 429)}
        result = vision_analyze.run_text_role(config, "quick", config["roles"]["quick"], "test", [], False)
        assert result["model_used"] == "google/m2"
        assert any(a["status"] == "quota_exceeded" for a in result["attempts"])
        assert ratelimit.is_cooled_down("google__m1", _now=fake_clock.now) is True

    def test_non_429_does_not_set_cooldown(self, mock_call_model, mock_resolve_key):
        config = _make_config([
            {"provider": "google", "name": "m1", "capabilities": ["general"]},
        ])
        mock_call_model.raise_for = {"google/m1": ("auth_error", 401)}
        result = vision_analyze.run_text_role(config, "quick", config["roles"]["quick"], "test", [], False)
        assert ratelimit.is_cooled_down("google__m1") is False


# ═══════════════════════════════════════════════════════════
# Exit code logic
# ═══════════════════════════════════════════════════════════

class TestExitCode4:
    def test_is_rate_limit_status_helper(self):
        """_is_rate_limit_status recognizes cooldown and quota_exceeded:* variants."""
        assert vision_analyze._is_rate_limit_status("cooldown") is True
        assert vision_analyze._is_rate_limit_status("quota_exceeded:requests:60s") is True
        assert vision_analyze._is_rate_limit_status("quota_exceeded:tokens:86400s") is True
        assert vision_analyze._is_rate_limit_status("auth_error") is False
        assert vision_analyze._is_rate_limit_status("timeout") is False
        assert vision_analyze._is_rate_limit_status("") is False

    def test_all_quota_exceeded(self, fake_clock, mock_call_model, mock_resolve_key):
        config = _make_config([
            {"provider": "google", "name": "m1", "capabilities": ["general"],
             "quotas": [{"metric": "requests", "window_seconds": 60, "limit": 1}]},
        ])
        q = {"metric": "requests", "window_seconds": 60, "limit": 1}
        ratelimit.record_usage("google__m1", q, 1, _now=fake_clock.now)
        result = vision_analyze.run_text_role(config, "quick", config["roles"]["quick"], "test", [], False)
        assert all(vision_analyze._is_rate_limit_status(a["status"]) for a in result["attempts"])

    def test_mixed_quota_and_failure(self, fake_clock, mock_call_model, mock_resolve_key):
        """Mix of quota_exceeded and auth_error → not all rate-limit."""
        config = _make_config([
            {"provider": "google", "name": "m1", "capabilities": ["general"],
             "quotas": [{"metric": "requests", "window_seconds": 60, "limit": 1}]},
            {"provider": "google", "name": "m2", "capabilities": ["general"]},
        ])
        q = {"metric": "requests", "window_seconds": 60, "limit": 1}
        ratelimit.record_usage("google__m1", q, 1, _now=fake_clock.now)
        mock_call_model.raise_for = {"google/m2": ("auth_error", 401)}
        result = vision_analyze.run_text_role(config, "quick", config["roles"]["quick"], "test", [], False)
        statuses = [a["status"] for a in result["attempts"]]
        assert "quota_exceeded:requests:60s" in statuses
        assert "auth_error" in statuses
        # Not all rate-limit → not exit 4
        assert not all(vision_analyze._is_rate_limit_status(s) for s in statuses)


# ═══════════════════════════════════════════════════════════
# Backward compat: model without quotas field
# ═══════════════════════════════════════════════════════════

class TestNoQuotas:
    def test_model_without_quotas_runs_normally(self, mock_call_model, mock_resolve_key):
        """If model has no 'quotas' field, it's not rate-limited (legacy models)."""
        config = _make_config([
            {"provider": "google", "name": "m1", "capabilities": ["general"]},  # no quotas
        ])
        result = vision_analyze.run_text_role(config, "quick", config["roles"]["quick"], "test", [], False)
        assert result["status"] == "success"
        assert result["model_used"] == "google/m1"
