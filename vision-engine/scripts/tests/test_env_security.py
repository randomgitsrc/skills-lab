"""Tests for env_security._parse_env_file: .env 解析兼容性。

重点：export 前缀（shell 风格 .env，不同工具写法不同）必须识别；
同时保持既有行为（注释行/空行跳过、引号剥离）不被破坏。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from env_security import _parse_env_file, resolve_key


@pytest.fixture
def env_file(tmp_path):
    def _write(content: str) -> Path:
        p = tmp_path / ".env"
        p.write_text(content, encoding="utf-8")
        return p
    return _write


class TestParseEnvFile:
    def test_plain_key_value(self, env_file):
        p = env_file("XROUTER_VISION_API_KEY=abc123\n")
        assert _parse_env_file(p) == {"XROUTER_VISION_API_KEY": "abc123"}

    def test_export_prefix(self, env_file):
        p = env_file("export XROUTER_VISION_API_KEY=abc123\n")
        assert _parse_env_file(p) == {"XROUTER_VISION_API_KEY": "abc123"}

    def test_export_prefix_multiple_spaces(self, env_file):
        p = env_file("export    XROUTER_VISION_API_KEY=abc123\n")
        assert _parse_env_file(p) == {"XROUTER_VISION_API_KEY": "abc123"}

    def test_export_prefix_tab(self, env_file):
        p = env_file("export\tXROUTER_VISION_API_KEY=abc123\n")
        assert _parse_env_file(p) == {"XROUTER_VISION_API_KEY": "abc123"}

    def test_key_starting_with_export_is_not_mangled(self, env_file):
        """KEY 名本身以 export 开头（exportX=1）不能被误判成 export 前缀。"""
        p = env_file("exportX=1\n")
        assert _parse_env_file(p) == {"exportX": "1"}

    def test_export_with_quoted_value(self, env_file):
        p = env_file('export KEY="quoted value"\n')
        assert _parse_env_file(p) == {"KEY": "quoted value"}

    def test_mixed_plain_and_export(self, env_file):
        p = env_file("A=1\nexport B=2\nC=3\n")
        assert _parse_env_file(p) == {"A": "1", "B": "2", "C": "3"}

    def test_comments_blank_and_malformed_lines_skipped(self, env_file):
        p = env_file("# comment\n\nexport GOOD=1\nno-equals-here\n")
        assert _parse_env_file(p) == {"GOOD": "1"}

    def test_value_with_equals_sign(self, env_file):
        p = env_file("export TOKEN=part1=part2\n")
        assert _parse_env_file(p) == {"TOKEN": "part1=part2"}

    def test_inline_comment_kept_in_value_by_design(self, env_file):
        """行内注释不剥离是既有设计（值里可能真的需要 #，如 URL 片段），
        记录行为防止将来被无意改变。"""
        p = env_file("URL=https://x.com/a#b\n")
        assert _parse_env_file(p) == {"URL": "https://x.com/a#b"}

    def test_missing_file_returns_empty(self, tmp_path):
        assert _parse_env_file(tmp_path / "nope.env") == {}


class TestResolveKeyPriority:
    def test_export_prefix_in_home_env_resolves(self, monkeypatch, tmp_path):
        """resolve_key 必须能经 _parse_env_file 解析出 export 写法（回归：此前的坑）。"""
        home = tmp_path / "home"
        home.mkdir()
        (home / ".env").write_text("export XROUTER_VISION_API_KEY=abc123\n", encoding="utf-8")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.delenv("XROUTER_VISION_API_KEY", raising=False)
        assert resolve_key("XROUTER_VISION_API_KEY") == "abc123"

    def test_os_environ_wins_over_home_env(self, monkeypatch, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        (home / ".env").write_text("export KEY=from-file\n", encoding="utf-8")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.setenv("KEY", "from-env")
        assert resolve_key("KEY") == "from-env"