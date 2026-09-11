"""公共工具：图片编码、mimetype 推断、HTTP 请求封装、endpoint 不可达缓存。"""
import base64
import mimetypes
import os
import time
from pathlib import Path

import httpx


def guess_media_type(image_path: str) -> str:
    """根据文件扩展名推断 media_type，不写死为 image/png（v1 的已知 bug）。"""
    mt, _ = mimetypes.guess_type(image_path)
    if mt in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        return mt
    # 兜底：读文件头判断
    with open(image_path, "rb") as f:
        head = f.read(12)
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if head.startswith(b"GIF8"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"  # 最后兜底


def encode_image(image_path: str) -> tuple[str, str]:
    """返回 (base64编码, media_type)"""
    media_type = guess_media_type(image_path)
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return b64, media_type


def image_dimensions(image_path: str) -> tuple[int, int]:
    """读取图片实际像素宽高，用于坐标归一化换算。不依赖模型自报的尺寸。"""
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            return img.width, img.height
    except ImportError:
        # 没装 Pillow 时退化：无法精确换算像素坐标，调用方应据此跳过pixel类convention的精确转换
        return (0, 0)


class AdapterHTTPError(RuntimeError):
    """封装HTTP层错误，调用方据此归类，不回显 headers 或 key（§3.1 报错脱敏要求）。"""

    def __init__(self, kind: str, message: str, status_code: int | None = None):
        self.kind = kind  # quota_exceeded | timeout | connect_timeout | network_error | auth_error | server_error | image_too_large | unknown
        self.status_code = status_code
        super().__init__(message)


def classify_http_error(exc: Exception) -> "AdapterHTTPError":
    # 检查顺序：ConnectTimeout → TimeoutException → ConnectError
    # ConnectTimeout 必须先于 TimeoutException 检查，才能区分"握手超时"和"响应超时"；
    # ConnectError 与 TimeoutException 是独立的继承分支，互不包含
    if isinstance(exc, httpx.ConnectTimeout):
        return AdapterHTTPError("connect_timeout", "connection timed out (network may be unreachable)")
    if isinstance(exc, httpx.TimeoutException):
        return AdapterHTTPError("timeout", "request timed out")
    if isinstance(exc, httpx.ConnectError):
        return AdapterHTTPError("network_error", "network unreachable or DNS failure")
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429:
            return AdapterHTTPError("quota_exceeded", "rate limited", code)
        if code in (401, 403):
            return AdapterHTTPError("auth_error", "authentication failed (check env var, not shown)", code)
        if code == 413:
            return AdapterHTTPError("image_too_large", "image exceeds provider size limit", code)
        if code >= 500:
            return AdapterHTTPError("server_error", f"provider server error {code}", code)
        return AdapterHTTPError("unknown", f"http error {code}", code)
    return AdapterHTTPError("unknown", str(exc))


DEFAULT_CONNECT_TIMEOUT = 5


def make_timeout(model_cfg: dict) -> httpx.Timeout:
    """从 model 配置构建 httpx.Timeout，connect 阶段用短超时快速检测不可达。"""
    total = model_cfg.get("timeout", 60)
    connect = model_cfg.get("connect_timeout", DEFAULT_CONNECT_TIMEOUT)
    return httpx.Timeout(timeout=total, connect=connect)


# NO_PROXY 里出现 `[::1]`（带方括号的 IPv6 字面量）会让 httpx 0.28 在创建
# Client 时把该条目当 URL 解析并崩溃（Invalid port: ':1]'）。
# 背景：DSH 的 http-proxy 策略（policy.ts LOOPBACK_NO_PROXY）会把
# ['localhost', '127.0.0.1', '::1', '[::1]'] 合并进 NO_PROXY——其中
# `[::1]` 是为了兼容 Node/undici 的匹配器（它读裸 `::1` 会解析成 host ':' port '1'），
# 但 httpx 自己已把裸 `::1` 正确处理为 IPv6 豁免（all://[::1]），多出来的
# `[::1]` 反而炸解析。这里在创建 Client 的瞬间剔除该条目，用毕恢复，
# 不污染进程其余部分的 env 视图。
_IPV6_BRACKETED_NO_PROXY = "[::1]"


def make_client(model_cfg: dict | None = None, *, timeout: httpx.Timeout | None = None) -> httpx.Client:
    """创建 httpx.Client，防御 NO_PROXY 中的 `[::1]` 条目导致 Client 构造崩溃。

    - model_cfg 提供时按 make_timeout(model_cfg) 建超时（adapter 常规路径）
    - 也可直接传 timeout（如 omniparser health_check 用短超时探活）
    - 仅临时修改环境变量（剔除 `[::1]`，保留裸 `::1`），Client 创建后立即恢复。
      vision-engine 是单线程顺序调用，无并发竞争；若未来引入并发请求，
      需改为在 fork 前一次性 sanitize env 或显式构建 proxy map。
    """
    timeout = timeout or make_timeout(model_cfg or {})
    saved: dict[str, str | None] = {}
    try:
        for name in ("NO_PROXY", "no_proxy"):
            val = os.environ.get(name)
            if val and _IPV6_BRACKETED_NO_PROXY in val.split(","):
                saved[name] = val
                cleaned = ",".join(
                    part for part in val.split(",")
                    if part.strip() != _IPV6_BRACKETED_NO_PROXY
                )
                if cleaned:
                    os.environ[name] = cleaned
                else:
                    os.environ.pop(name, None)
        return httpx.Client(timeout=timeout)
    finally:
        for name, val in saved.items():
            if val is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = val


_ENDPOINT_UNREACHABLE: dict[str, float] = {}
DEFAULT_ENDPOINT_UNREACHABLE_TTL = 300


def mark_endpoint_unreachable(base_url: str, ttl: float = DEFAULT_ENDPOINT_UNREACHABLE_TTL):
    _ENDPOINT_UNREACHABLE[base_url] = time.monotonic() + ttl


def is_endpoint_unreachable(base_url: str) -> bool:
    deadline = _ENDPOINT_UNREACHABLE.get(base_url)
    if deadline is None:
        return False
    if time.monotonic() < deadline:
        return True
    del _ENDPOINT_UNREACHABLE[base_url]
    return False


def clear_endpoint_cache():
    """清除全部 endpoint 不可达缓存（--clear-quotas 和 self-test 调用）。"""
    _ENDPOINT_UNREACHABLE.clear()
