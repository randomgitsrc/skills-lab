"""OpenAI /v1/chat/completions 格式适配（覆盖 GPT-4o、Qwen2.5-VL 等 openai兼容 provider）。"""
import httpx

from .common import encode_image, classify_http_error, AdapterHTTPError


def call(model_cfg: dict, api_key: str, system_prompt: str | None,
         user_prompt: str, image_paths: list[str]) -> str:
    base = model_cfg["base_url"].rstrip("/")
    content = [{"type": "text", "text": user_prompt}]
    for p in image_paths:
        b64, media_type = encode_image(p)
        content.insert(0, {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}})

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": content})

    body = {
        "model": model_cfg["name"],
        "max_tokens": model_cfg.get("max_tokens") or 4096,
        "messages": messages,
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        with httpx.Client(timeout=model_cfg["timeout"]) as client:
            r = client.post(f"{base}/chat/completions", headers=headers, json=body)
            r.raise_for_status()
            data = r.json()
    except AdapterHTTPError:
        raise
    except Exception as e:
        raise classify_http_error(e) from e

    choices = data.get("choices", [])
    if not choices:
        raise AdapterHTTPError("unknown", "model returned no choices")
    return choices[0]["message"]["content"]
