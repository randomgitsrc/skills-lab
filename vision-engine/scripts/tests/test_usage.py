"""Tests for adapters/usage.py: extract token usage from various provider response formats."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from adapters import usage


# ═══════════════════════════════════════════════════════════
# Google Gemini format
# ═══════════════════════════════════════════════════════════

class TestGoogleUsage:
    def test_extracts_google_usage(self):
        """Google uses usageMetadata with promptTokenCount/candidatesTokenCount/totalTokenCount."""
        response = {
            "candidates": [...],
            "usageMetadata": {
                "promptTokenCount": 100,
                "candidatesTokenCount": 50,
                "totalTokenCount": 150,
            },
        }
        result = usage.parse_usage("google", response)
        assert result == {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
        }

    def test_google_missing_usage_returns_none(self):
        """No usageMetadata → None (caller can fall back to estimation)."""
        response = {"candidates": [...]}
        assert usage.parse_usage("google", response) is None

    def test_google_partial_usage(self):
        """Only totalTokenCount present, prompt/candidates missing → None (incomplete data)."""
        response = {"usageMetadata": {"totalTokenCount": 100}}
        assert usage.parse_usage("google", response) is None


# ═══════════════════════════════════════════════════════════
# OpenAI format (also Qwen-VL via compatible mode)
# ═══════════════════════════════════════════════════════════

class TestOpenAIUsage:
    def test_extracts_openai_usage(self):
        response = {
            "choices": [...],
            "usage": {
                "prompt_tokens": 200,
                "completion_tokens": 80,
                "total_tokens": 280,
            },
        }
        result = usage.parse_usage("openai", response)
        assert result == {
            "input_tokens": 200,
            "output_tokens": 80,
            "total_tokens": 280,
        }

    def test_openai_missing_usage_returns_none(self):
        response = {"choices": [...]}
        assert usage.parse_usage("openai", response) is None

    def test_openai_partial_usage(self):
        response = {"usage": {"total_tokens": 100}}
        assert usage.parse_usage("openai", response) is None


# ═══════════════════════════════════════════════════════════
# Anthropic format
# ═══════════════════════════════════════════════════════════

class TestAnthropicUsage:
    def test_extracts_anthropic_usage(self):
        response = {
            "content": [...],
            "usage": {
                "input_tokens": 150,
                "output_tokens": 60,
            },
        }
        result = usage.parse_usage("anthropic", response)
        # Anthropic doesn't return total_tokens; compute it
        assert result == {
            "input_tokens": 150,
            "output_tokens": 60,
            "total_tokens": 210,
        }

    def test_anthropic_missing_usage_returns_none(self):
        response = {"content": [...]}
        assert usage.parse_usage("anthropic", response) is None


# ═══════════════════════════════════════════════════════════
# Omniparser (no token concept)
# ═══════════════════════════════════════════════════════════

class TestOmniparserUsage:
    def test_omniparser_always_none(self):
        """Omniparser has no token accounting; always None regardless of input."""
        assert usage.parse_usage("omniparser", {}) is None
        assert usage.parse_usage("omniparser", {"anything": "goes"}) is None


# ═══════════════════════════════════════════════════════════
# Unknown format
# ═══════════════════════════════════════════════════════════

class TestUnknownFormat:
    def test_unknown_format_returns_none(self):
        """Unknown api_format → None (caller bug, not silent fail)."""
        assert usage.parse_usage("claude-new-format", {}) is None
        assert usage.parse_usage("foobar", {"usage": {}}) is None
