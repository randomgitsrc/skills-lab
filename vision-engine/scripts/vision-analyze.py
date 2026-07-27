#!/usr/bin/env python3
"""
vision-analyze.py — vision-engine CLI 主入口。

用法:
  vision-analyze.py -i img.png                              # comprehensive(默认)
  vision-analyze.py -i img.png -r quick -p "这张图里有啥"      # 一次性提问快速路径
  vision-analyze.py -i img.png -r ocr
  vision-analyze.py -i img.png -r locate -p "找到确认按钮"
  vision-analyze.py -i img.png -r locate-ui -p "登录按钮"
  vision-analyze.py -i a.png -i2 b.png -r compare
  vision-analyze.py -i img.png -c '{"task_goal":"验证按钮布局"}'
  vision-analyze.py -i img.png --model sonnet                # 强制指定model(可用alias或provider/name)
  vision-analyze.py --verify-grounding gemini-3-pro           # 实测某model的grounding准确度
  vision-analyze.py --self-test                               # 批量存活检查config里配了key的全部model
  vision-analyze.py --clear-quotas                            # 清除本地quota限流计数(无网络请求)

退出码:
  0 成功
  1 参数错误(含文件不存在、task_goal超硬上限)
  2 全部模型失败
  3 无可用模型(key缺失/白名单校验失败/omniparser健康检查失败)
  4 限流(候选模型均RPM/RPD超限或429冷却中)
  5 bbox_list结果校验失败(全部条目非法)
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import bbox_utils          # noqa: E402
import cache as cache_mod  # noqa: E402
import context as ctx_mod  # noqa: E402
import env_security        # noqa: E402
import logger as logger_mod  # noqa: E402
import ratelimit           # noqa: E402
from adapters import anthropic_api, openai_api, google_api, omniparser_api  # noqa: E402
from adapters.common import AdapterHTTPError, image_dimensions  # noqa: E402

DEFAULT_CONFIG_PATH = SCRIPT_DIR.parent / "config" / "vision-config.json"
SUPPORTED_SCHEMA_VERSIONS = {"4.0"}

# 限流类状态：所有这些状态都意味着"模型可用但本地跳过"，exit code 4（限流）
# 区别于真正的失败（auth_error/server_error/timeout等），后者是exit code 2
# quota_exceeded:requests:60s / quota_exceeded:tokens:60s 等都是该model被quota跳过
def _is_rate_limit_status(status: str) -> bool:
    return status == "cooldown" or status.startswith("quota_exceeded:")

# provider级别的共享字段：在providers[provider_id]下声明一次，model条目可以覆盖同名字段
PROVIDER_LEVEL_FIELDS = ("base_url", "api_format", "api_key_env")


def model_ref(model: dict) -> str:
    """身份识别用provider/name复合key，不是裸name——不同provider下可以有同名model，
    这是OpenClaw等成熟多provider路由器的通行做法，也是这轮修正的地方。"""
    return f"{model['provider']}/{model['name']}"


def resolve_ref_or_alias(models: list[dict], ref_or_alias: str) -> dict | None:
    """任何接受model引用的地方（preferred_models/--model/--verify-grounding）都通过这个函数解析，
    优先精确匹配provider/name完整ref，其次匹配alias（alias是全局扁平命名空间，加载时已校验唯一）。"""
    for m in models:
        if model_ref(m) == ref_or_alias:
            return m
    for m in models:
        if m.get("alias") == ref_or_alias:
            return m
    return None


def _sanitize_ref_for_filename(ref: str) -> str:
    return ref.replace("/", "__")


def _flatten_providers(raw_config: dict) -> list[dict]:
    """把按provider分组的nested结构拍平成内部使用的扁平models列表。
    这样select_candidates/run_text_role等下游逻辑完全不用感知config authoring格式的变化。"""
    flat = []
    for provider_id, provider_cfg in raw_config.get("providers", {}).items():
        shared = {k: provider_cfg[k] for k in PROVIDER_LEVEL_FIELDS if k in provider_cfg}
        for m in provider_cfg.get("models", []):
            merged = dict(shared)
            merged.update(m)  # model条目里的同名字段可以覆盖provider级别的共享值
            merged["provider"] = provider_id
            flat.append(merged)
    return flat


# ────────────────────────── 配置加载与校验 ──────────────────────────

def load_config(config_path: Path) -> dict:
    if not config_path.is_file():
        print(f"Error: config not found: {config_path}", file=sys.stderr)
        sys.exit(1)
    config = json.loads(config_path.read_text())

    version = config.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        print(
            f"Error: unsupported config schema_version '{version}', "
            f"expected one of {sorted(SUPPORTED_SCHEMA_VERSIONS)}. "
            f"新旧脚本/config不兼容，请检查版本。",
            file=sys.stderr,
        )
        sys.exit(1)

    config["models"] = _flatten_providers(config)

    # system_prompt_file：允许把长prompt外置到.md文件，避免inline字符串难编辑/难diff。
    # 路径相对于config.json自身所在目录。读取后直接填进role["system_prompt"]，
    # 下游所有引用role.get("system_prompt")的代码完全不用感知这层区别。
    for role_name, role in config.get("roles", {}).items():
        prompt_file = role.get("system_prompt_file")
        if prompt_file:
            if role.get("system_prompt"):
                print(f"Error: role '{role_name}' 同时配置了system_prompt和system_prompt_file，"
                      f"只能二选一", file=sys.stderr)
                sys.exit(1)
            full_path = config_path.parent / prompt_file
            if not full_path.is_file():
                print(f"Error: role '{role_name}' 的system_prompt_file指向的文件不存在: {full_path}",
                      file=sys.stderr)
                sys.exit(1)
            role["system_prompt"] = full_path.read_text(encoding="utf-8")

    # capability白名单静态校验：grounding/ui-grounding能力只允许出现在白名单provider下
    whitelist = config.get("capability_provider_whitelist", {})
    for model in config["models"]:
        for cap, allowed_providers in whitelist.items():
            if cap in model.get("capabilities", []) and model["provider"] not in allowed_providers:
                print(
                    f"Error: model '{model_ref(model)}' declares capability '{cap}' "
                    f"but provider '{model['provider']}' is not in whitelist {allowed_providers}",
                    file=sys.stderr,
                )
                sys.exit(3)

    # capabilities健全性检查（警告，不阻断）：
    # - 死标签：某model声明了某capability，但没有任何role的requires用到它——大概率是配置遗留或笔误
    # - 不可满足的role：某role要求了某capability，但没有任何model声明它——这个role实际上永远选不到候选
    declared_caps = set()
    for m in config["models"]:
        declared_caps.update(m.get("capabilities", []))
    required_caps = set()
    for role in config["roles"].values():
        required_caps.update(role.get("requires", []))

    dead_caps = declared_caps - required_caps
    if dead_caps:
        print(f"[警告] 以下capability被model声明了，但没有任何role要求它，不影响路由(死标签，"
              f"确认是否为笔误或遗留配置): {sorted(dead_caps)}", file=sys.stderr)

    unsatisfiable = required_caps - declared_caps
    if unsatisfiable:
        print(f"[警告] 以下capability被role要求，但没有任何model声明它，"
              f"对应role将永远无法选中候选model: {sorted(unsatisfiable)}", file=sys.stderr)

    # 唯一性校验：身份是provider/name复合key，不是裸name。同一provider下的models不能重名
    # （因为RPM计数文件名/--model匹配/preferred_models查找都靠provider/name这个ref），
    # 但跨provider允许同名——这是这轮的修正点，上一版误把name当成全局唯一标识了。
    seen_refs = set()
    for m in config["models"]:
        ref = model_ref(m)
        if ref in seen_refs:
            print(f"Error: model ref重复: '{ref}'。同一provider下的model name不能重复"
                  f"（不同provider下允许同名，因为身份识别用的是完整的provider/name）。",
                  file=sys.stderr)
            sys.exit(1)
        seen_refs.add(ref)

    # alias是全局扁平命名空间（跟provider/name不同，不按provider分区），必须唯一，
    # 且不能含'/'（避免跟provider/name格式混淆，解析时无法区分）。
    seen_aliases = {}
    for m in config["models"]:
        alias = m.get("alias")
        if not alias:
            continue
        if "/" in alias:
            print(f"Error: model '{model_ref(m)}' 的alias '{alias}' 不能包含'/'"
                  f"（会跟provider/name格式混淆）", file=sys.stderr)
            sys.exit(1)
        if alias in seen_aliases:
            print(f"Error: alias '{alias}' 被多个model使用（{seen_aliases[alias]} 和 {model_ref(m)}），"
                  f"alias必须全局唯一", file=sys.stderr)
            sys.exit(1)
        seen_aliases[alias] = model_ref(m)

    return config


def resolve_role(config: dict, role_name: str | None) -> tuple[str, dict]:
    roles = config["roles"]
    if role_name is None:
        role_name = config["defaults"]["role"]
    if role_name not in roles:
        print(f"Error: unknown role '{role_name}'. Available: {list(roles)}", file=sys.stderr)
        sys.exit(1)
    return role_name, roles[role_name]


# ────────────────────────── 模型调用分发 ──────────────────────────

ADAPTER_BY_FORMAT = {
    "anthropic": anthropic_api.call,
    "openai": openai_api.call,
    "google": google_api.call,
}


def call_model(model_cfg: dict, api_key: str | None, system_prompt: str | None,
                user_prompt: str, image_paths: list[str],
                role_max_tokens: int | None = None) -> dict:
    """返回 {"text": str, "usage": dict | None}。usage 由 adapter 从 provider 响应提取。
    role_max_tokens: role 级覆盖，优先于 model_cfg 的 max_tokens。"""
    # role 级 max_tokens 覆盖 model 级（role > model > 默认 4096）
    if role_max_tokens is not None:
        model_cfg = {**model_cfg, "max_tokens": role_max_tokens}
    fmt = model_cfg["api_format"]
    if fmt not in ADAPTER_BY_FORMAT:
        raise AdapterHTTPError("unknown", f"unsupported api_format: {fmt}")
    return ADAPTER_BY_FORMAT[fmt](model_cfg, api_key, system_prompt, user_prompt, image_paths)


# ────────────────────────── candidate 选取与排序 ──────────────────────────

def select_candidates(config: dict, role: dict) -> list[dict]:
    required = set(role.get("requires", []))
    candidates = [m for m in config["models"] if required.issubset(set(m.get("capabilities", [])))]

    if role.get("output_schema") == "text" and not candidates:
        candidates = [m for m in config["models"] if "general" in m.get("capabilities", [])]
        # bbox_list 不做通用兜底：非grounding模型的坐标是编的，混进fallback只会污染结果

    preferred = role.get("preferred_models")
    if preferred:
        by_ref = {model_ref(m): m for m in candidates}
        all_refs_and_aliases = {model_ref(m) for m in config["models"]} | \
                                {m["alias"] for m in config["models"] if m.get("alias")}
        ordered = []
        seen_entries = set()
        for entry in preferred:
            if entry in seen_entries:
                continue  # preferred_models里写重复了，跳过第二次，不当成新的警告case
            seen_entries.add(entry)
            m = resolve_ref_or_alias(candidates, entry)
            if m is not None and model_ref(m) in by_ref:
                ordered.append(by_ref.pop(model_ref(m)))
            elif m is not None:
                continue  # alias和完整ref指向同一个model、且已经被前面的条目选过了，跳过不重复警告
            elif entry not in all_refs_and_aliases:
                print(f"[警告] role的preferred_models里的'{entry}'在config中不存在"
                      f"（检查拼写/alias/是否已删除该model）", file=sys.stderr)
            else:
                print(f"[警告] role的preferred_models里的'{entry}'不满足当前role的capability要求，"
                      f"已被排除在候选池外", file=sys.stderr)
        # preferred_models里没提到的候选，按(deprecated, priority)排在后面，不丢失fallback安全网
        remaining = sorted(by_ref.values(), key=lambda m: (bool(m.get("deprecated")), m.get("priority", 999)))
        return ordered + remaining

    # deprecated=True的model无论priority多小，一律排到最后——依然可用于fallback，但不被优先选中
    candidates.sort(key=lambda m: (bool(m.get("deprecated")), m.get("priority", 999)))
    return candidates


# ────────────────────────── --model 解析辅助函数 ──────────────────────────

def resolve_forced_model(candidates: list[dict], forced_input: str) -> tuple[dict | None, str | None]:
    """--model参数解析：优先精确匹配provider/name完整ref，其次匹配alias，
    最后只有裸name且在candidates里唯一时才允许简写。
    返回(匹配到的model, 错误信息)，两者恰好一个非None。"""
    m = resolve_ref_or_alias(candidates, forced_input)
    if m is not None:
        return m, None
    bare_matches = [m for m in candidates if m["name"] == forced_input]
    if len(bare_matches) == 1:
        return bare_matches[0], None
    if len(bare_matches) > 1:
        refs = [model_ref(m) for m in bare_matches]
        return None, f"'{forced_input}' 在多个provider下存在同名model({refs})，请用完整的provider/name或alias指定"
    return None, None


# ────────────────────────── text 类 role 的 fallback 主循环 ──────────────────────────

def run_text_role(config: dict, role_name: str, role: dict, user_prompt: str,
                   image_paths: list[str], verbose: bool, forced_model: str | None = None) -> dict:
    candidates = select_candidates(config, role)

    if forced_model:
        match, err = resolve_forced_model(candidates, forced_model)
        if match is None:
            return {"status": "error", "reason": "forced_model_not_eligible",
                     "detail": err or f"'{forced_model}' 不在此role的candidates中"
                                f"(可能不满足capability要求，或config里不存在该model)", "attempts": []}
        candidates = [match]  # 只试这一个，不fallback——这是--model的设计初衷，用于隔离测试单个model

    candidates, unavailable = env_security.precheck_key_existence(candidates)
    if verbose and unavailable:
        print(f"[verbose] skipping models without key: {unavailable}", file=sys.stderr)

    if not candidates:
        return {"status": "error", "reason": "no_available_model", "attempts": []}

    attempts = []
    for model in candidates:
        ref = model_ref(model)
        sanitized = _sanitize_ref_for_filename(ref)

        # 1. cooldown 检查（之前收到过 429，冷却期内直接跳过）
        if ratelimit.is_cooled_down(sanitized):
            attempts.append({"model": ref, "status": "cooldown"})
            continue

        # 2. 通用 quota 检查（requests/tokens × 任意 window）
        #    model["quotas"] 列表为空 → 无 quota 限制，直接放行
        #    projected 不写文件（dry-run），由响应后的 record_usage 精确记账
        quota_blocked = _check_model_quotas(model, sanitized)
        if quota_blocked is not None:
            attempts.append({"model": ref, "status": quota_blocked})
            continue

        api_key = env_security.resolve_key(model.get("api_key_env"))
        t0 = time.time()
        try:
            result_obj = call_model(model, api_key, role.get("system_prompt"), user_prompt, image_paths,
                                    role_max_tokens=role.get("max_tokens"))
        except AdapterHTTPError as e:
            # 429/quota_exceeded 触发冷却
            if e.kind == "quota_exceeded":
                ratelimit.set_cooldown(sanitized, model.get("cooldown_seconds", 60))
            attempts.append({"model": ref, "status": e.kind, "error": str(e)})
            continue
        latency_ms = int((time.time() - t0) * 1000)
        text = result_obj["text"]
        usage = result_obj.get("usage")

        # 3. 响应成功：精确记录 token / request 消耗
        for quota in model.get("quotas", []):
            if quota["metric"] == "requests":
                ratelimit.record_usage(sanitized, quota, 1, _now=time.time())
            elif quota["metric"] == "tokens" and usage is not None:
                ratelimit.record_usage(sanitized, quota, usage["total_tokens"], _now=time.time())
            elif quota["metric"] == "tokens" and usage is None:
                # usage字段缺失（极少见），保守估算；宁可少记也别装满TPM窗口
                # 用min(max_tokens, 512)防止4096的默认值快速填满quota
                ratelimit.record_usage(sanitized, quota,
                                       min(model.get("max_tokens") or 4096, 512), _now=time.time())

        attempt_record = {"model": ref, "status": "success", "latency_ms": latency_ms}
        if usage:
            attempt_record["tokens_in"] = usage.get("input_tokens")
            attempt_record["tokens_out"] = usage.get("output_tokens")
        attempts.append(attempt_record)
        if verbose and model.get("deprecated"):
            note = model.get("deprecated_note", "")
            print(f"[verbose] 使用了标记为deprecated的model '{ref}'"
                  f"{('：' + note) if note else ''}，建议尽快迁移到替代model", file=sys.stderr)
        result = {
            "status": "success", "model_used": ref, "provider": model["provider"],
            "result": text, "attempts": attempts,
        }
        if usage:
            result["usage"] = usage
        return result

    return {"status": "error", "reason": "all_models_failed", "attempts": attempts}


def _check_model_quotas(model: dict, sanitized: str) -> str | None:
    """检查 model 的所有 quotas，返回第一个超限的 status 字符串，否则 None。"""
    for quota in model.get("quotas", []):
        if ratelimit.check_quota(sanitized, quota):
            metric = quota["metric"]
            window = quota["window_seconds"]
            return f"quota_exceeded:{metric}:{window}s"
    return None


# ────────────────────────── bbox_list 类 role (locate) 的 fallback 主循环 ──────────────────────────

def run_locate_role(config: dict, role_name: str, role: dict, user_prompt: str,
                     image_path: str, verbose: bool, forced_model: str | None = None) -> dict:
    candidates = select_candidates(config, role)

    if forced_model:
        match, err = resolve_forced_model(candidates, forced_model)
        if match is None:
            return {"status": "error", "reason": "forced_model_not_eligible",
                     "detail": err or f"'{forced_model}' 不在此role的candidates中"
                                f"(可能不满足capability要求，或config里不存在该model)", "attempts": []}
        candidates = [match]

    candidates, unavailable = env_security.precheck_key_existence(candidates)
    if verbose and unavailable:
        print(f"[verbose] skipping models without key: {unavailable}", file=sys.stderr)

    if not candidates:
        return {"status": "error", "reason": "no_available_model", "attempts": []}

    width, height = image_dimensions(image_path)
    attempts = []

    for model in candidates:
        ref = model_ref(model)
        sanitized = _sanitize_ref_for_filename(ref)

        # 1. cooldown 检查
        if ratelimit.is_cooled_down(sanitized):
            attempts.append({"model": ref, "status": "cooldown"})
            continue

        # 2. 通用 quota 检查
        quota_blocked = _check_model_quotas(model, sanitized)
        if quota_blocked is not None:
            attempts.append({"model": ref, "status": quota_blocked})
            continue

        api_key = env_security.resolve_key(model.get("api_key_env"))
        t0 = time.time()
        try:
            result_obj = call_model(model, api_key, role.get("system_prompt"), user_prompt, [image_path],
                                    role_max_tokens=role.get("max_tokens"))
        except AdapterHTTPError as e:
            if e.kind == "quota_exceeded":
                ratelimit.set_cooldown(sanitized, model.get("cooldown_seconds", 60))
            attempts.append({"model": ref, "status": e.kind, "error": str(e)})
            continue
        latency_ms = int((time.time() - t0) * 1000)
        raw_text = result_obj["text"]
        usage = result_obj.get("usage")

        # 3. 响应成功：精确记录 token / request 消耗
        for quota in model.get("quotas", []):
            if quota["metric"] == "requests":
                ratelimit.record_usage(sanitized, quota, 1, _now=time.time())
            elif quota["metric"] == "tokens" and usage is not None:
                ratelimit.record_usage(sanitized, quota, usage["total_tokens"], _now=time.time())
            elif quota["metric"] == "tokens" and usage is None:
                ratelimit.record_usage(sanitized, quota, model.get("max_tokens") or 4096, _now=time.time())

        convention = model.get("coordinate_convention", "gemini_1000")
        valid_entries, dropped = bbox_utils.extract_and_validate(raw_text, convention, width, height)

        if dropped == -1 or not valid_entries:
            attempts.append({"model": ref, "status": "invalid_schema", "latency_ms": latency_ms})
            continue

        attempts.append({"model": ref, "status": "success",
                          "latency_ms": latency_ms, "dropped_count": max(dropped, 0)})
        if verbose and model.get("deprecated"):
            note = model.get("deprecated_note", "")
            print(f"[verbose] 使用了标记为deprecated的model '{ref}'"
                  f"{('：' + note) if note else ''}，建议尽快迁移到替代model", file=sys.stderr)
        result = {
            "status": "success", "model_used": ref, "provider": model["provider"],
            "result": valid_entries, "dropped_count": max(dropped, 0), "attempts": attempts,
        }
        if usage:
            result["usage"] = usage
        return result

    return {"status": "error", "reason": "all_models_failed_or_invalid", "attempts": attempts}


# ────────────────────────── locate-ui: enumerate_then_filter ──────────────────────────

def _keyword_match(label: str, query: str) -> bool:
    label_l, query_l = label.lower(), query.lower()
    return query_l in label_l or label_l in query_l or any(
        tok in label_l for tok in query_l.split() if len(tok) > 1
    )


def _convert_omni_elements(raw_elements: list, convention: str, width: int, height: int) -> list[dict]:
    """OmniParser 返回的原始元素列表 → 统一格式。"""
    converted = []
    for el in raw_elements:
        box = bbox_utils.convert_box(el.get("box"), convention, width, height)
        if box is None:
            continue
        converted.append({"label": el["label"], "box": [round(v) for v in box],
                           "confidence": "high", "type": "element"})
    return converted


def _parse_llm_ui_elements(raw_text: str, convention: str, width: int, height: int) -> list[dict]:
    """从 LLM 返回的 JSON 数组中解析 UI 元素。支持两种格式：
    - 标准格式: {label, type, box: [x1,y1,x2,y2]}
    - 紧凑格式: {l, t, b: [x1,y1,x2,y2]}
    """
    parsed = bbox_utils.extract_json_array(raw_text)
    if parsed is None:
        return []
    converted = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        label = entry.get("label") or entry.get("l") or ""
        box = entry.get("box") or entry.get("b")
        etype = entry.get("type") or entry.get("t") or "element"
        if not label or box is None:
            continue
        normalized_box = bbox_utils.convert_box(box, convention, width, height)
        if normalized_box is None:
            continue
        # 像素坐标 → 归一化 0-1000（与 bbox_utils 统一基准一致）
        converted.append({
            "label": str(label).strip(),
            "box": [round(v) for v in normalized_box],
            "confidence": "medium",  # LLM 坐标标记为 medium（非专用检测器）
            "type": str(etype),
        })
    return converted


def run_locate_ui_role(config: dict, role: dict, query: str | None, image_path: str) -> dict:
    ui_models = [m for m in config["models"] if "ui-grounding" in m.get("capabilities", [])]
    if not ui_models:
        return {"status": "error", "reason": "no_ui_grounding_model_configured", "attempts": []}

    # Sort by priority; cooldown/quota checks filter below
    candidates = sorted(ui_models, key=lambda m: m.get("priority", 999))
    width, height = image_dimensions(image_path)
    attempts = []

    for model in candidates:
        ref = model_ref(model)
        sanitized = _sanitize_ref_for_filename(ref)

        # Cooldown + quota checks (same as text/locate roles)
        if ratelimit.is_cooled_down(sanitized):
            attempts.append({"model": ref, "status": "cooldown"})
            continue
        quota_blocked = _check_model_quotas(model, sanitized)
        if quota_blocked is not None:
            attempts.append({"model": ref, "status": quota_blocked})
            continue

        # OmniParser 路径
        if model.get("api_format") == "omniparser":
            if not omniparser_api.health_check(model["base_url"]):
                attempts.append({"model": ref, "status": "omniparser_unavailable"})
                continue
            t0 = time.time()
            try:
                raw_elements = omniparser_api.detect(model, image_path)
            except AdapterHTTPError as e:
                attempts.append({"model": ref, "status": e.kind, "error": str(e)})
                continue
            latency_ms = int((time.time() - t0) * 1000)
            convention = model.get("coordinate_convention", "omniparser_pixel")
            converted = _convert_omni_elements(raw_elements, convention, width, height)

        # LLM 路径
        else:
            api_key = env_security.resolve_key(model.get("api_key_env"))
            if api_key is None:
                attempts.append({"model": ref, "status": "skipped_no_key"})
                continue
            user_prompt = f"Find elements: {query}" if query else "Enumerate all interactive elements"
            # locate-ui 枚举输出量大，LLM 响应慢；至少 120s
            original_timeout = model.get("timeout")
            if original_timeout and original_timeout < 120:
                model["timeout"] = 120
            t0 = time.time()
            try:
                result_obj = call_model(model, api_key, role.get("system_prompt"), user_prompt, [image_path])
            except AdapterHTTPError as e:
                if e.kind == "quota_exceeded":
                    ratelimit.set_cooldown(sanitized, model.get("cooldown_seconds", 60))
                attempts.append({"model": ref, "status": e.kind, "error": str(e)})
                continue
            finally:
                if original_timeout:
                    model["timeout"] = original_timeout
            latency_ms = int((time.time() - t0) * 1000)
            convention = model.get("coordinate_convention", "gemini_1000")
            converted = _parse_llm_ui_elements(result_obj["text"], convention, width, height)
            # Record usage for successful calls
            usage = result_obj.get("usage")
            for quota in model.get("quotas", []):
                if quota["metric"] == "requests":
                    ratelimit.record_usage(sanitized, quota, 1, _now=time.time())
                elif quota["metric"] == "tokens" and usage is not None:
                    ratelimit.record_usage(sanitized, quota, usage["total_tokens"], _now=time.time())
                elif quota["metric"] == "tokens" and usage is None:
                    ratelimit.record_usage(sanitized, quota, min(model.get("max_tokens") or 4096, 512), _now=time.time())

        attempts.append({"model": ref, "status": "success", "latency_ms": latency_ms})
        break  # Success — don't try remaining candidates

    else:
        # All candidates exhausted without success
        return {"status": "error", "reason": "all_models_failed", "attempts": attempts}

    total = len(converted)
    note = None
    if not query:
        result = converted
        matched = None
    else:
        matched = [e for e in converted if _keyword_match(e["label"], query)]
        if matched:
            result = matched
        else:
            result = converted  # 匹配不到时返回全部，附加提示，而不是静默返回空数组
            note = (f"未找到与查询词'{query}'精确匹配的元素，以下是画面检测到的全部{total}个元素，"
                    f"请勿将此误判为'该元素不存在'")

    response = {
        "status": "success", "model_used": model_ref(model), "provider": model["provider"],
        "query_mode": "enumerate_then_filter", "matched_query": query,
        "filter_matched": bool(matched) if query else None,
        "total_elements_detected": total, "result": result,
        "attempts": [{"model": model_ref(model), "status": "success", "latency_ms": latency_ms}],
    }
    if note:
        response["note"] = note
    return response


# ────────────────────────── --self-test: 批量存活检查 ──────────────────────────

FIXTURE_DIR = SCRIPT_DIR / "fixtures"
SELF_TEST_PROBE_IMAGE = FIXTURE_DIR / "self-test-probe.png"
SELF_TEST_PROMPT = "回复OK即可，这是一次存活探测，不需要分析任何内容。"


def run_self_test(config: dict) -> dict:
    if not SELF_TEST_PROBE_IMAGE.is_file():
        return {"status": "error", "reason": "fixture_missing",
                "detail": "探测图缺失，检查scripts/fixtures/self-test-probe.png"}

    results = []
    for model in config["models"]:
        entry = {"model": model_ref(model), "provider": model["provider"]}

        # ui-grounding类(如omniparser)不是对话模型，走健康检查而非chat探测
        if model.get("api_format") == "omniparser":
            alive = omniparser_api.health_check(model["base_url"])
            entry["status"] = "alive" if alive else "dead"
            entry["method"] = "health_check"
            results.append(entry)
            continue

        api_key_env = model.get("api_key_env")
        if api_key_env and env_security.resolve_key(api_key_env) is None:
            entry["status"] = "skipped_no_key"
            results.append(entry)
            continue

        api_key = env_security.resolve_key(api_key_env)
        t0 = time.time()
        try:
            call_model(model, api_key, None, SELF_TEST_PROMPT, [str(SELF_TEST_PROBE_IMAGE)])
            entry["status"] = "alive"
            entry["latency_ms"] = int((time.time() - t0) * 1000)
        except AdapterHTTPError as e:
            entry["status"] = "dead"
            entry["error_kind"] = e.kind
            entry["detail"] = str(e)
        entry["method"] = "chat_probe"
        if model.get("deprecated"):
            entry["deprecated"] = True
        results.append(entry)

    alive_count = sum(1 for r in results if r["status"] == "alive")
    dead_count = sum(1 for r in results if r["status"] == "dead")
    tested_count = alive_count + dead_count

    return {
        "status": "completed",
        "summary": {"total": len(results), "tested": tested_count,
                    "alive": alive_count, "dead": dead_count,
                    "skipped_no_key": len(results) - tested_count},
        "results": results,
    }


# ────────────────────────── --verify-grounding: 实测grounding准确度 ──────────────────────────

FIXTURE_IMAGE = FIXTURE_DIR / "grounding-probe.png"
FIXTURE_TRUTH = FIXTURE_DIR / "grounding-probe-truth.json"
IOU_PASS_THRESHOLD = 0.5  # 经验阈值：达到这个IoU认为"具备基本可用的grounding能力"


def run_verify_grounding(config: dict, model_ref_input: str) -> dict:
    model = resolve_ref_or_alias(config["models"], model_ref_input)
    if model is None:
        bare_matches = [m for m in config["models"] if m["name"] == model_ref_input]
        if len(bare_matches) == 1:
            model = bare_matches[0]
        elif len(bare_matches) > 1:
            return {"status": "error", "reason": "ambiguous_model",
                    "detail": f"'{model_ref_input}' 在多个provider下存在同名model"
                              f"({[model_ref(m) for m in bare_matches]})，请用完整的provider/name或alias指定"}
        else:
            return {"status": "error", "reason": "model_not_found",
                    "detail": f"config里没有名为'{model_ref_input}'的model（provider/name、alias或裸name均未匹配）"}

    if not FIXTURE_IMAGE.is_file() or not FIXTURE_TRUTH.is_file():
        return {"status": "error", "reason": "fixture_missing",
                "detail": "探测图/ground truth文件缺失，检查scripts/fixtures/目录"}

    truth = json.loads(FIXTURE_TRUTH.read_text())
    gt_box = truth["ground_truth_box"]

    ref = model_ref(model)
    api_key = env_security.resolve_key(model.get("api_key_env"))
    if model.get("api_key_env") and api_key is None:
        return {"status": "error", "reason": "no_key",
                "detail": f"'{ref}'需要{model['api_key_env']}但未配置"}

    locate_prompt = config["roles"]["locate"]["system_prompt"]
    width, height = image_dimensions(str(FIXTURE_IMAGE))

    t0 = time.time()
    try:
        result_obj = call_model(model, api_key, locate_prompt, f"找出{truth['target_label']}", [str(FIXTURE_IMAGE)])
        raw_text = result_obj["text"]
    except AdapterHTTPError as e:
        return {"status": "error", "reason": e.kind, "detail": str(e)}
    latency_ms = int((time.time() - t0) * 1000)

    convention = model.get("coordinate_convention", "gemini_1000")
    valid_entries, dropped = bbox_utils.extract_and_validate(raw_text, convention, width, height)

    if not valid_entries:
        return {
            "status": "completed", "model": ref, "latency_ms": latency_ms,
            "best_iou": 0.0, "raw_response_sample": raw_text[:300],
            "recommendation": (
                f"未能从响应中提取出合法bbox（dropped={dropped}），不建议给'{ref}'打grounding标签，"
                f"或该model的坐标格式约定(coordinate_convention)配置有误，先用-v核对raw_response"
            ),
        }

    best_iou = max(bbox_utils.iou(gt_box, e["box"]) for e in valid_entries)
    verdict = "达标" if best_iou >= IOU_PASS_THRESHOLD else "不达标"
    recommendation = (
        f"IoU={best_iou:.2f}（阈值{IOU_PASS_THRESHOLD}），{verdict}。"
        + (f"有实测证据支持给'{ref}'打grounding标签。"
           if best_iou >= IOU_PASS_THRESHOLD else
           f"不建议给'{ref}'打grounding标签——即使返回了格式合法的bbox，位置也不够准。"
           f"注意：单张探测图只能说明'至少不是完全瞎编'，不能证明在真实复杂场景下同样准确，"
           f"建议正式启用前再用你的真实场景图片人工抽查几次。")
    )

    return {
        "status": "completed", "model": ref, "latency_ms": latency_ms,
        "best_iou": round(best_iou, 3), "returned_boxes": valid_entries,
        "ground_truth_box": gt_box, "dropped_count": dropped,
        "recommendation": recommendation,
    }


# ────────────────────────── main ──────────────────────────

def main():
    parser = argparse.ArgumentParser(description="vision-engine CLI")
    parser.add_argument("-i", "--image", required=False, help="图片路径 (png/jpg/gif/webp)")
    parser.add_argument("-i2", "--image2", help="第二张图片路径（compare role时必需）")
    parser.add_argument("-r", "--role", default=None,
                         help="quick/comprehensive(默认)/ocr/code/compare/locate/locate-ui")
    parser.add_argument("-p", "--prompt", default=None, help="提示词/查询描述")
    parser.add_argument("-c", "--context", default=None,
                         help="dispatch-context: 内联JSON字符串或文件路径。quick role忽略此参数")
    parser.add_argument("-f", "--format", default="json", choices=["json", "text", "yaml"])
    parser.add_argument("--model", default=None,
                         help="强制指定单个model（provider/name完整ref、alias，或在candidates中唯一时"
                              "可用裸name），跳过priority排序与fallback，专用于测试/验证某个model，"
                              "不建议在生产调用中使用（失去容灾能力）")
    parser.add_argument("--verify-grounding", metavar="MODEL_NAME", default=None,
                         help="用内置探测图实测某个model的grounding准确度(IoU)，给'该不该打grounding标签'"
                              "提供实测证据而非仅凭文档宣称。指定后忽略-i/-r等其他分析参数")
    parser.add_argument("--self-test", action="store_true",
                         help="遍历config里配了key的全部model，各发一次最小化探测请求，"
                              "汇总存活/失效报告，用于定期核实model是否被下线，忽略-i/-r等其他分析参数")
    parser.add_argument("--clear-quotas", action="store_true",
                         help="清除本地quota限流数据（~/.local/share/vision-engine/ratelimit/），"
                              "用于测试/调试验证时重置RPM/RPD/TPM计数器。不发送任何网络请求")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    config = load_config(Path(args.config))

    if args.self_test:
        result = run_self_test(config)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result["status"] == "completed" and result["summary"]["dead"] == 0 else 1)

    if args.verify_grounding:
        result = run_verify_grounding(config, args.verify_grounding)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result["status"] == "completed" else 1)

    if args.clear_quotas:
        import shutil
        base = ratelimit._BASE_DIR
        if os.path.isdir(base):
            shutil.rmtree(base)
            print(f"已清除: {base}")
        else:
            print(f"目录不存在，无需清除: {base}")
        sys.exit(0)

    if not args.image:
        print("Error: -i/--image is required (unless using --verify-grounding)", file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(args.image):
        print(f"Error: file not found: {args.image}", file=sys.stderr)
        sys.exit(1)

    role_name, role = resolve_role(config, args.role)

    # §3.1 安全检查（不阻断，仅警告）
    for w in env_security.check_env_file_permissions() + env_security.check_gitignore():
        print(w, file=sys.stderr)

    # dispatch-context（quick role忽略）
    dispatch_ctx = None
    skip_cache = args.no_cache
    if args.context and not role.get("skip_context"):
        try:
            raw_ctx = ctx_mod.load_context(args.context)
            dispatch_ctx = ctx_mod.validate_context(
                raw_ctx,
                config.get("context", {}).get("task_goal_max_chars", 120),
                config.get("context", {}).get("prior_context_max_items", 2),
            )
        except ctx_mod.ContextError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        skip_cache = True  # 提供-c时默认跳过缓存

    if args.prompt:
        skip_cache = True  # 自定义-p时自动跳过缓存
    if args.model:
        skip_cache = True  # 指定--model时跳过缓存，否则可能拿到其他候选跑出的旧结果

    # 组装最终user_prompt
    if role_name == "quick":
        user_prompt = args.prompt or "请描述这张图片。"
    elif role.get("query_mode") == "enumerate_then_filter":
        user_prompt = None  # locate-ui不把-p传给模型，见run_locate_ui_role
    else:
        base_prompt = role.get("system_prompt") or ""
        extra = f"\n\n用户补充要求：{args.prompt}" if args.prompt else ""
        context_block = ctx_mod.render_context_block(dispatch_ctx) if dispatch_ctx else ""
        user_prompt = (context_block + base_prompt + extra).strip() or (args.prompt or "请分析这张图片。")

    # 缓存查询
    cache_cfg = config.get("cache", {})
    result = None
    if cache_cfg.get("enabled") and not skip_cache and role_name != "locate-ui":
        key = cache_mod.cache_key(args.image, role_name)
        cached = cache_mod.get(cache_cfg["dir"], key, cache_cfg.get("ttl_hours", 24))
        if cached is not None:
            result = cached
            result["_cache_hit"] = True

    if result is None:
        if role_name == "compare":
            if not args.image2:
                print("Error: -i2/--image2 required for compare role", file=sys.stderr)
                sys.exit(1)
            result = run_text_role(config, role_name, role, user_prompt, [args.image, args.image2],
                                    args.verbose, forced_model=args.model)
        elif role.get("output_schema") == "bbox_list" and role.get("query_mode") == "enumerate_then_filter":
            if args.model:
                print("Error: --model 不适用于 locate-ui（该role的候选池本来就只有单个"
                      "ui-grounding model，无fallback可谈）", file=sys.stderr)
                sys.exit(1)
            result = run_locate_ui_role(config, role, args.prompt, args.image)
        elif role.get("output_schema") == "bbox_list":
            result = run_locate_role(config, role_name, role, user_prompt, args.image,
                                      args.verbose, forced_model=args.model)
        else:
            result = run_text_role(config, role_name, role, user_prompt, [args.image],
                                    args.verbose, forced_model=args.model)

        if cache_cfg.get("enabled") and not skip_cache and result.get("status") == "success" \
                and role_name != "locate-ui":
            key = cache_mod.cache_key(args.image, role_name)
            cache_mod.set(cache_cfg["dir"], key, result)

    # 日志
    log_cfg = config.get("logging", {})
    if log_cfg.get("enabled"):
        attempts = result.get("attempts", [])
        last = attempts[-1] if attempts else {}
        usage = result.get("usage")
        logger_mod.log_event(
            log_cfg["file"], log_cfg.get("rotate_mb", 50), log_cfg.get("rotate_keep", 3),
            role=role_name, model=result.get("model_used"), provider=result.get("provider"),
            status=result.get("status"), latency_ms=last.get("latency_ms"),
            image=args.image, dropped_count=result.get("dropped_count"),
            error_kind=result.get("reason"),
            attempts=attempts,  # 完整fallback链：每个候选的model+status+error，便于诊断限流/429
            tokens_in=(usage or {}).get("input_tokens"),
            tokens_out=(usage or {}).get("output_tokens"),
            usage_source="api" if usage else None,
        )

    result["role"] = role_name
    result["output_schema"] = role.get("output_schema")
    result["image"] = args.image

    # 输出与退出码
    status = result.get("status")
    if status != "success":
        reason = result.get("reason", "")
        if reason == "forced_model_not_eligible":
            exit_code = 1  # --model指定了不合法的model，属参数错误
        elif reason == "no_available_model" or "unavailable" in reason:
            exit_code = 3
        elif any(_is_rate_limit_status(a.get("status", "")) for a in result.get("attempts", [])) and \
                all(_is_rate_limit_status(a.get("status", "")) for a in result.get("attempts", [])):
            exit_code = 4
        elif role.get("output_schema") == "bbox_list":
            exit_code = 5
        else:
            exit_code = 2
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stdout)
        sys.exit(exit_code)

    if args.format == "text":
        print(result.get("result", "") if isinstance(result.get("result"), str)
              else json.dumps(result.get("result"), ensure_ascii=False))
    elif args.format == "yaml":
        print("result:")
        payload = result.get("result", "")
        lines = payload.splitlines() if isinstance(payload, str) else [json.dumps(payload, ensure_ascii=False)]
        print("  text: |")
        for line in lines:
            print(f"    {line}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    sys.exit(0)


if __name__ == "__main__":
    main()
