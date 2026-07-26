"""本地RPM限流，用flock包住整个读改写过程，修复v1版本"读取→判断→追加→写回"
四步无锁操作在并发场景下的更新丢失问题。"""
import fcntl
import json
import time
from pathlib import Path

LOCK_TIMEOUT_SECONDS = 0.2


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


def is_rpm_exceeded(model_name: str, rpm_limit: int) -> bool:
    """True = 超限或获取锁超时(保守跳过，不做无锁写入)。False = 未超限，本次调用已计入。"""
    lock_path = Path(f"/tmp/vision-engine-ratelimit-{model_name}.lock")
    lock_path.touch(exist_ok=True)

    with open(lock_path, "r+") as lf:
        try:
            _acquire_with_timeout(lf.fileno(), LOCK_TIMEOUT_SECONDS)
        except _LockTimeout:
            return True  # 超时未获取锁，判定为限流，宁可保守跳过

        try:
            data_path = Path(f"/tmp/vision-engine-ratelimit-{model_name}.json")
            now = time.time()
            timestamps = []
            if data_path.is_file():
                try:
                    timestamps = json.loads(data_path.read_text())
                    if not isinstance(timestamps, list):
                        timestamps = []
                except (json.JSONDecodeError, ValueError):
                    timestamps = []  # 文件损坏，fail-open：重置为空记录

            timestamps = [t for t in timestamps if now - t < 60]

            if len(timestamps) >= rpm_limit:
                return True

            timestamps.append(now)
            data_path.write_text(json.dumps(timestamps))
            return False
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
