"""Tests for adapters/common.make_client: NO_PROXY 中 `[::1]` 的防御。

背景：DSH 的 http-proxy 策略会向 NO_PROXY 合并 ['localhost','127.0.0.1','::1','[::1]']，
其中 `[::1]` 是为兼容 Node/undici 而加的；但 httpx 0.28 创建 Client 时把该条目
当 URL 解析会抛 Invalid port: ':1]'。make_client 在创建瞬间剔除 `[::1]`（保留
裸 `::1`），用毕恢复环境变量。

可观测契约（测试只断言这些）：
1. 坏 NO_PROXY（含 [::1]）下 make_client 能成功创建 Client
2. 创建后环境变量完全恢复原值（不污染进程 env 视图）
3. 干净环境不受影响
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from adapters.common import make_client


def _set_bad_proxy_env():
    """复刻 DSH 注入：NO_PROXY/no_proxy 同时含 ::1 与 [::1]。"""
    os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1,[::1]"
    os.environ["no_proxy"] = "localhost,127.0.0.1,::1,[::1]"
    os.environ["HTTP_PROXY"] = "http://127.0.0.1:10808"
    os.environ["HTTPS_PROXY"] = "http://127.0.0.1:10808"


class TestMakeClientProxyDefense:
    def test_creates_client_despite_bracketed_ipv6_in_no_proxy(self, monkeypatch):
        """坏 NO_PROXY（含 [::1]）下 make_client 必须能正常创建 Client。"""
        monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,::1,[::1]")
        monkeypatch.setenv("no_proxy", "localhost,127.0.0.1,::1,[::1]")
        with make_client() as client:
            assert client is not None

    def test_env_restored_after_client_creation(self):
        """创建后 NO_PROXY/no_proxy 必须恢复原值（不污染进程 env 视图）。"""
        _set_bad_proxy_env()
        before = os.environ["NO_PROXY"]
        with make_client():
            pass
        assert os.environ["NO_PROXY"] == before
        assert os.environ["no_proxy"] == before

    def test_clean_env_untouched(self, monkeypatch):
        """本来就没有 [::1] 的环境不受影响。"""
        monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
        monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")
        with make_client():
            assert os.environ["NO_PROXY"] == "localhost,127.0.0.1"

    def test_timeout_from_model_cfg(self):
        """model_cfg 路径：timeout 来自 make_timeout(model_cfg)（total 落在 read 上，
        connect 保持短握手超时默认值——httpx 0.28 无 .total，用 .read 断言）。"""
        with make_client({"timeout": 123}) as client:
            assert client._timeout.read == 123.0
            assert client._timeout.connect == 5.0  # DEFAULT_CONNECT_TIMEOUT

    def test_explicit_timeout_arg(self):
        """显式 timeout 参数路径（omniparser health_check 用）。"""
        with make_client(timeout=3.0) as client:
            assert client._timeout.connect == 3.0

    def test_default_model_cfg_none_ok(self):
        """model_cfg 为 None 时也能创建（health_check 路径不传 model_cfg）。"""
        with make_client() as client:
            assert client is not None

    def test_httpx_client_creation_broken_without_defense(self):
        """对照：裸 httpx.Client 在坏 NO_PROXY 下确实崩（证明防御必要性）。"""
        import httpx
        os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1,[::1]"
        os.environ["no_proxy"] = "localhost,127.0.0.1,::1,[::1]"
        with pytest.raises(Exception):
            httpx.Client(timeout=30)