"""缓存：key = sha256(image_bytes + role)，不含prompt。
自定义-p或提供-c(dispatch-context)时，调用方应在main里跳过缓存，本模块不做这个判断。"""
import hashlib
import json
import time
from pathlib import Path


def cache_key(image_path: str, role: str) -> str:
    with open(image_path, "rb") as f:
        image_bytes = f.read()
    h = hashlib.sha256()
    h.update(image_bytes)
    h.update(role.encode())
    return h.hexdigest()


def get(cache_dir: str, key: str, ttl_hours: float) -> dict | None:
    path = Path(cache_dir).expanduser() / f"{key}.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, ValueError):
        return None
    if time.time() - payload.get("_cached_at", 0) > ttl_hours * 3600:
        return None
    return payload.get("result")


def set(cache_dir: str, key: str, result: dict) -> None:
    d = Path(cache_dir).expanduser()
    try:
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{key}.json"
        path.write_text(json.dumps({"_cached_at": time.time(), "result": result}))
    except OSError:
        pass  # 缓存目录权限问题：降级为不缓存，不阻断主流程
