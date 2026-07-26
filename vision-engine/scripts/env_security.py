"""API Key 安全设计实现（§3.1）：
读取优先级 os.environ > ~/.env > cwd/.env；.env权限检查、.gitignore检查、
日志/报错脱敏在 logger.py 和 adapters/common.py 里各自落实，不重复在这里做。
"""
import os
import stat
import subprocess
from pathlib import Path


def _parse_env_file(path: Path) -> dict:
    env = {}
    if not path.is_file():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def resolve_key(api_key_env: str | None) -> str | None:
    """按优先级解析某个模型的API key。api_key_env为None时（如omniparser本地服务）直接返回None。"""
    if api_key_env is None:
        return None
    if api_key_env in os.environ:
        return os.environ[api_key_env]

    home_env = _parse_env_file(Path.home() / ".env")
    if api_key_env in home_env:
        return home_env[api_key_env]

    cwd_env = _parse_env_file(Path.cwd() / ".env")
    if api_key_env in cwd_env:
        return cwd_env[api_key_env]

    return None


def check_env_file_permissions(verbose: bool = False) -> list[str]:
    """检查.env文件权限是否为600，不是则警告(不阻断)。返回警告信息列表。"""
    warnings = []
    for path in (Path.home() / ".env", Path.cwd() / ".env"):
        if not path.is_file():
            continue
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode != 0o600:
            warnings.append(f"警告: {path} 权限为 {oct(mode)}，建议 chmod 600 {path}")
    return warnings


def check_gitignore(verbose: bool = False) -> list[str]:
    """.env所在目录若在git仓库内，检查.gitignore是否包含.env，缺失则警告(不阻断)。"""
    warnings = []
    cwd_env = Path.cwd() / ".env"
    if not cwd_env.is_file():
        return warnings

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=Path.cwd(), capture_output=True, text=True, timeout=3,
        )
        if result.returncode != 0:
            return warnings  # 不在git仓库内
        repo_root = Path(result.stdout.strip())
    except (subprocess.SubprocessError, OSError):
        return warnings

    gitignore = repo_root / ".gitignore"
    content = gitignore.read_text() if gitignore.is_file() else ""
    if ".env" not in content.splitlines():
        warnings.append(
            f"警告: 检测到 {cwd_env} 但 {gitignore} 未包含 '.env' 一行，"
            f"存在API key被误提交到git的风险"
        )
    return warnings


def precheck_key_existence(models: list[dict]) -> tuple[list[dict], list[str]]:
    """启动时预检：只查环境变量/`.env`中是否存在对应key，不做网络验证。
    返回 (可用模型列表, 被剔除模型的名字列表)，供fallback链更快收敛到真正可用的模型。
    """
    available = []
    unavailable = []
    for m in models:
        api_key_env = m.get("api_key_env")
        if api_key_env is None:
            available.append(m)  # 无需key的provider（如self-hosted omniparser）
            continue
        if resolve_key(api_key_env) is not None:
            available.append(m)
        else:
            unavailable.append(m["name"])
    return available, unavailable
