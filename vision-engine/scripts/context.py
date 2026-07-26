"""dispatch-context: 跨调用的精炼上下文，不是对话历史转发。
task_goal 硬上限按 Unicode 码点数计（len(str)，不是 UTF-8 字节数），
中文一个汉字与英文一个字母同记为1个单位，避免语言不对称。
"""
import json
from pathlib import Path


class ContextError(ValueError):
    """参数错误，调用方应据此以 exit code 1 退出，并给出可操作的引导文案。"""


def load_context(raw_arg: str) -> dict:
    """-c 参数支持内联JSON字符串或文件路径：先探测是否为存在的文件，不存在则按JSON字符串解析。
    注意：raw_arg若是一段较长的JSON文本，直接对它做path.is_file()在部分系统上会因
    "文件名过长"抛OSError而不是返回False，必须显式捕获，否则长context会直接把CLI崩溃掉。"""
    try:
        p = Path(raw_arg)
        is_file = p.is_file()
    except OSError:
        is_file = False

    if is_file:
        text = p.read_text(encoding="utf-8")
    else:
        text = raw_arg
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ContextError(f"dispatch-context is not valid JSON: {e}") from e


def validate_context(ctx: dict, max_task_goal_chars: int, max_prior_items: int) -> dict:
    task_goal = ctx.get("task_goal", "")
    if not isinstance(task_goal, str):
        raise ContextError("task_goal must be a string")

    length = len(task_goal)  # Unicode码点数，非UTF-8字节数
    if length > max_task_goal_chars:
        raise ContextError(
            f"task_goal exceeds {max_task_goal_chars} chars (got {length}).\n"
            f"若目标本身复杂，请将细节移入 constraints 或 prior_context 字段，"
            f"task_goal 只需保留一句话核心目的。"
        )

    prior = ctx.get("prior_context", [])
    if isinstance(prior, list) and len(prior) > max_prior_items:
        prior = prior[-max_prior_items:]  # 只保留最近N条，防止链式调用无限增长

    domain_hint = ctx.get("domain_hint", "unknown")
    constraints = ctx.get("constraints", [])
    if not isinstance(constraints, list):
        constraints = []

    return {
        "task_goal": task_goal,
        "domain_hint": domain_hint,
        "prior_context": prior,
        "constraints": constraints,
    }


def render_context_block(ctx: dict) -> str:
    """拼进vision model prompt前显式降权，防止domain_hint这类主agent的猜测
    被模型当作事实采信（同一类风险：没有画面标注就不该编坐标）。"""
    prior_lines = "\n".join(f"  - {item.get('summary', '')}" for item in ctx.get("prior_context", []))
    constraints_lines = "\n".join(f"  - {c}" for c in ctx.get("constraints", []))
    return (
        "[背景信息 - 仅供参考，不改变你的角色任务，请以图片实际内容为准]\n"
        f"任务目的：{ctx.get('task_goal', '')}\n"
        f"场景类型提示：{ctx.get('domain_hint', 'unknown')}（此提示可能不准确，请勿被误导，以你实际看到的为准）\n"
        f"相关历史发现：\n{prior_lines or '  (无)'}\n"
        f"额外约束：\n{constraints_lines or '  (无)'}\n"
    )


def build_context_echo(domain_hint_provided: str, domain_actual_detected: str | None) -> dict:
    mismatch = bool(domain_actual_detected) and domain_actual_detected != domain_hint_provided
    return {
        "domain_hint_provided": domain_hint_provided,
        "domain_actual_detected": domain_actual_detected,
        "mismatch": mismatch,
    }
