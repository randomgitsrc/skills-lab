"""Anthropic /v1/messages 格式适配（覆盖 Claude 官方与 MiniMax 等兼容此格式的 provider）。"""
import httpx

from .common import encode_image, classify_http_error, AdapterHTTPError
from .usage import parse_usage


def call(model_cfg: dict, api_key: str, system_prompt: str | None,
         user_prompt: str, image_paths: list[str]) -> dict:
    """返回 {"text": str, "usage": dict | None}。usage 来自 response.usage。"""
    b, _t = model_cfg["base_url"].rstrip("/"), model_cfg["timeout"]
    content = []
    for p in image_paths:
        b64, media_type = encode_image(p)
        content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}})
    content.append({"type": "text", "text": user_prompt})

    body = {
        "model": model_cfg["name"],
        "max_tokens": model_cfg.get("max_tokens") or 4096,
        "messages": [{"role": "user", "content": content}],
    }
    if system_prompt:
        body["system"] = system_prompt

    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}

    try:
        with httpx.Client(timeout=model_cfg["timeout"]) as client:
            r = client.post(f"{b}/v1/messages", headers=headers, json=body)
            r.raise_for_status()
            data = r.json()
    except AdapterHTTPError:
        raise
    except Exception as e:
        raise classify_http_error(e) from e

    blocks = data.get("content", [])
    text_parts = [blk.get("text", "") for blk in blocks if blk.get("type") == "text"]
    if not text_parts:
        raise AdapterHTTPError("unknown", "model returned no text content block")

    # 提取 rate limit 响应头
    rl_headers = {}
    for k, v in r.headers.items():
        kl = k.lower()
        if kl.startswith("anthropic-ratelimit-"):
            rl_headers[kl] = v

    return {"text": "\n".join(text_parts), "usage": parse_usage("anthropic", data),
            "rate_limit_headers": rl_headers}
