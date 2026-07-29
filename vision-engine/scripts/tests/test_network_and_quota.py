"""Tests for quota recording in self-test/verify-grounding/OmniParser paths,
classify_http_error network_error/connect_timeout, make_timeout,
and base_url endpoint unreachable cache."""
import importlib.util
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import ratelimit
from adapters.common import AdapterHTTPError, classify_http_error

_spec = importlib.util.spec_from_file_location(
    "vision_analyze", Path(__file__).parent.parent / "vision-analyze.py"
)
vision_analyze = importlib.util.module_from_spec(_spec)
sys.modules["vision_analyze"] = vision_analyze
_spec.loader.exec_module(vision_analyze)


def _make_config(models):
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
            "locate": {
                "system_prompt": "find it",
                "requires": ["grounding"],
                "output_schema": "bbox_list",
                "query_mode": "targeted",
            },
            "locate-ui": {
                "system_prompt": "enumerate",
                "requires": ["ui-grounding"],
                "output_schema": "bbox_list",
                "query_mode": "enumerate_then_filter",
            },
        },
        "providers": {},
        "models": augmented,
    }


@pytest.fixture(autouse=True)
def redirect_ratelimit(monkeypatch, tmp_path):
    monkeypatch.setattr(ratelimit, "_BASE_DIR", str(tmp_path))


@pytest.fixture
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
def mock_resolve_key(monkeypatch):
    monkeypatch.setattr(vision_analyze.env_security, "resolve_key", lambda env: "dummy-key")


@pytest.fixture(autouse=True)
def clear_endpoint_cache():
    from adapters.common import clear_endpoint_cache
    clear_endpoint_cache()
    yield
    clear_endpoint_cache()


# ═══════════════════════════════════════════════════════════
# Bug fix: self-test records quota
# ═══════════════════════════════════════════════════════════

class TestSelfTestQuotaRecording:
    def test_self_test_records_requests_quota(self, fake_clock, mock_resolve_key, tmp_path):
        """self-test consumes server-side quota; local must record it."""
        config = _make_config([
            {"provider": "google-free", "name": "gemini-flash",
             "capabilities": ["general"],
             "quotas": [{"metric": "requests", "window_seconds": 86400, "limit": 20}]},
        ])

        def mock_call(model, api_key, system_prompt, user_prompt, image_paths, role_max_tokens=None):
            return {"text": "OK", "usage": None, "rate_limit_headers": {}}

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(vision_analyze, "call_model", mock_call)
        try:
            result = vision_analyze.run_self_test(config)
            assert result["results"][0]["status"] == "alive"
            q = {"metric": "requests", "window_seconds": 86400, "limit": 20}
            # After 1 recorded request, check_quota with projected=0 should show 1 used (not full)
            # Verify the record exists by checking that projecting 19 more would hit the limit
            assert ratelimit.check_quota("google-free__gemini-flash", q, projected=19) is True
            assert ratelimit.check_quota("google-free__gemini-flash", q, projected=18) is False
        finally:
            monkeypatch.undo()

    def test_self_test_records_tokens_quota(self, fake_clock, mock_resolve_key, tmp_path):
        """self-test with usage info should record token consumption."""
        config = _make_config([
            {"provider": "google", "name": "gemini-pro",
             "capabilities": ["general"],
             "quotas": [{"metric": "tokens", "window_seconds": 60, "limit": 100000}]},
        ])

        def mock_call(model, api_key, system_prompt, user_prompt, image_paths, role_max_tokens=None):
            return {"text": "OK",
                    "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
                    "rate_limit_headers": {}}

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(vision_analyze, "call_model", mock_call)
        try:
            result = vision_analyze.run_self_test(config)
            assert result["results"][0]["status"] == "alive"
            q = {"metric": "tokens", "window_seconds": 60, "limit": 100000}
            # 110 tokens recorded; projecting 99890 more should hit limit
            assert ratelimit.check_quota("google__gemini-pro", q, projected=99890) is True
            assert ratelimit.check_quota("google__gemini-pro", q, projected=99889) is False
        finally:
            monkeypatch.undo()


