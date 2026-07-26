"""bbox_list 类结果的容错提取 + 坐标转换 + 逐条校验（部分接受，不因个别条目非法整体判失败）。

统一输出格式：box = [ymin, xmin, ymax, xmax]，0-1000 归一化，与 Gemini 原生约定一致
（因为 gemini_1000 不需要转换，直接作为基准，减少一次不必要的换算）。
"""
import json
import re

VALID_CONFIDENCE = {"high", "medium", "low"}
VALID_TYPE = {"element", "effect", "region"}


def extract_json_array(raw_text: str) -> list | None:
    """容错提取：模型即使被要求'不要markdown包裹'，实际仍可能包裹或加解释性文字。
    策略：先剥离```json ... ```代码块，再找第一个'['到匹配的']'，最后尝试json.loads。
    """
    text = raw_text.strip()

    fence_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    else:
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, list):
        return None
    return parsed


def convert_box(box: list, convention: str, img_width: int, img_height: int) -> list | None:
    """按模型坐标约定转换为统一格式 [ymin,xmin,ymax,xmax] 0-1000。
    转换失败（缺宽高信息、box字段不完整）返回 None，调用方据此丢弃该条目而非整体失败。
    """
    if not isinstance(box, list) or len(box) != 4:
        return None
    try:
        a, b, c, d = [float(v) for v in box]
    except (TypeError, ValueError):
        return None

    if convention == "gemini_1000":
        # 已是 [ymin,xmin,ymax,xmax] 0-1000，直接透传
        return [a, b, c, d]

    if convention in ("qwen_pixel", "omniparser_pixel"):
        # 假设原生给的是 [x1,y1,x2,y2] 像素坐标，需要宽高才能归一化
        if img_width <= 0 or img_height <= 0:
            return None
        x1, y1, x2, y2 = a, b, c, d
        ymin = round(y1 / img_height * 1000)
        xmin = round(x1 / img_width * 1000)
        ymax = round(y2 / img_height * 1000)
        xmax = round(x2 / img_width * 1000)
        return [ymin, xmin, ymax, xmax]

    return None  # 未知convention，保守丢弃而非猜测性透传


def validate_entry(entry: dict, convention: str, img_width: int, img_height: int) -> dict | None:
    """校验单条bbox结果，返回转换+校验后的合法条目，或None（该条目被丢弃）。"""
    if not isinstance(entry, dict):
        return None
    label = entry.get("label")
    box = entry.get("box")
    confidence = entry.get("confidence")
    etype = entry.get("type", "element")

    if not isinstance(label, str) or not label.strip():
        return None
    if confidence not in VALID_CONFIDENCE:
        return None
    if etype not in VALID_TYPE:
        return None

    converted = convert_box(box, convention, img_width, img_height)
    if converted is None:
        return None
    if not all(0 <= v <= 1000 for v in converted):
        return None
    ymin, xmin, ymax, xmax = converted
    if ymin >= ymax or xmin >= xmax:
        return None  # 退化/非法框（零面积或反向）

    return {
        "label": label.strip(),
        "box": [round(v) for v in converted],
        "confidence": confidence,
        "type": etype,
    }


def iou(box_a: list, box_b: list) -> float:
    """计算两个[ymin,xmin,ymax,xmax]格式框的IoU(交并比)，用于实测评估grounding准确度，
    而不是仅凭provider文档宣称就给model打grounding标签。"""
    ay1, ax1, ay2, ax2 = box_a
    by1, bx1, by2, bx2 = box_b

    inter_y1, inter_x1 = max(ay1, by1), max(ax1, bx1)
    inter_y2, inter_x2 = min(ay2, by2), min(ax2, bx2)
    inter_h, inter_w = max(0, inter_y2 - inter_y1), max(0, inter_x2 - inter_x1)
    inter_area = inter_h * inter_w

    area_a = max(0, ay2 - ay1) * max(0, ax2 - ax1)
    area_b = max(0, by2 - by1) * max(0, bx2 - bx1)
    union = area_a + area_b - inter_area
    if union <= 0:
        return 0.0
    return inter_area / union


def extract_and_validate(raw_text: str, convention: str, img_width: int, img_height: int) -> tuple[list, int]:
    """主入口：返回 (合法条目列表, 丢弃数量)。合法条目数为0时，调用方应判定该次调用为 invalid_schema。"""
    parsed = extract_json_array(raw_text)
    if parsed is None:
        return [], -1  # -1 表示连解析都失败，区别于"解析出来但条目全部非法"

    valid = []
    dropped = 0
    for entry in parsed:
        v = validate_entry(entry, convention, img_width, img_height)
        if v is not None:
            valid.append(v)
        else:
            dropped += 1
    return valid, dropped
