"""审计日志：JSONL追加写入，仅允许白名单字段(§3.1: 绝不整体dump request/response，避免泄露key)。
超过rotate_mb后按 log.1.jsonl / log.2.jsonl 归档，保留rotate_keep份。"""
import json
import time
from pathlib import Path

ALLOWED_FIELDS = {
    "ts", "role", "model", "provider", "status", "latency_ms",
    "image", "tokens_in", "tokens_out", "dropped_count", "error_kind",
}


def _rotate_if_needed(log_path: Path, rotate_mb: float, rotate_keep: int) -> None:
    if not log_path.is_file():
        return
    if log_path.stat().st_size < rotate_mb * 1024 * 1024:
        return
    for i in range(rotate_keep - 1, 0, -1):
        src = log_path.with_name(f"{log_path.stem}.{i}{log_path.suffix}")
        dst = log_path.with_name(f"{log_path.stem}.{i + 1}{log_path.suffix}")
        if src.is_file():
            src.rename(dst)
    log_path.rename(log_path.with_name(f"{log_path.stem}.1{log_path.suffix}"))


def log_event(log_file: str, rotate_mb: float, rotate_keep: int, **fields) -> None:
    filtered = {k: v for k, v in fields.items() if k in ALLOWED_FIELDS}
    filtered["ts"] = filtered.get("ts") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    path = Path(log_file).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed(path, rotate_mb, rotate_keep)
        with open(path, "a") as f:
            f.write(json.dumps(filtered, ensure_ascii=False) + "\n")
    except OSError:
        pass  # 日志目录权限问题：降级为不记日志，不阻断主流程