# ═══════════════════════════════════════════════════════════
# Bug fix: verify-grounding records quota
# ═══════════════════════════════════════════════════════════

class TestVerifyGroundingQuotaRecording:
    def test_verify_grounding_records_requests_quota(self, fake_clock, mock_resolve_key, tmp_path):
        config = _make_config([
            {"provider": "google", "name": "gemini-pro",
             "capabilities": ["general", "grounding"],
             "coordinate_convention": "gemini_1000",
             "quotas": [{"metric": "requests", "window_seconds": 86400, "limit": 250}]},
        ])

        def mock_call(model, api_key, system_prompt, user_prompt, image_paths, role_max_tokens=None):
            return {"text": '[{"label":"red rect","box":[400,400,600,600],"confidence":"high","type":"element"}]',
                    "usage": {"input_tokens": 200, "output_tokens": 50, "total_tokens": 250},
                    "rate_limit_headers": {}}

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(vision_analyze, "call_model", mock_call)
        monkeypatch.setattr(vision_analyze, "image_dimensions", lambda p: (1000, 1000))
        try:
            result = vision_analyze.run_verify_grounding(config, "gemini-pro")
            assert result["status"] == "completed"
            q = {"metric": "requests", "window_seconds": 86400, "limit": 250}
            # 1 request recorded; projecting 249 more should hit limit
            assert ratelimit.check_quota("google__gemini-pro", q, projected=249) is True
            assert ratelimit.check_quota("google__gemini-pro", q, projected=248) is False
        finally:
            monkeypatch.undo()


# ═══════════════════════════════════════════════════════════
# classify_http_error: network_error and connect_timeout
# ═══════════════════════════════════════════════════════════

class TestClassifyHTTPError:
    def test_connect_error_is_network_error(self):
        import httpx
        err = classify_http_error(httpx.ConnectError("connection refused"))
        assert err.kind == "network_error"

    def test_connect_timeout_is_connect_timeout(self):
        import httpx
        err = classify_http_error(httpx.ConnectTimeout("connect timed out"))
        assert err.kind == "connect_timeout"

    def test_read_timeout_still_timeout(self):
        import httpx
        err = classify_http_error(httpx.ReadTimeout("read timed out"))
        assert err.kind == "timeout"

    def test_write_timeout_still_timeout(self):
        import httpx
        err = classify_http_error(httpx.WriteTimeout("write timed out"))
        assert err.kind == "timeout"

    def test_pool_timeout_still_timeout(self):
        import httpx
        err = classify_http_error(httpx.PoolTimeout("pool timed out"))
        assert err.kind == "timeout"

    def test_429_still_quota_exceeded(self):
        import httpx
        resp = MagicMock()
        resp.status_code = 429
        err = classify_http_error(httpx.HTTPStatusError("429", request=MagicMock(), response=resp))
        assert err.kind == "quota_exceeded"

    def test_401_still_auth_error(self):
        import httpx
        resp = MagicMock()
        resp.status_code = 401
        err = classify_http_error(httpx.HTTPStatusError("401", request=MagicMock(), response=resp))
        assert err.kind == "auth_error"

    def test_500_still_server_error(self):
        import httpx
        resp = MagicMock()
        resp.status_code = 500
        err = classify_http_error(httpx.HTTPStatusError("500", request=MagicMock(), response=resp))
        assert err.kind == "server_error"


# ═══════════════════════════════════════════════════════════
# make_timeout: connect_timeout separation
# ═══════════════════════════════════════════════════════════

