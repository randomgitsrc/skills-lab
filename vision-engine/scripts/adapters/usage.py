"""从 provider 响应中提取 token usage。

每个 provider 的 usage 字段格式不同，统一归一化为：
  {"input_tokens": int, "output_tokens": int, "total_tokens": int}

返回 None 表示无法提取（响应缺 usage 字段、字段不完整、未知 provider 格式），
调用方应据情况估算或跳过 token 计量。
"""


def parse_usage(provider_format: str, response: dict) -> dict | None:
    """根据 provider_format 从响应中提取 token usage。返回 None 表示无法提取。"""
    if provider_format == "google":
        return _parse_google(response)
    if provider_format == "openai":
        return _parse_openai(response)
    if provider_format == "anthropic":
        return _parse_anthropic(response)
    if provider_format == "omniparser":
        return None  # 无 token 概念
    return None  # 未知格式


def _parse_google(response: dict) -> dict | None:
    """Google Gemini: usageMetadata.{promptTokenCount, candidatesTokenCount, totalTokenCount}"""
    meta = response.get("usageMetadata")
    if not isinstance(meta, dict):
        return None
    prompt = meta.get("promptTokenCount")
    candidates = meta.get("candidatesTokenCount")
    total = meta.get("totalTokenCount")
    if not all(isinstance(x, int) for x in (prompt, candidates, total)):
        return None
    return {
        "input_tokens": prompt,
        "output_tokens": candidates,
        "total_tokens": total,
    }


def _parse_openai(response: dict) -> dict | None:
    """OpenAI compatible: usage.{prompt_tokens, completion_tokens, total_tokens}"""
    u = response.get("usage")
    if not isinstance(u, dict):
        return None
    prompt = u.get("prompt_tokens")
    completion = u.get("completion_tokens")
    total = u.get("total_tokens")
    if not all(isinstance(x, int) for x in (prompt, completion, total)):
        return None
    return {
        "input_tokens": prompt,
        "output_tokens": completion,
        "total_tokens": total,
    }


def _parse_anthropic(response: dict) -> dict | None:
    """Anthropic: usage.{input_tokens, output_tokens} (无 total_tokens，自行求和)"""
    u = response.get("usage")
    if not isinstance(u, dict):
        return None
    inp = u.get("input_tokens")
    out = u.get("output_tokens")
    if not all(isinstance(x, int) for x in (inp, out)):
        return None
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": inp + out,
    }
