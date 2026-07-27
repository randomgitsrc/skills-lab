"""Google Gemini generateContent 格式适配。
Gemini 原生检测能力习惯输出 [ymin,xmin,ymax,xmax]，0-1000归一化 —— 这正是本项目
统一 bbox 格式的基准（coordinate_convention: gemini_1000 时直接透传，不做换算）。
"""
import httpx

from .common import encode_image, classify_http_error, AdapterHTTPError
from .usage import parse_usage


def call(model_cfg: dict, api_key: str, system_prompt: str | None,
         user_prompt: str, image_paths: list[str]) -> dict:
    """返回 {"text": str, "usage": dict | None}。usage 来自 response.usageMetadata。"""
    base = model_cfg["base_url"].rstrip("/")
    model_name = model_cfg["name"]

    parts = []
    for p in image_paths:
        b64, media_type = encode_image(p)
        parts.append({"inline_data": {"mime_type": media_type, "data": b64}})
    parts.append({"text": user_prompt})

    body = {"contents": [{"role": "user", "parts": parts}]}
    if system_prompt:
        body["system_instruction"] = {"parts": [{"text": system_prompt}]}
    body["generationConfig"] = {"maxOutputTokens": model_cfg.get("max_tokens") or 4096}

    url = f"{base}/models/{model_name}:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}

    try:
        with httpx.Client(timeout=model_cfg["timeout"]) as client:
            r = client.post(url, headers=headers, json=body)
            r.raise_for_status()
            data = r.json()
    except AdapterHTTPError:
        raise
    except Exception as e:
        raise classify_http_error(e) from e

    candidates = data.get("candidates", [])
    if not candidates:
        raise AdapterHTTPError("unknown", "model returned no candidates (possibly blocked by safety filter)")
    parts_out = candidates[0].get("content", {}).get("parts", [])
    text_parts = [p.get("text", "") for p in parts_out if "text" in p]
    if not text_parts:
        raise AdapterHTTPError("unknown", "model returned no text part")
    return {"text": "\n".join(text_parts), "usage": parse_usage("google", data)}