class TestMakeTimeout:
    def test_default_connect_timeout(self):
        from adapters.common import make_timeout, DEFAULT_CONNECT_TIMEOUT
        t = make_timeout({"timeout": 30})
        assert t.connect == DEFAULT_CONNECT_TIMEOUT
        assert t.read == 30
        assert t.write == 30

    def test_custom_connect_timeout(self):
        from adapters.common import make_timeout
        t = make_timeout({"timeout": 60, "connect_timeout": 10})
        assert t.connect == 10
        assert t.read == 60

    def test_default_total_timeout(self):
        from adapters.common import make_timeout
        t = make_timeout({})
        assert t.read == 60
        assert t.connect == 5

    def test_provider_level_connect_timeout_inherited(self):
        from adapters.common import make_timeout
        t = make_timeout({"timeout": 30, "connect_timeout": 8})
        assert t.connect == 8

    def test_flatten_providers_inherits_connect_timeout(self):
        """connect_timeout at provider level should propagate to models via _flatten_providers."""
        raw_config = {
            "providers": {
                "google": {
                    "base_url": "https://example.com",
                    "api_format": "google",
                    "api_key_env": "KEY",
                    "connect_timeout": 10,
                    "models": [
                        {"name": "m1", "capabilities": ["general"]},
                    ]
                }
            }
        }
        flat = vision_analyze._flatten_providers(raw_config)
        assert flat[0]["connect_timeout"] == 10


# ═══════════════════════════════════════════════════════════
# Endpoint unreachable cache
# ═══════════════════════════════════════════════════════════

class TestEndpointUnreachableCache:
    def test_mark_and_check(self):
        from adapters.common import mark_endpoint_unreachable, is_endpoint_unreachable
        mark_endpoint_unreachable("https://api.example.com")
        assert is_endpoint_unreachable("https://api.example.com") is True
        assert is_endpoint_unreachable("https://other.example.com") is False

    def test_ttl_expiry(self, monkeypatch):
        from adapters.common import mark_endpoint_unreachable, is_endpoint_unreachable
        import adapters.common as common_mod
        now = time.monotonic()
        monkeypatch.setattr(common_mod.time, "monotonic", lambda: now)
        mark_endpoint_unreachable("https://api.example.com", ttl=60)
        monkeypatch.setattr(common_mod.time, "monotonic", lambda: now + 61)
        assert is_endpoint_unreachable("https://api.example.com") is False

    def test_clear_cache(self):
        from adapters.common import mark_endpoint_unreachable, is_endpoint_unreachable, clear_endpoint_cache
        mark_endpoint_unreachable("https://api.example.com")
        clear_endpoint_cache()
        assert is_endpoint_unreachable("https://api.example.com") is False

    def test_shared_base_url_both_cached(self):
        from adapters.common import mark_endpoint_unreachable, is_endpoint_unreachable
        url = "https://generativelanguage.googleapis.com/v1beta"
        mark_endpoint_unreachable(url)
        assert is_endpoint_unreachable(url) is True


# ═══════════════════════════════════════════════════════════
# Integration: endpoint cache in fallback loop
# ═══════════════════════════════════════════════════════════

