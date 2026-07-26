"""公共工具：图片编码、mimetype 推断、HTTP 请求封装。"""
import base64
import mimetypes
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
    """封装HTTP层错误，调用方据此归类 quota_exceeded/timeout/auth_error/server_error，
    并且这里不回显 headers 或 key（§3.1 报错脱敏要求）。"""

    def __init__(self, kind: str, message: str, status_code: int | None = None):
        self.kind = kind  # quota_exceeded | timeout | auth_error | server_error | image_too_large | unknown
        self.status_code = status_code
        super().__init__(message)


def classify_http_error(exc: Exception) -> "AdapterHTTPError":
    if isinstance(exc, httpx.TimeoutException):
        return AdapterHTTPError("timeout", "request timed out")
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
