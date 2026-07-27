"""通用 quota 限流 + 429冷却。

支持任意 (metric, window_seconds, limit) 组合：
- metric="requests" 窗口内累计请求数
- metric="tokens"   窗口内累计 token 数
未来可扩展 cost 等其他 metric。

每个 (metric, window) 组合一个文件，路径：
  {BASE_DIR}/{metric}-{window}s-{model_name}.json

数据 schema:
- requests: {"timestamps": [ts1, ts2, ...]}
- tokens:   {"entries": [[ts, n], [ts, n], ...]}

flock 保护并发，损坏文件 fail-open（按空处理）。"""
import fcntl
import json
import os
import time
from pathlib import Path

_BASE_DIR = os.path.expanduser("~/.local/share/vision-engine/ratelimit")
LOCK_TIMEOUT_SECONDS = 0.2
SUPPORTED_METRICS = ("requests", "tokens")


class _LockTimeout(Exception):
    pass


def _acquire_with_timeout(fileno: int, timeout: float):
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fileno, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise _LockTimeout()
            time.sleep(0.01)


def _data_path(metric: str, window_seconds: int, model_name: str) -> Path:
    """每个 (metric, window, model) 组合一个独立文件。"""
    return Path(_BASE_DIR) / f"{metric}-{window_seconds}s-{model_name}.json"


def _read_state(path: Path, metric: str) -> dict:
    """读文件 → 解析为当前 metric 的 state，损坏/missing → fail-open 空状态。"""
    if not path.is_file():
        return _empty_state(metric)
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return _empty_state(metric)
    except (json.JSONDecodeError, ValueError, OSError):
        return _empty_state(metric)

    # 校验必需键 + 逐条格式
    if metric == "requests":
        if "timestamps" not in data or not isinstance(data["timestamps"], list):
            return _empty_state(metric)
        if not all(isinstance(t, (int, float)) for t in data["timestamps"]):
            return _empty_state(metric)
        return data
    elif metric == "tokens":
        if "entries" not in data or not isinstance(data["entries"], list):
            return _empty_state(metric)
        valid_entries = []
        for e in data["entries"]:
            if isinstance(e, (list, tuple)) and len(e) == 2 \
                    and isinstance(e[0], (int, float)) and isinstance(e[1], (int, float)):
                valid_entries.append([float(e[0]), int(e[1])])
        if not valid_entries and data["entries"]:
            # 文件有数据但全部非法 → 视为损坏，fail-open
            return _empty_state(metric)
        data["entries"] = valid_entries
        return data
    return _empty_state(metric)


def _empty_state(metric: str) -> dict:
    if metric == "requests":
        return {"timestamps": []}
    if metric == "tokens":
        return {"entries": []}
    raise ValueError(f"unknown metric: {metric}")


def _prune(state: dict, window_seconds: int, now: float) -> dict:
    """按 window 滑动裁剪过期记录。"""
    if "timestamps" in state:
        state["timestamps"] = [t for t in state["timestamps"] if now - t < window_seconds]
    elif "entries" in state:
        state["entries"] = [[t, n] for t, n in state["entries"] if now - t < window_seconds]
    return state


def _with_lock(lock_path: Path, fn):
    """在 flock 保护下执行 fn(lock_fileno)；锁超时 → 返回 None 让调用方保守处理。"""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch(exist_ok=True)
    with open(lock_path, "r+") as lf:
        try:
            _acquire_with_timeout(lf.fileno(), LOCK_TIMEOUT_SECONDS)
        except _LockTimeout:
            return None
        try:
            return fn(lf)
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def _count_in_window(state: dict) -> int:
    """窗口内累计值：requests = len(timestamps), tokens = sum(entries 第二列)。"""
    if "timestamps" in state:
        return len(state["timestamps"])
    if "entries" in state:
        try:
            return sum(n for _, n in state["entries"])
        except (TypeError, ValueError):
            return 0  # 格式异常 → 按空窗口处理
    return 0


def _record(state: dict, value: int, now: float) -> dict:
    """追加一条记录到 state。"""
    if "timestamps" in state:
        state["timestamps"].append(now)
    elif "entries" in state:
        state["entries"].append([now, value])
    return state