class TestEndpointCacheInFallback:
    def test_network_error_marks_endpoint_unreachable(self, fake_clock, mock_resolve_key):
        """When a model gets network_error, same base_url models are skipped."""
        from adapters.common import mark_endpoint_unreachable
        config = _make_config([
            {"provider": "google", "name": "m1", "capabilities": ["general"],
             "base_url": "https://google.api.com/v1"},
            {"provider": "google", "name": "m2", "capabilities": ["general"],
             "base_url": "https://google.api.com/v1"},
            {"provider": "alibailian", "name": "m3", "capabilities": ["general"],
             "base_url": "https://ali.api.com/v1"},
        ])
        calls = []

        def mock_call(model, api_key, system_prompt, user_prompt, image_paths, role_max_tokens=None):
            ref = vision_analyze.model_ref(model)
            calls.append(ref)
            if ref == "google/m1":
                raise AdapterHTTPError("network_error", "unreachable")
            return {"text": "ok", "usage": None, "rate_limit_headers": {}}

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(vision_analyze, "call_model", mock_call)
        try:
            result = vision_analyze.run_text_role(config, "quick", config["roles"]["quick"], "test", [], False)
            assert result["status"] == "success"
            assert result["model_used"] == "alibailian/m3"
            statuses = [a["status"] for a in result["attempts"]]
            assert "network_error" in statuses
            assert "endpoint_unreachable" in statuses
        finally:
            monkeypatch.undo()

    def test_connect_timeout_marks_endpoint_unreachable(self, fake_clock, mock_resolve_key):
        """ConnectTimeout should mark endpoint unreachable."""
        config = _make_config([
            {"provider": "google", "name": "m1", "capabilities": ["general"],
             "base_url": "https://google.api.com/v1"},
            {"provider": "alibailian", "name": "m2", "capabilities": ["general"],
             "base_url": "https://ali.api.com/v1"},
        ])

        def mock_call(model, api_key, system_prompt, user_prompt, image_paths, role_max_tokens=None):
            ref = vision_analyze.model_ref(model)
            if ref == "google/m1":
                raise AdapterHTTPError("connect_timeout", "connect timed out")
            return {"text": "ok", "usage": None, "rate_limit_headers": {}}

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(vision_analyze, "call_model", mock_call)
        try:
            result = vision_analyze.run_text_role(config, "quick", config["roles"]["quick"], "test", [], False)
            assert result["status"] == "success"
            assert result["model_used"] == "alibailian/m2"
        finally:
            monkeypatch.undo()

    def test_read_timeout_does_not_mark_endpoint_unreachable(self, fake_clock, mock_resolve_key):
        """ReadTimeout should NOT mark endpoint unreachable — provider is reachable, just slow."""
        config = _make_config([
            {"provider": "google", "name": "m1", "capabilities": ["general"],
             "base_url": "https://google.api.com/v1"},
            {"provider": "google", "name": "m2", "capabilities": ["general"],
             "base_url": "https://google.api.com/v1"},
        ])

        def mock_call(model, api_key, system_prompt, user_prompt, image_paths, role_max_tokens=None):
            ref = vision_analyze.model_ref(model)
            if ref == "google/m1":
                raise AdapterHTTPError("timeout", "read timed out")
            return {"text": "ok", "usage": None, "rate_limit_headers": {}}

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(vision_analyze, "call_model", mock_call)
        try:
            result = vision_analyze.run_text_role(config, "quick", config["roles"]["quick"], "test", [], False)
            assert result["status"] == "success"
            assert result["model_used"] == "google/m2"
            statuses = [a["status"] for a in result["attempts"]]
            assert "timeout" in statuses
            assert "endpoint_unreachable" not in statuses
        finally:
            monkeypatch.undo()

    def test_shared_base_url_across_providers(self, fake_clock, mock_resolve_key):
        """google and google-free share base_url; one failure caches out both."""
        config = _make_config([
            {"provider": "google", "name": "m1", "capabilities": ["general"],
             "base_url": "https://generativelanguage.googleapis.com/v1beta"},
            {"provider": "google-free", "name": "m2", "capabilities": ["general"],
             "base_url": "https://generativelanguage.googleapis.com/v1beta"},
            {"provider": "alibailian", "name": "m3", "capabilities": ["general"],
             "base_url": "https://ali.api.com/v1"},
        ])

        def mock_call(model, api_key, system_prompt, user_prompt, image_paths, role_max_tokens=None):
            ref = vision_analyze.model_ref(model)
            if ref == "google/m1":
                raise AdapterHTTPError("network_error", "unreachable")
            return {"text": "ok", "usage": None, "rate_limit_headers": {}}

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(vision_analyze, "call_model", mock_call)
        try:
            result = vision_analyze.run_text_role(config, "quick", config["roles"]["quick"], "test", [], False)
            assert result["status"] == "success"
            assert result["model_used"] == "alibailian/m3"
            statuses = [a["status"] for a in result["attempts"]]
            assert "endpoint_unreachable" in statuses
        finally:
            monkeypatch.undo()


