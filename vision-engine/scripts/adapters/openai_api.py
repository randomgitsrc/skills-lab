"""OpenAI /v1/chat/completions 格式适配（覆盖 GPT-4o、Qwen2.5-VL 等 openai兼容 provider）。

支持两种请求模式（按 model_cfg["stream"] 选择，模型级开关）：
- 非流式（默认）：一次 POST 等完整响应
- 流式（SSE，model_cfg["stream"]=true 时启用）：逐 chunk 拼装后返回完整文本。
  背景：部分自营链路在"等响应头"阶段有硬超时（Cloudflare 边缘 100s），非流式长任务
  会被掐线（524）；流式首字节秒级到达，此后只要 chunk 间隔不超限，连接可存活任意
  时长（实测 101s+ 完成）。仅配置了 stream 的模型走此路径，其余模型行为不变。
"""
import json

import httpx

from .common import encode_image, classify_http_error, AdapterHTTPError, make_timeout
from .usage import parse_usage


def call(model_cfg: dict, api_key: str, system_prompt: str | None,
         user_prompt: str, image_paths: list[str]) -> dict:
    """返回 {"text": str, "usage": dict | None}。usage 来自响应（流式取末 chunk）。"""
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

    if model_cfg.get("stream"):
        return _call_stream(model_cfg, base, headers, body)
    return _call_once(model_cfg, base, headers, body)


def _call_once(model_cfg: dict, base: str, headers: dict, body: dict) -> dict:
    """非流式：一次 POST 等完整响应。"""
    try:
        with httpx.Client(timeout=make_timeout(model_cfg)) as client:
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

    # 提取 rate limit 响应头
    rl_headers = {}
    for k, v in r.headers.items():
        kl = k.lower()
        if "ratelimit" in kl or "rate-limit" in kl:
            rl_headers[kl] = v

    return {"text": choices[0]["message"]["content"], "usage": parse_usage("openai", data),
            "rate_limit_headers": rl_headers}


def _consume_sse_line(line: str, parts: list[str]) -> tuple[bool, dict | None]:
    """消费一行 SSE 数据。返回 (stop, usage_chunk)。

    - stop=True 表示收到 [DONE]，调用方应结束循环
    - 返回的 usage_chunk 是带 usage 的 chunk（通常为末帧），无则 None
    - 不可解析的行静默忽略，不中断流
    """
    if not line or not line.startswith("data:"):
        return False, None
    payload = line[5:].strip()
    if payload == "[DONE]":
        return True, None
    try:
        chunk = json.loads(payload)
    except json.JSONDecodeError:
        return False, None
    choices = chunk.get("choices") or []
    if choices and choices[0].get("delta", {}).get("content"):
        parts.append(choices[0]["delta"]["content"])
    if chunk.get("usage"):
        return False, chunk
    return False, None


def _call_stream(model_cfg: dict, base: str, headers: dict, body: dict) -> dict:
    """流式（SSE）：逐 chunk 拼装 content，返回与 _call_once 相同结构。

    - 请求体追加 stream:true + stream_options.include_usage:true（OpenAI 兼容标准，
      让最后一帧携带 usage，token 计量不丢失）
    - 状态码错误（401/429/5xx，含 524）在流开始前即抛，走同一 classify 归类
    - 流结束后若一段 content 都没拼出来，抛 server_error（触发上层 fallback），
      不静默返回空文本——gemma 系 thinking=True 时小 max_tokens 会被思考全吃掉，
      返回 200 但 content 为空，这是另一个已实测的坑
    """
    stream_body = {**body, "stream": True, "stream_options": {"include_usage": True}}
    parts: list[str] = []
    usage_chunk: dict | None = None
    rl_headers: dict = {}
    status_code: int | None = None

    try:
        with httpx.Client(timeout=make_timeout(model_cfg)) as client:
            with client.stream("POST", f"{base}/chat/completions", headers=headers, json=stream_body) as r:
                # 状态码错误在进入 body 前就能确定，直接抛
                status_code = r.status_code
                if r.status_code >= 400:
                    r.read()
                    r.raise_for_status()

                for k, v in r.headers.items():
                    kl = k.lower()
                    if "ratelimit" in kl or "rate-limit" in kl:
                        rl_headers[kl] = v

                for line in r.iter_lines():
                    stop, u = _consume_sse_line(line, parts)
                    if u:
                        usage_chunk = u
                    if stop:
                        break
    except AdapterHTTPError:
        raise
    except Exception as e:
        raise classify_http_error(e) from e

    text = "".join(parts)
    if not text:
        raise AdapterHTTPError("server_error", "streamed response produced no content (empty)", status_code)

    usage = parse_usage("openai", usage_chunk) if usage_chunk else None
    return {"text": text, "usage": usage, "rate_limit_headers": rl_headers}
