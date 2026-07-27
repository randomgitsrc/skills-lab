#!/usr/bin/env python3
"""vision-stats — 调用统计 + 限流矫正工具。

子命令:
  summary  日志统计：调用次数/token/成功率/role分布
  quota    当前各模型各窗口本地 quota 使用率
  set      手动设置某模型某窗口的已用计数
  sync     自动探测+矫正（仅 Anthropic/OpenAI，Google 无 rate limit header）
  clean    删除已不存在模型的 ratelimit/cooldown 文件
  reset    重置指定模型的全部 ratelimit/cooldown 文件

用法:
  python3 scripts/vision-stats.py summary
  python3 scripts/vision-stats.py quota
  python3 scripts/vision-stats.py set google-free/gemini-3.6-flash --used 15 --metric requests --window 86400
  python3 scripts/vision-stats.py sync --model anthropic/claude-sonnet-5
  python3 scripts/vision-stats.py clean --yes
  python3 scripts/vision-stats.py reset google-free/gemini-3.6-flash --yes
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

# ─── path setup ───
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import ratelimit

# ─── constants ───
DEFAULT_LOG = os.path.expanduser("~/.local/share/vision-engine/log.jsonl")
DEFAULT_CONFIG = str(SCRIPT_DIR.parent / "config" / "vision-config.json")


# ─── config helpers ───

def load_models_from_config(config_path: str) -> list[dict]:
    """Load config and return flattened model list (provider/name as ref)."""
    with open(config_path) as f:
        raw = json.load(f)
    models = []
    for provider_id, provider_cfg in raw.get("providers", {}).items():
        for m in provider_cfg.get("models", []):
            entry = dict(m)
            entry["provider"] = provider_id
            entry["_ref"] = f"{provider_id}/{m['name']}"
            entry["_sanitized"] = f"{provider_id}__{m['name']}"
            models.append(entry)
    return models


def _sanitize(ref: str) -> str:
    return ref.replace("/", "__")


def _desanitize(name: str) -> str:
    return name.replace("__", "/")


# ═══════════════════════════════════════════════════════════
# summary — 日志统计
# ═══════════════════════════════════════════════════════════

def cmd_summary(log_path: str, config_path: str, date_from: str | None = None,
                date_to: str | None = None) -> dict:
    """从审计日志汇总调用统计。返回结构化结果。"""
    by_model = defaultdict(lambda: {
        "success": 0, "error": 0, "tokens_in": 0, "tokens_out": 0,
        "roles": defaultdict(int),
    })
    fallback_stats = defaultdict(lambda: {
        "model": "", "attempts": 0, "success": 0, "quota_exceeded": 0, "other_fail": 0,
    })

    with open(log_path) as f:
        for line in f:
            try:
                e = json.loads(line.strip())
            except (json.JSONDecodeError, ValueError):
                continue

            # Date filtering
            ts = e.get("ts", "")
            if date_from and ts < date_from:
                continue
            if date_to and ts > date_to:
                continue

            model = e.get("model") or "__error__"
            d = by_model[model]
            status = e.get("status")

            if status == "success":
                d["success"] += 1
                d["tokens_in"] += e.get("tokens_in") or 0
                d["tokens_out"] += e.get("tokens_out") or 0
                d["roles"][e.get("role") or "?"] += 1
            else:
                d["error"] += 1
                d["roles"][e.get("role") or "?"] += 1

            # Fallback stats from attempts
            for att in e.get("attempts", []):
                att_model = att.get("model") or "?"
                att_status = att.get("status") or "?"
                fb = fallback_stats[att_model]
                fb["model"] = att_model
                fb["attempts"] += 1
                if att_status == "success":
                    fb["success"] += 1
                elif "quota_exceeded" in str(att_status):
                    fb["quota_exceeded"] += 1
                else:
                    fb["other_fail"] += 1

    # Build output
    model_list = []
    for model in sorted(by_model.keys()):
        d = by_model[model]
        display = model if model != "__error__" else "(all_models_failed)"
        model_list.append({
            "model": display,
            "success": d["success"],
            "error": d["error"],
            "tokens_in": d["tokens_in"],
            "tokens_out": d["tokens_out"],
            "roles": dict(d["roles"]),
        })

    return {
        "models": model_list,
        "total_success": sum(d["success"] for d in by_model.values()),
        "total_error": sum(d["error"] for d in by_model.values()),
        "total_tokens_in": sum(d["tokens_in"] for d in by_model.values()),
        "total_tokens_out": sum(d["tokens_out"] for d in by_model.values()),
        "fallback_stats": [fb for fb in fallback_stats.values()],
    }


def _print_summary(result: dict):
    """Pretty-print summary result."""
    print("\n=== 调用统计 ===\n")
    header = f"{'模型':<40} {'成功':>5} {'失败':>5} {'tokens_in':>10} {'tokens_out':>10} 角色"
    print(header)
    print("─" * len(header) + "─" * 30)
    for m in result["models"]:
        roles = ", ".join(f"{k}:{v}" for k, v in sorted(m["roles"].items()))
        print(f"{m['model']:<40} {m['success']:>5} {m['error']:>5} "
              f"{m['tokens_in']:>10,} {m['tokens_out']:>10,} {roles}")
    print(f"\nTOTAL: 成功 {result['total_success']}  失败 {result['total_error']}  "
          f"tokens_in {result['total_tokens_in']:,}  tokens_out {result['total_tokens_out']:,}")

    # Fallback stats
    fb = result["fallback_stats"]
    if fb:
        print(f"\n=== Fallback 统计 ===\n")
        print(f"{'模型':<40} {'尝试':>6} {'成功':>5} {'quota_ex':>8} {'失败':>5}")
        for f in sorted(fb, key=lambda x: x["model"]):
            print(f"{f['model']:<40} {f['attempts']:>6} {f['success']:>5} "
                  f"{f['quota_exceeded']:>8} {f['other_fail']:>5}")


# ═══════════════════════════════════════════════════════════
# quota — 当前使用率
# ═══════════════════════════════════════════════════════════

def cmd_quota(config_path: str) -> list[dict]:
    """读取各模型各窗口的本地 quota 使用率。返回条目列表。"""
    models = load_models_from_config(config_path)
    now = time.time()
    entries = []

    for m in models:
        ref = m["_ref"]
        sanitized = m["_sanitized"]

        # Check cooldown
        cd_path = ratelimit._cooldown_path(sanitized)
        cooldown_status = "-"
        if cd_path.is_file():
            try:
                cd_data = json.loads(cd_path.read_text())
                if now < cd_data.get("cooldown_until", 0):
                    remaining = int(cd_data["cooldown_until"] - now)
                    cooldown_status = f"冷却中({remaining}s)"
                else:
                    cooldown_status = "expired"
            except (json.JSONDecodeError, ValueError):
                cooldown_status = "expired"

        # Check each quota
        for q in m.get("quotas", []):
            metric = q["metric"]
            window = q["window_seconds"]
            limit = q["limit"]

            path = ratelimit._data_path(metric, window, sanitized)
            state = ratelimit._read_state(path, metric)
            state = ratelimit._prune(state, window, now)
            current = ratelimit._count_in_window(state)
            pct = (current / limit * 100) if limit > 0 else 0.0

            entries.append({
                "key": f"{ref}|{metric}|{window}s",
                "model": ref,
                "metric": metric,
                "window": window,
                "current": current,
                "limit": limit,
                "pct": round(pct, 1),
                "cooldown": cooldown_status if q is m["quotas"][0] else "",
            })

    return entries


def _print_quota(entries: list[dict]):
    """Pretty-print quota result."""
    print("\n=== 本地 quota 使用率 ===\n")
    print(f"{'模型':<40} {'窗口':<14} {'当前/上限':>14} {'使用率':>8} cooldown")
    print("─" * 90)
    for e in entries:
        if e["metric"] == "tokens" and e["limit"] >= 1000:
            lim_str = f"{e['limit']//1000}K"
            cur_str = f"{e['current']//1000}K" if e['current'] >= 1000 else str(e['current'])
        else:
            lim_str = str(e["limit"])
            cur_str = str(e["current"])
        window_str = f"{e['metric'][:3]}/{e['window']}s"
        print(f"{e['model']:<40} {window_str:<14} {cur_str + '/' + lim_str:>14} "
              f"{e['pct']:>7.1f}% {e.get('cooldown', '')}")


# ═══════════════════════════════════════════════════════════
# set — 手动矫正
# ═══════════════════════════════════════════════════════════

def cmd_set(model_ref: str, metric: str, window_seconds: int, used_count: int) -> dict:
    """覆写指定模型指定窗口的 ratelimit 计数为 used_count。"""
    sanitized = _sanitize(model_ref)
    now = time.time()
    path = ratelimit._data_path(metric, window_seconds, sanitized)

    # Read old value for reporting
    old_state = ratelimit._read_state(path, metric)
    old_state = ratelimit._prune(old_state, window_seconds, now)
    old_count = ratelimit._count_in_window(old_state)

    # Build new state
    if metric == "requests":
        if used_count <= 0:
            state = {"timestamps": []}
        else:
            step = window_seconds / max(used_count, 1)
            timestamps = [now - step * i for i in range(used_count)]
            state = {"timestamps": timestamps}
    elif metric == "tokens":
        if used_count <= 0:
            state = {"entries": []}
        else:
            # Distribute into chunks (max 100 entries)
            chunk_size = max(1, used_count // 100)
            entries = []
            remaining = used_count
            t = now
            while remaining > 0:
                n = min(chunk_size, remaining)
                entries.append([t, n])
                remaining -= n
                t -= 0.01
            state = {"entries": entries}
    else:
        raise ValueError(f"unsupported metric: {metric}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))

    return {
        "model": model_ref,
        "metric": metric,
        "window": window_seconds,
        "old_count": old_count,
        "new_count": used_count,
        "path": str(path),
    }


def _print_set(result: dict, config_path: str):
    """Pretty-print set result."""
    # Look up limit from config
    models = load_models_from_config(config_path)
    limit = "?"
    for m in models:
        if m["_ref"] == result["model"]:
            for q in m.get("quotas", []):
                if q["metric"] == result["metric"] and q["window_seconds"] == result["window"]:
                    limit = str(q["limit"])
                    break

    print(f"\n=== 设置 {result['model']} ===")
    print(f"  {result['metric']}/{result['window']}s: "
          f"原值 {result['old_count']}/{limit} → 新值 {result['new_count']}/{limit}")
    print(f"  已覆写 {result['path']}")


# ═══════════════════════════════════════════════════════════
# reset — 重置模型
# ═══════════════════════════════════════════════════════════

def cmd_reset(model_ref: str) -> int:
    """删除指定模型的所有 ratelimit/cooldown 文件。返回删除文件数。"""
    sanitized = _sanitize(model_ref)
    base = Path(ratelimit._BASE_DIR)
    deleted = 0
    for f in list(base.iterdir()):
        if sanitized in f.name:
            f.unlink(missing_ok=True)
            deleted += 1
    return deleted


def _print_reset(model_ref: str, deleted: int):
    print(f"\n=== 重置 {model_ref} ===")
    print(f"  已删除 {deleted} 个文件")
    print(f"  下次调用时自动重建")


# ═══════════════════════════════════════════════════════════
# clean — 清理残留
# ═══════════════════════════════════════════════════════════

def cmd_clean(config_path: str) -> dict:
    """找出并删除已不存在模型的 ratelimit/cooldown 文件 + 过期 cooldown。"""
    models = load_models_from_config(config_path)
    valid_sanitized = {m["_sanitized"] for m in models}
    valid_refs = {m["_ref"] for m in models}

    base = Path(ratelimit._BASE_DIR)
    if not base.is_dir():
        return {"stale_models": {}, "expired_cooldowns": 0, "deleted": 0}

    now = time.time()
    stale_models = defaultdict(list)
    expired_cooldowns = 0
    to_delete = []

    for f in sorted(base.iterdir()):
        name = f.name
        if name.endswith(".lock"):
            continue  # lock files will be cleaned with their .json

        # Extract sanitized model name from filename
        # patterns: {metric}-{window}s-{model}.json, cooldown-{model}.json
        sanitized = None
        if name.startswith("cooldown-") and name.endswith(".json"):
            sanitized = name[len("cooldown-"):-len(".json")]
        elif "-" in name and name.endswith(".json"):
            # e.g. requests-60s-google-free__gemini-3.6-flash.json
            # model part is after the second dash and window spec
            parts = name[:-len(".json")].split("-", 2)
            if len(parts) >= 3:
                # parts = ['requests', '60s', 'google-free__gemini-3.6-flash']
                sanitized = parts[2]

        if sanitized is None:
            continue

        # Check if stale
        if sanitized not in valid_sanitized:
            ref = _desanitize(sanitized)
            stale_models[ref].append(name)
            to_delete.append(f)
            continue

        # Check if expired cooldown
        if name.startswith("cooldown-") and name.endswith(".json"):
            try:
                data = json.loads(f.read_text())
                if now >= data.get("cooldown_until", 0):
                    expired_cooldowns += 1
                    to_delete.append(f)
            except (json.JSONDecodeError, ValueError):
                expired_cooldowns += 1
                to_delete.append(f)

    return {
        "stale_models": dict(stale_models),
        "expired_cooldowns": expired_cooldowns,
        "deleted": len(to_delete),
        "_to_delete": to_delete,
    }


def _print_clean(result: dict, yes: bool):
    print("\n=== 清理不存在模型的限流数据 ===\n")

    if result["stale_models"]:
        print("已删除模型（config 中不存在）:")
        for ref, files in sorted(result["stale_models"].items()):
            print(f"  {ref}: {len(files)} 文件")

    if result["expired_cooldowns"]:
        print(f"\n已过期的 cooldown 文件: {result['expired_cooldowns']} 个")

    if not result["stale_models"] and not result["expired_cooldowns"]:
        print("无残留数据，不需要清理。")
        return

    if not yes:
        answer = input(f"\n共 {result['deleted']} 文件待删除。确认？[y/N] ")
        if answer.lower() != "y":
            print("已取消。")
            return

    for f in result["_to_delete"]:
        f.unlink(missing_ok=True)
    # Also clean associated lock files
    base = Path(ratelimit._BASE_DIR)
    for f in result["_to_delete"]:
        lock = f.with_suffix(".lock")
        if lock.exists():
            lock.unlink(missing_ok=True)

    print(f"已删除 {result['deleted']} 个文件")


# ═══════════════════════════════════════════════════════════
# sync — 自动矫正（仅 Anthropic/OpenAI）
# ═══════════════════════════════════════════════════════════

def cmd_sync(config_path: str, model_filter: str | None = None) -> list[dict]:
    """对支持 rate limit header 的 provider 发探测请求并矫正本地数据。"""
    import env_security
    from adapters.common import AdapterHTTPError

    models = load_models_from_config(config_path)
    results = []

    for m in models:
        ref = m["_ref"]
        if model_filter and ref != model_filter and m.get("alias") != model_filter:
            continue

        api_format = m.get("api_format", "")
        if api_format not in ("anthropic", "openai"):
            results.append({"model": ref, "status": "skipped",
                            "reason": f"{api_format} provider 不返回 rate limit header，用 set 手动矫正"})
            continue

        api_key = env_security.resolve_key(m.get("api_key_env"))
        if not api_key:
            results.append({"model": ref, "status": "skipped", "reason": "无 API key"})
            continue

        # Build minimal probe config
        probe_cfg = dict(m)
        probe_cfg["max_tokens"] = 1
        probe_cfg["timeout"] = 15

        try:
            resp = vision_analyze.call_model(
                probe_cfg, api_key, None, "Reply OK",
                [str(SCRIPT_DIR / "fixtures" / "self-test-probe.png")]
            )
            rl_headers = resp.get("rate_limit_headers", {})
            if not rl_headers:
                results.append({"model": ref, "status": "no_headers",
                                "reason": "响应中无 rate limit header"})
                continue

            # Parse and sync
            synced = []
            for q in m.get("quotas", []):
                metric = q["metric"]
                window = q["window_seconds"]
                limit = q["limit"]

                remaining = _extract_remaining(rl_headers, api_format, metric, window)
                if remaining is None:
                    continue

                used = limit - remaining
                if used < 0:
                    used = 0

                cmd_set(ref, metric, window, used)
                synced.append(f"{metric}/{window}s: remaining={remaining}/{limit} → 本地已用={used}")

            results.append({"model": ref, "status": "synced", "details": synced})

        except (AdapterHTTPError, Exception) as e:
            results.append({"model": ref, "status": "error", "reason": str(e)})

    return results


def _extract_remaining(headers: dict, api_format: str, metric: str,
                        window_seconds: int) -> int | None:
    """从响应头提取指定 metric 的 remaining 值。"""
    headers_lower = {k.lower(): v for k, v in headers.items()}

    if api_format == "anthropic":
        if metric == "requests":
            key = "anthropic-ratelimit-requests-remaining"
        elif metric == "tokens":
            key = "anthropic-ratelimit-input-tokens-remaining"
        else:
            return None
    elif api_format == "openai":
        if metric == "requests":
            key = "x-ratelimit-remaining-requests"
        elif metric == "tokens":
            key = "x-ratelimit-remaining-tokens"
        else:
            return None
    else:
        return None

    val = headers_lower.get(key)
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _print_sync(results: list[dict]):
    print("\n=== 同步服务端 quota 到本地 ===\n")
    for i, r in enumerate(results, 1):
        status = r["status"]
        if status == "synced":
            print(f"[{i}] {r['model']}")
            for d in r["details"]:
                print(f"  {d}")
            print("  ✅ 已同步")
        elif status == "skipped":
            print(f"[{i}] {r['model']}: ⚠️ 跳过: {r['reason']}")
        elif status == "no_headers":
            print(f"[{i}] {r['model']}: ⚠️ {r['reason']}")
        elif status == "error":
            print(f"[{i}] {r['model']}: ❌ 错误: {r['reason']}")


# ═══════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="vision-engine 调用统计 + 限流矫正工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # summary
    p_summary = sub.add_parser("summary", help="日志统计")
    p_summary.add_argument("--log", default=DEFAULT_LOG, help="审计日志路径")
    p_summary.add_argument("--config", default=DEFAULT_CONFIG, help="config 路径")
    p_summary.add_argument("--from", dest="date_from", help="起始日期 (ISO)")
    p_summary.add_argument("--to", dest="date_to", help="结束日期 (ISO)")

    # quota
    p_quota = sub.add_parser("quota", help="当前 quota 使用率")
    p_quota.add_argument("--config", default=DEFAULT_CONFIG, help="config 路径")

    # set
    p_set = sub.add_parser("set", help="手动设置模型 quota 计数")
    p_set.add_argument("model", help="模型引用 (provider/name 或 alias)")
    p_set.add_argument("--used", type=int, required=True, help="已用计数")
    p_set.add_argument("--metric", choices=["requests", "tokens"], default="requests")
    p_set.add_argument("--window", type=int, default=86400, help="窗口秒数 (默认 86400=1天)")
    p_set.add_argument("--config", default=DEFAULT_CONFIG, help="config 路径")

    # sync
    p_sync = sub.add_parser("sync", help="自动探测+矫正 (仅 Anthropic/OpenAI)")
    p_sync.add_argument("--model", help="只矫正指定模型 (默认全部)")
    p_sync.add_argument("--config", default=DEFAULT_CONFIG, help="config 路径")

    # clean
    p_clean = sub.add_parser("clean", help="清理残留数据")
    p_clean.add_argument("--config", default=DEFAULT_CONFIG, help="config 路径")
    p_clean.add_argument("--yes", action="store_true", help="跳过确认")

    # reset
    p_reset = sub.add_parser("reset", help="重置模型限流数据")
    p_reset.add_argument("model", help="模型引用 (provider/name)")
    p_reset.add_argument("--yes", action="store_true", help="跳过确认")

    args = parser.parse_args()

    if args.command == "summary":
        result = cmd_summary(args.log, args.config, args.date_from, args.date_to)
        _print_summary(result)

    elif args.command == "quota":
        entries = cmd_quota(args.config)
        _print_quota(entries)

    elif args.command == "set":
        # Resolve alias
        model_ref = args.model
        models = load_models_from_config(args.config)
        for m in models:
            if m["_ref"] == model_ref or m.get("alias") == model_ref:
                model_ref = m["_ref"]
                break
        result = cmd_set(model_ref, args.metric, args.window, args.used)
        _print_set(result, args.config)

    elif args.command == "sync":
        results = cmd_sync(args.config, args.model)
        _print_sync(results)

    elif args.command == "clean":
        result = cmd_clean(args.config)
        _print_clean(result, args.yes)

    elif args.command == "reset":
        if not args.yes:
            answer = input(f"确认重置 {args.model} 的全部限流数据？[y/N] ")
            if answer.lower() != "y":
                print("已取消。")
                return
        deleted = cmd_reset(args.model)
        _print_reset(args.model, deleted)


if __name__ == "__main__":
    main()
