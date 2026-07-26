#!/usr/bin/env python3
"""
vision-analyze.py — vision-engine CLI 主入口。

用法:
  vision-analyze.py -i img.png                              # comprehensive(默认)
  vision-analyze.py -i img.png -r quick -p "这张图里有啥"      # 一次性提问快速路径
  vision-analyze.py -i img.png -r ocr
  vision-analyze.py -i img.png -r locate -p "找到确认按钮"
  vision-analyze.py -i img.png -r locate-ui -p "登录按钮"
  vision-analyze.py -i1 a.png -i2 b.png -r compare
  vision-analyze.py -i img.png -c '{"task_goal":"验证按钮布局"}'

退出码:
  0 成功
  1 参数错误(含文件不存在、task_goal超硬上限)
  2 全部模型失败
  3 无可用模型(key缺失/白名单校验失败/omniparser健康检查失败)
  4 RPM限流(候选模型均超限)
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
SUPPORTED_SCHEMA_VERSIONS = {"2.0"}


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

    # capability白名单静态校验：grounding/ui-grounding能力只允许出现在白名单provider下
    whitelist = config.get("capability_provider_whitelist", {})
    for model in config["models"]:
        for cap, allowed_providers in whitelist.items():
            if cap in model.get("capabilities", []) and model["provider"] not in allowed_providers:
                print(
                    f"Error: model '{model['name']}' declares capability '{cap}' "
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
                user_prompt: str, image_paths: list[str]) -> str:
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
        by_name = {m["name"]: m for m in candidates}
        ordered = [by_name.pop(name) for name in preferred if name in by_name]
        # preferred_models里没提到的候选，按原priority排在后面，不丢失fallback安全网
        remaining = sorted(by_name.values(), key=lambda m: m.get("priority", 999))
        return ordered + remaining

    candidates.sort(key=lambda m: m.get("priority", 999))
    return candidates


# ────────────────────────── text 类 role 的 fallback 主循环 ──────────────────────────

def run_text_role(config: dict, role_name: str, role: dict, user_prompt: str,
                   image_paths: list[str], verbose: bool, forced_model: str | None = None) -> dict:
    candidates = select_candidates(config, role)

    if forced_model:
        match = next((m for m in candidates if m["name"] == forced_model), None)
        if match is None:
            return {"status": "error", "reason": "forced_model_not_eligible",
                     "detail": f"'{forced_model}' 不在此role的candidates中"
                                f"(可能不满足capability要求，或config里不存在该model名)", "attempts": []}
        candidates = [match]  # 只试这一个，不fallback——这是--model的设计初衷，用于隔离测试单个model

    candidates, unavailable = env_security.precheck_key_existence(candidates)
    if verbose and unavailable:
        print(f"[verbose] skipping models without key: {unavailable}", file=sys.stderr)

    if not candidates:
        return {"status": "error", "reason": "no_available_model", "attempts": []}

    attempts = []
    for model in candidates:
        if ratelimit.is_rpm_exceeded(model["name"], model.get("rpm_limit", 60)):
            attempts.append({"model": model["name"], "status": "rpm_limited"})
            continue

        api_key = env_security.resolve_key(model.get("api_key_env"))
        t0 = time.time()
        try:
            text = call_model(model, api_key, role.get("system_prompt"), user_prompt, image_paths)
        except AdapterHTTPError as e:
            attempts.append({"model": model["name"], "status": e.kind, "error": str(e)})
            continue
        latency_ms = int((time.time() - t0) * 1000)
        attempts.append({"model": model["name"], "status": "success", "latency_ms": latency_ms})
        return {"status": "success", "model_used": model["name"], "provider": model["provider"],
                "result": text, "attempts": attempts}

    return {"status": "error", "reason": "all_models_failed", "attempts": attempts}


# ────────────────────────── bbox_list 类 role (locate) 的 fallback 主循环 ──────────────────────────

def run_locate_role(config: dict, role_name: str, role: dict, user_prompt: str,
                     image_path: str, verbose: bool, forced_model: str | None = None) -> dict:
    candidates = select_candidates(config, role)

    if forced_model:
        match = next((m for m in candidates if m["name"] == forced_model), None)
        if match is None:
            return {"status": "error", "reason": "forced_model_not_eligible",
                     "detail": f"'{forced_model}' 不在此role的candidates中"
                                f"(可能不满足capability要求，或config里不存在该model名)", "attempts": []}
        candidates = [match]

    candidates, unavailable = env_security.precheck_key_existence(candidates)
    if verbose and unavailable:
        print(f"[verbose] skipping models without key: {unavailable}", file=sys.stderr)

    if not candidates:
        return {"status": "error", "reason": "no_available_model", "attempts": []}

    width, height = image_dimensions(image_path)
    attempts = []

    for model in candidates:
        if ratelimit.is_rpm_exceeded(model["name"], model.get("rpm_limit", 60)):
            attempts.append({"model": model["name"], "status": "rpm_limited"})
            continue

        api_key = env_security.resolve_key(model.get("api_key_env"))
        t0 = time.time()
        try:
            raw_text = call_model(model, api_key, role.get("system_prompt"), user_prompt, [image_path])
        except AdapterHTTPError as e:
            attempts.append({"model": model["name"], "status": e.kind, "error": str(e)})
            continue
        latency_ms = int((time.time() - t0) * 1000)

        convention = model.get("coordinate_convention", "gemini_1000")
        valid_entries, dropped = bbox_utils.extract_and_validate(raw_text, convention, width, height)

        if dropped == -1 or not valid_entries:
            attempts.append({"model": model["name"], "status": "invalid_schema", "latency_ms": latency_ms})
            continue

        attempts.append({"model": model["name"], "status": "success",
                          "latency_ms": latency_ms, "dropped_count": max(dropped, 0)})
        return {"status": "success", "model_used": model["name"], "provider": model["provider"],
                "result": valid_entries, "dropped_count": max(dropped, 0), "attempts": attempts}

    return {"status": "error", "reason": "all_models_failed_or_invalid", "attempts": attempts}


# ────────────────────────── locate-ui: enumerate_then_filter ──────────────────────────

def _keyword_match(label: str, query: str) -> bool:
    label_l, query_l = label.lower(), query.lower()
    return query_l in label_l or label_l in query_l or any(
        tok in label_l for tok in query_l.split() if len(tok) > 1
    )


def run_locate_ui_role(config: dict, role: dict, query: str | None, image_path: str) -> dict:
    ui_models = [m for m in config["models"] if "ui-grounding" in m.get("capabilities", [])]
    if not ui_models:
        return {"status": "error", "reason": "no_ui_grounding_model_configured", "attempts": []}
    model = sorted(ui_models, key=lambda m: m.get("priority", 999))[0]  # 目前无fallback，单点

    if not omniparser_api.health_check(model["base_url"]):
        return {"status": "error", "reason": "omniparser_unavailable",
                "detail": f"health check failed for {model['base_url']}", "attempts": []}

    width, height = image_dimensions(image_path)
    convention = model.get("coordinate_convention", "omniparser_pixel")

    t0 = time.time()
    try:
        raw_elements = omniparser_api.detect(model, image_path)
    except AdapterHTTPError as e:
        return {"status": "error", "reason": e.kind, "attempts": [{"model": model["name"], "status": e.kind}]}
    latency_ms = int((time.time() - t0) * 1000)

    converted = []
    for el in raw_elements:
        box = bbox_utils.convert_box(el.get("box"), convention, width, height)
        if box is None:
            continue
        converted.append({"label": el["label"], "box": [round(v) for v in box],
                           "confidence": "high", "type": "element"})

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
        "status": "success", "model_used": model["name"], "provider": model["provider"],
        "query_mode": "enumerate_then_filter", "matched_query": query,
        "filter_matched": bool(matched) if query else None,
        "total_elements_detected": total, "result": result,
        "attempts": [{"model": model["name"], "status": "success", "latency_ms": latency_ms}],
    }
    if note:
        response["note"] = note
    return response


# ────────────────────────── --verify-grounding: 实测grounding准确度 ──────────────────────────

FIXTURE_DIR = SCRIPT_DIR / "fixtures"
FIXTURE_IMAGE = FIXTURE_DIR / "grounding-probe.png"
FIXTURE_TRUTH = FIXTURE_DIR / "grounding-probe-truth.json"
IOU_PASS_THRESHOLD = 0.5  # 经验阈值：达到这个IoU认为"具备基本可用的grounding能力"


def run_verify_grounding(config: dict, model_name: str) -> dict:
    model = next((m for m in config["models"] if m["name"] == model_name), None)
    if model is None:
        return {"status": "error", "reason": "model_not_found",
                "detail": f"config里没有名为'{model_name}'的model"}

    if not FIXTURE_IMAGE.is_file() or not FIXTURE_TRUTH.is_file():
        return {"status": "error", "reason": "fixture_missing",
                "detail": "探测图/ground truth文件缺失，检查scripts/fixtures/目录"}

    truth = json.loads(FIXTURE_TRUTH.read_text())
    gt_box = truth["ground_truth_box"]

    api_key = env_security.resolve_key(model.get("api_key_env"))
    if model.get("api_key_env") and api_key is None:
        return {"status": "error", "reason": "no_key",
                "detail": f"'{model_name}'需要{model['api_key_env']}但未配置"}

    locate_prompt = config["roles"]["locate"]["system_prompt"]
    width, height = image_dimensions(str(FIXTURE_IMAGE))

    t0 = time.time()
    try:
        raw_text = call_model(model, api_key, locate_prompt, f"找出{truth['target_label']}", [str(FIXTURE_IMAGE)])
    except AdapterHTTPError as e:
        return {"status": "error", "reason": e.kind, "detail": str(e)}
    latency_ms = int((time.time() - t0) * 1000)

    convention = model.get("coordinate_convention", "gemini_1000")
    valid_entries, dropped = bbox_utils.extract_and_validate(raw_text, convention, width, height)

    if not valid_entries:
        return {
            "status": "completed", "model": model_name, "latency_ms": latency_ms,
            "best_iou": 0.0, "raw_response_sample": raw_text[:300],
            "recommendation": (
                f"未能从响应中提取出合法bbox（dropped={dropped}），不建议给'{model_name}'打grounding标签，"
                f"或该model的坐标格式约定(coordinate_convention)配置有误，先用-v核对raw_response"
            ),
        }

    best_iou = max(bbox_utils.iou(gt_box, e["box"]) for e in valid_entries)
    verdict = "达标" if best_iou >= IOU_PASS_THRESHOLD else "不达标"
    recommendation = (
        f"IoU={best_iou:.2f}（阈值{IOU_PASS_THRESHOLD}），{verdict}。"
        + (f"有实测证据支持给'{model_name}'打grounding标签。"
           if best_iou >= IOU_PASS_THRESHOLD else
           f"不建议给'{model_name}'打grounding标签——即使返回了格式合法的bbox，位置也不够准。"
           f"注意：单张探测图只能说明'至少不是完全瞎编'，不能证明在真实复杂场景下同样准确，"
           f"建议正式启用前再用你的真实场景图片人工抽查几次。")
    )

    return {
        "status": "completed", "model": model_name, "latency_ms": latency_ms,
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
                         help="强制指定单个model名字，跳过priority排序与fallback，专用于测试/验证某个model，"
                              "不建议在生产调用中使用（失去容灾能力）")
    parser.add_argument("--verify-grounding", metavar="MODEL_NAME", default=None,
                         help="用内置探测图实测某个model的grounding准确度(IoU)，给'该不该打grounding标签'"
                              "提供实测证据而非仅凭文档宣称。指定后忽略-i/-r等其他分析参数")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    config = load_config(Path(args.config))

    if args.verify_grounding:
        result = run_verify_grounding(config, args.verify_grounding)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result["status"] == "completed" else 1)

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
        logger_mod.log_event(
            log_cfg["file"], log_cfg.get("rotate_mb", 50), log_cfg.get("rotate_keep", 3),
            role=role_name, model=result.get("model_used"), provider=result.get("provider"),
            status=result.get("status"), latency_ms=last.get("latency_ms"),
            image=args.image, dropped_count=result.get("dropped_count"),
            error_kind=result.get("reason"),
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
        elif any(a.get("status") == "rpm_limited" for a in result.get("attempts", [])) and \
                all(a.get("status") in ("rpm_limited",) for a in result.get("attempts", [])):
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