# ═══════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════

def check_quota(
    model_name: str,
    quota_cfg: dict,
    projected: int = 0,
    _now: float | None = None,
) -> bool:
    """检查 model 是否在 quota 窗口内已超限（或加上 projected 后会超限）。

    projected: 本次调用预估消耗的量（requests=1 可省略；tokens 时传 prompt+max_tokens 估算）。
    注意：projected 是 dry-run，不写入文件。响应后用 record_usage 精确更新。

    返回 True = 超限（跳过）, False = 可用（请求会被发）。
    锁超时 → 保守返回 True（宁可跳过也别爆服务端）。
    """
    metric = quota_cfg["metric"]
    if metric not in SUPPORTED_METRICS:
        raise ValueError(f"unsupported metric: {metric!r}, supported: {SUPPORTED_METRICS}")
    window = quota_cfg["window_seconds"]
    limit = quota_cfg["limit"]
    now = _now if _now is not None else time.time()
    path = _data_path(metric, window, model_name)
    lock_path = path.with_suffix(".lock")

    def _do(_lf):
        state = _read_state(path, metric)
        state = _prune(state, window, now)
        current = _count_in_window(state)
        if current + projected >= limit:
            return True  # 超限，不写入
        # 不写入 projected（dry-run）；只有 record_usage 才实际记录
        return False

    result = _with_lock(lock_path, _do)
    return result if result is not None else True  # lock timeout → conservative


def record_usage(
    model_name: str,
    quota_cfg: dict,
    actual: int,
    _now: float | None = None,
) -> None:
    """响应成功后调用，把实际消耗记入 quota 文件。

    失败调用（429/timeout/5xx）不要调这个——只有 success 才消耗配额。
    锁超时静默跳过（非关键写）。
    """
    metric = quota_cfg["metric"]
    if metric not in SUPPORTED_METRICS:
        raise ValueError(f"unsupported metric: {metric!r}")
    window = quota_cfg["window_seconds"]
    now = _now if _now is not None else time.time()
    path = _data_path(metric, window, model_name)
    lock_path = path.with_suffix(".lock")

    def _do(_lf):
        state = _read_state(path, metric)
        state = _prune(state, window, now)
        state = _record(state, actual, now)
        try:
            path.write_text(json.dumps(state))
        except OSError:
            pass  # 写入失败不阻断主流程

    _with_lock(lock_path, _do)


# ═══════════════════════════════════════════════════════════
# Cooldown (独立维度，429 触发)
# ═══════════════════════════════════════════════════════════

def _cooldown_path(model_name: str) -> Path:
    return Path(_BASE_DIR) / f"cooldown-{model_name}.json"


def set_cooldown(model_name: str, cooldown_seconds: int, _now: float | None = None) -> None:
    """429 触发后设置冷却期。非关键写：锁超时静默跳过。"""
    now = _now if _now is not None else time.time()
    path = _cooldown_path(model_name)
    lock_path = path.with_suffix(".lock")

    def _do(_lf):
        try:
            path.write_text(json.dumps({"cooldown_until": now + cooldown_seconds}))
        except OSError:
            pass

    try:
        _with_lock(lock_path, _do)
    except FileNotFoundError:
        return


def is_cooled_down(model_name: str, _now: float | None = None) -> bool:
    """True = 在冷却期内（跳过该模型）。False = 无冷却或已过期。
    锁超时 → 保守返回 True（宁可跳过也不撞 429）。
    """
    now = _now if _now is not None else time.time()
    path = _cooldown_path(model_name)
    lock_path = path.with_suffix(".lock")

    def _do(_lf):
        if not path.is_file():
            return False
        try:
            data = json.loads(path.read_text())
            cooldown_until = data.get("cooldown_until")
            if cooldown_until is None:
                return False
        except (json.JSONDecodeError, ValueError, OSError):
            return False
        if now < cooldown_until:
            return True
        # 过期则清理文件
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return False

    result = _with_lock(lock_path, _do)
    return result if result is not None else True  # lock timeout → conservative