# ═══════════════════════════════════════════════════════════
# self-test unreachable status
# ═══════════════════════════════════════════════════════════

class TestSelfTestUnreachable:
    def test_network_error_shows_unreachable(self, fake_clock, mock_resolve_key):
        config = _make_config([
            {"provider": "google", "name": "m1", "capabilities": ["general"]},
        ])

        def mock_call(model, api_key, system_prompt, user_prompt, image_paths, role_max_tokens=None):
            raise AdapterHTTPError("network_error", "unreachable")

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(vision_analyze, "call_model", mock_call)
        try:
            result = vision_analyze.run_self_test(config)
            assert result["results"][0]["status"] == "unreachable"
            assert result["results"][0]["error_kind"] == "network_error"
            assert result["summary"]["unreachable"] == 1
        finally:
            monkeypatch.undo()

    def test_connect_timeout_shows_unreachable(self, fake_clock, mock_resolve_key):
        config = _make_config([
            {"provider": "google", "name": "m1", "capabilities": ["general"]},
        ])

        def mock_call(model, api_key, system_prompt, user_prompt, image_paths, role_max_tokens=None):
            raise AdapterHTTPError("connect_timeout", "connect timed out")

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(vision_analyze, "call_model", mock_call)
        try:
            result = vision_analyze.run_self_test(config)
            assert result["results"][0]["status"] == "unreachable"
            assert result["results"][0]["error_kind"] == "connect_timeout"
        finally:
            monkeypatch.undo()

    def test_server_error_shows_dead(self, fake_clock, mock_resolve_key):
        config = _make_config([
            {"provider": "google", "name": "m1", "capabilities": ["general"]},
        ])

        def mock_call(model, api_key, system_prompt, user_prompt, image_paths, role_max_tokens=None):
            raise AdapterHTTPError("server_error", "500")

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(vision_analyze, "call_model", mock_call)
        try:
            result = vision_analyze.run_self_test(config)
            assert result["results"][0]["status"] == "dead"
        finally:
            monkeypatch.undo()

    def test_self_test_bypasses_endpoint_cache(self, fake_clock, mock_resolve_key):
        """self-test should test current network, not respect cached unreachable state."""
        from adapters.common import mark_endpoint_unreachable
        config = _make_config([
            {"provider": "google", "name": "m1", "capabilities": ["general"],
             "base_url": "https://google.api.com/v1"},
        ])
        mark_endpoint_unreachable("https://google.api.com/v1")

        def mock_call(model, api_key, system_prompt, user_prompt, image_paths, role_max_tokens=None):
            return {"text": "OK", "usage": None, "rate_limit_headers": {}}

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(vision_analyze, "call_model", mock_call)
        try:
            result = vision_analyze.run_self_test(config)
            assert result["results"][0]["status"] == "alive"
        finally:
            monkeypatch.undo()


# ═══════════════════════════════════════════════════════════
# --clear-quotas clears endpoint cache
# ═══════════════════════════════════════════════════════════

class TestClearQuotasClearsEndpointCache:
    def test_clear_endpoint_cache_function(self):
        from adapters.common import mark_endpoint_unreachable, is_endpoint_unreachable, clear_endpoint_cache
        mark_endpoint_unreachable("https://api.example.com")
        assert is_endpoint_unreachable("https://api.example.com") is True
        clear_endpoint_cache()
        assert is_endpoint_unreachable("https://api.example.com") is False


# ═══════════════════════════════════════════════════════════
# 429 quota sync: fill local count to limit on 429
# ═══════════════════════════════════════════════════════════

