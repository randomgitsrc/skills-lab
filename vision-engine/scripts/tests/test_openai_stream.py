"""Tests for adapters/openai_api.py SSE streaming support.

背景：部分自营链路（Cloudflare 边缘 100s 源站超时）会掐线非流式长任务（524）。
修复 = 模型级 stream:true 走 SSE。这里直测 _consume_sse_line 的行解析逻辑：
chunk 拼装、[DONE] 终止、usage 末帧提取、坏行容错。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from adapters.openai_api import _consume_sse_line


def _sse_line(obj) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False)


class TestConsumeSseLine:
    def test_accumulates_content_deltas(self):
        """多个 content chunk 依次拼进 parts。"""
        parts: list[str] = []
        for frag in ("Hello", ", ", "world"):
            stop, usage = _consume_sse_line(
                _sse_line({"choices": [{"delta": {"content": frag}}]}), parts)
            assert stop is False
            assert usage is None
        assert "".join(parts) == "Hello, world"

    def test_done_stops_stream(self):
        """[DONE] 帧返回 stop=True。"""
        parts: list[str] = []
        stop, usage = _consume_sse_line("data: [DONE]", parts)
        assert stop is True
        assert usage is None
        assert parts == []

    def test_usage_chunk_captured_without_content(self):
        """末尾 usage 帧（无 content）返回整 chunk（顶层含 usage 键，供 parse_usage 解析），不 append content。"""
        parts: list[str] = []
        stop, usage = _consume_sse_line(
            _sse_line({"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                                "total_tokens": 15}}), parts)
        assert stop is False
        assert usage["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        assert parts == []

    def test_usage_chunk_with_content_both_handled(self):
        """同时有 content 和 usage 的帧：content 进 parts，整 chunk 返回。"""
        parts: list[str] = []
        stop, usage = _consume_sse_line(
            _sse_line({"choices": [{"delta": {"content": "x"}}],
                       "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}),
            parts)
        assert stop is False
        assert parts == ["x"]
        assert usage["usage"]["total_tokens"] == 2

    def test_ignores_empty_and_non_data_lines(self):
        """空行 / 非 data 前缀行（keep-alive 注释等）静默忽略。"""
        parts: list[str] = []
        for line in ("", "\n", ": keep-alive", "event: ping"):
            stop, usage = _consume_sse_line(line, parts)
            assert stop is False
            assert usage is None
        assert parts == []

    def test_ignores_malformed_json_payload(self):
        """data: 后跟非 JSON：忽略不中断流。"""
        parts: list[str] = []
        stop, usage = _consume_sse_line("data: {{{{broken", parts)
        assert stop is False
        assert usage is None
        assert parts == []

    def test_delta_without_content_is_noop(self):
        """delta 存在但无 content（如 role 帧）：noop。"""
        parts: list[str] = []
        stop, usage = _consume_sse_line(
            _sse_line({"choices": [{"delta": {"role": "assistant"}}]}), parts)
        assert stop is False
        assert usage is None
        assert parts == []