"""OmniParser 本地检测服务适配。
与其他adapter不同：这不是对话模型，是专门的UI元素检测服务，
输入一张图，输出全部检测到的可交互元素（不接受自然语言查询）。
本地过滤（enumerate_then_filter）逻辑不在这里，在主脚本 locate_ui.py 里做，
这个模块只负责"调用服务、拿到结构化元素列表"这一层。
"""
import httpx

from .common import encode_image, classify_http_error, AdapterHTTPError


def health_check(base_url: str, timeout: float = 3.0) -> bool:
    """调用前置健康检查（§13风险表：locate-ui无fallback，服务不可用要尽早明确失败）。"""
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(f"{base_url.rstrip('/')}/health")
            return r.status_code == 200
    except Exception:
        return False


def detect(model_cfg: dict, image_path: str) -> list[dict]:
    """返回原始检测结果（像素坐标），格式约定：
    [{"label": str, "box": [x1,y1,x2,y2]（像素）, "type": "element", "interactable": bool}, ...]
    实际字段名以你部署的 OmniParser 服务真实返回结构为准，这里假设一种常见约定，
    接入时按实际服务响应调整此处解析逻辑。
    """
    base = model_cfg["base_url"].rstrip("/")
    b64, media_type = encode_image(image_path)

    try:
        with httpx.Client(timeout=model_cfg.get("timeout") or 30) as client:
            r = client.post(f"{base}/parse", json={"image_base64": b64, "media_type": media_type})
            r.raise_for_status()
            data = r.json()
    except AdapterHTTPError:
        raise
    except Exception as e:
        raise classify_http_error(e) from e

    elements = data.get("elements", [])
    result = []
    for el in elements:
        result.append({
            "label": el.get("label") or el.get("text") or el.get("description") or "unlabeled",
            "box": el.get("bbox") or el.get("box"),
            "type": "element",
            "interactable": el.get("interactable", True),
        })
    return result