class TestQuotaSyncOn429:
    def test_429_fills_requests_quota_to_limit(self, fake_clock, mock_resolve_key):
        """When 429 fires, local requests count should be filled to limit
        so quota check blocks future attempts until window expires."""
        config = _make_config([
            {"provider": "google-free", "name": "m1", "capabilities": ["general"],
             "quotas": [{"metric": "requests", "window_seconds": 60, "limit": 5}],
             "cooldown_seconds": 60},
            {"provider": "alibailian", "name": "m2", "capabilities": ["general"]},
        ])

        def mock_call(model, api_key, system_prompt, user_prompt, image_paths, role_max_tokens=None):
            ref = vision_analyze.model_ref(model)
            if ref == "google-free/m1":
                raise AdapterHTTPError("quota_exceeded", "rate limited", 429)
            return {"text": "ok", "usage": None, "rate_limit_headers": {}}

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(vision_analyze, "call_model", mock_call)
        try:
            result = vision_analyze.run_text_role(config, "quick", config["roles"]["quick"], "test", [], False)
            assert result["model_used"] == "alibailian/m2"
            q = {"metric": "requests", "window_seconds": 60, "limit": 5}
            assert ratelimit.check_quota("google-free__m1", q) is True
        finally:
            monkeypatch.undo()

    def test_429_fills_tokens_quota_to_limit(self, fake_clock, mock_resolve_key):
        """When 429 fires on a tokens quota, local count should be filled to limit."""
        config = _make_config([
            {"provider": "google", "name": "m1", "capabilities": ["general"],
             "quotas": [{"metric": "tokens", "window_seconds": 60, "limit": 1000}],
             "cooldown_seconds": 60},
            {"provider": "alibailian", "name": "m2", "capabilities": ["general"]},
        ])

        def mock_call(model, api_key, system_prompt, user_prompt, image_paths, role_max_tokens=None):
            ref = vision_analyze.model_ref(model)
            if ref == "google/m1":
                raise AdapterHTTPError("quota_exceeded", "rate limited", 429)
            return {"text": "ok", "usage": None, "rate_limit_headers": {}}

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(vision_analyze, "call_model", mock_call)
        try:
            result = vision_analyze.run_text_role(config, "quick", config["roles"]["quick"], "test", [], False)
            assert result["model_used"] == "alibailian/m2"
            q = {"metric": "tokens", "window_seconds": 60, "limit": 1000}
            assert ratelimit.check_quota("google__m1", q) is True
        finally:
            monkeypatch.undo()

    def test_429_partial_count_fills_gap(self, fake_clock, mock_resolve_key):
        """If local already has 3/5 requests, 429 should fill remaining 2 to reach limit."""
        config = _make_config([
            {"provider": "google-free", "name": "m1", "capabilities": ["general"],
             "quotas": [{"metric": "requests", "window_seconds": 60, "limit": 5}],
             "cooldown_seconds": 60},
            {"provider": "alibailian", "name": "m2", "capabilities": ["general"]},
        ])
        q = {"metric": "requests", "window_seconds": 60, "limit": 5}
        ratelimit.record_usage("google-free__m1", q, 1, _now=fake_clock.now)
        ratelimit.record_usage("google-free__m1", q, 1, _now=fake_clock.now)
        ratelimit.record_usage("google-free__m1", q, 1, _now=fake_clock.now)

        def mock_call(model, api_key, system_prompt, user_prompt, image_paths, role_max_tokens=None):
            ref = vision_analyze.model_ref(model)
            if ref == "google-free/m1":
                raise AdapterHTTPError("quota_exceeded", "rate limited", 429)
            return {"text": "ok", "usage": None, "rate_limit_headers": {}}

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(vision_analyze, "call_model", mock_call)
        try:
            result = vision_analyze.run_text_role(config, "quick", config["roles"]["quick"], "test", [], False)
            assert result["model_used"] == "alibailian/m2"
            assert ratelimit.check_quota("google-free__m1", q) is True
        finally:
            monkeypatch.undo()
