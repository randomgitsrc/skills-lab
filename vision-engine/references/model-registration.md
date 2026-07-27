# Model 注册指南

> 这份文档**不是**分析图片时需要的，是配置维护类内容。
> SKILL.md 只在"分析图片"场景下加载，而"给 config 加新 model"是配置维护任务，
> 需要主动打开本文件，不会自动加载。
>
> 路由逻辑、alias/provider/name 身份识别、deprecated 处理 → 见 `references/model-routing.md`。

## 谁来注册一个新model？

现状：**完全靠人工**。没有自动发现机制。注册一个 model 就是编辑 `config/vision-config.json`，
找到对应 provider 的 `models` 数组加一段 JSON，存盘即生效。

## 一个新model该配哪些字段？

**config 按 provider 分组**，同一 provider 下的 model 共享 `base_url`/`api_format`/`api_key_env`：

```json
"providers": {
  "你的provider名（自定义，如openai/google/alibaba/self-hosted）": {
    "base_url": "API端点",
    "api_format": "anthropic | openai | google | omniparser（决定走哪个adapter）",
    "api_key_env": "环境变量名，没有key就填null",
    "models": [
      {
        "name": "model名字，同一provider下不能重复；不同provider下允许同名（身份识别用完整的provider/name）",
        "alias": "可选，全局唯一的短名字，不能含'/'",
        "capabilities": ["..."],
        "coordinate_convention": "仅grounding/ui-grounding类model需要",
        "priority": 1,
        "timeout": 60,
        "max_tokens": 4096,
        "quotas": [
          {"metric": "requests", "window_seconds": 60,    "limit": 25,  "_note": "Tier1 RPM 截图实测"},
          {"metric": "requests", "window_seconds": 86400, "limit": 250, "_note": "Tier1 RPD 截图实测"},
          {"metric": "tokens",   "window_seconds": 60,    "limit": 2000000, "_note": "Tier1 TPM 截图实测"}
        ],
        "cooldown_seconds": 可选，收到429后冷却时长，默认60秒
      }
    ]
  }
}
```

model 条目里也可以覆盖同 provider 的共享字段（比如某个 model 走不同的 `base_url`），
字段同名时 model 条目优先——但正常情况下不需要这么做。

## api_format 选择

只能是这四个值之一，对应 `scripts/adapters/` 下的四个 adapter：

| api_format | adapter | 适用场景 |
|---|---|---|
| `anthropic` | `anthropic_api.py` | Claude、MiniMax 等走 Anthropic 格式的服务 |
| `openai` | `openai_api.py` | GPT、Qwen 等走 OpenAI 兼容格式的服务；**自建服务（vLLM/Ollama/LM Studio）默认选这个** |
| `google` | `google_api.py` | Gemini 原生格式（grounding 基准） |
| `omniparser` | `omniparser_api.py` | OmniParser 本地 UI 检测服务 |

如果你的 model 走完全不同的私有协议，需要新写 adapter 并在 `vision-analyze.py` 的
`ADAPTER_BY_FORMAT` 字典里注册——这是唯一必须碰代码的情况。

## capabilities 注册表

只有 4 个 capability 会真正影响路由（被某个 role 的 `requires` 用到）：

| capability | 含义 | 判断依据 |
|---|---|---|
| `general` | 能看图并做基础描述/分析 | 能接收图片输入、给出有意义描述，就该打——门槛最低 |
| `ocr` | 文字提取**准确率**可靠 | 不是"能看图就行"，要求密集文本/小字号/表格识别准确率过关。没实测过先别打 |
| `grounding` | 经专门训练、像素级 bbox 坐标可信 | **门槛最高，最容易配错**。判断依据不是"model 说自己能给坐标"（通用模型问了也会给，但是编的），而是 provider 官方文档明确写了做过 grounding/detection 训练。**拿不准用 `--verify-grounding` 实测** |
| `ui-grounding` | 专门的 UI 元素检测服务 | 只有 OmniParser 这类专用检测服务该打，通用视觉模型不该打 |

**不影响路由的标签**（除非在 `roles` 里新增一个 `requires` 它的 role，否则打了也是摆设）：
`data`、`style`、`spatial-relative`。

加了新 model 或改了 capabilities，CLI 下次启动会自动做两项静态校验（不阻断，只警告，见 stderr）：
- 死标签：某 model 声明了某 capability，但没有 role 要求它
- 不可满足：某 role 要求了某 capability，但没有 model 声明它

校验能发现"标签有没有被用到"，发现不了"标签打得准不准"——用 `--verify-grounding` 实测。

## `grounding` 标签该不该打——别只信文档，用实测

```bash
scripts/vision-analyze.py --verify-grounding MODEL_NAME --config config/vision-config.json
```

用内置探测图（`scripts/fixtures/grounding-probe.png`，1000x1000 白底图，中央有一个位置已知的红色矩形）
直接调用指定 model，问它"找出红色矩形"，把返回的 bbox 和 ground truth 计算 IoU（交并比）。
IoU≥0.5 判定"有实测证据支持打 grounding 标签"。

**能力边界**：单张探测图表现好不代表真实复杂场景也一样准，正式启用前建议再用真实场景图人工抽查。
IoU 阈值 0.5 是经验值，精度要求高的场景自己核对 `best_iou` 具体数值。

## UI 检测类 model 该配 `grounding` 还是 `ui-grounding`

区分标准不是"擅不擅长 UI"，而是**交互接口是 targeted 还是 enumerate**：

- **targeted**（给描述，返回对应元素坐标）→ 配 `grounding`，走 `locate` role。UI-TARS 属于这类。
- **enumerate**（不接受查询，一次性吐出全部元素）→ 配 `ui-grounding`，走 `locate-ui` role。OmniParser 属于这类。

配错后果：targeted 接口的 model 硬配成 `ui-grounding`，`locate-ui` 的 `enumerate_then_filter` 逻辑
会拿到不符合预期的响应格式，本地过滤失效或报错。

## 通用 quota 框架（`quotas` 字段）

每个 model 通过 `quotas` 数组配置任意 (metric, window, limit) 组合，不在代码里 hardcode 三种固定类型。
本地限流（不依赖服务端响应），目的是在多并发/频繁调用场景下不浪费请求额度。
数据存在 `~/.local/share/vision-engine/ratelimit/{metric}-{window}s-{model_name}.json`，flock 保护。

### quotas 条目结构

| 字段 | 含义 | 示例 |
|------|------|------|
| `metric` | `"requests"`（请求次数）或 `"tokens"`（token 数） | `"requests"` |
| `window_seconds` | 滑动窗口（秒），任意正整数 | `60` / `86400` / `3600` |
| `limit` | 窗口内上限 | `25` / `2000000` |
| `_note` | 自由文本，写实测依据（截图日期/provider文档链接） | `"Tier1 RPM 截图实测"` |

### 数据 schema

```
requests metric → {"timestamps": [ts1, ts2, ...]}
tokens metric   → {"entries": [[ts, n], [ts, n], ...]}
```

- `check_quota` 是 dry-run（不写文件），检查累计值是否 ≥ limit
- `record_usage` 在响应成功后调用，精确记账
- 失败调用（429/timeout/5xx）不调 record_usage，不计入

### 示例配置

```json
"quotas": [
  {"metric": "requests", "window_seconds": 60,    "limit": 25},
  {"metric": "requests", "window_seconds": 86400, "limit": 250},
  {"metric": "tokens",   "window_seconds": 60,    "limit": 2000000}
]
```

### Token 准确统计

每个 adapter 从 provider 响应中提取 `usage` 字段，归一化为 `{"input_tokens", "output_tokens", "total_tokens"}`：

| api_format | 数据来源 | 字段名 |
|-----------|---------|--------|
| `google` | `usageMetadata` | `promptTokenCount` / `candidatesTokenCount` / `totalTokenCount` |
| `openai` | `usage` | `prompt_tokens` / `completion_tokens` / `total_tokens` |
| `anthropic` | `usage` | `input_tokens` / `output_tokens`（自行求和 total） |
| `omniparser` | 无 | 无 token 概念，返回 None |

- 响应成功 → 日志记 `tokens_in` / `tokens_out` / `usage_source: "api"`
- 响应缺 usage 字段 → 用 `max_tokens` 保守估计，日志记 `usage_source: None`
- Token 计数在 `record_usage` 中按 `tokens` metric 的 quota 窗口累加

### 429 cooldown

**429 冷却仅在 `kind == "quota_exceeded"` 时触发**——其他错误（auth_error / server_error / timeout）
不会设冷却。`cooldown_seconds` 字段可以配置，默认 60 秒。

**`--self-test` 和 `--verify-grounding` 不受限流**——这两个是诊断命令。

### 实测依据

每个 model 的 `quotas` 条目都应该来自实际测量（Google AI Studio rate limit 页面、并发请求测出实际 429 阈值等），
截图保存到项目目录，在 `_note` 字段里写"YYYY-MM-DD 实测"以便日后审计。

## role 字段完整说明

| 字段 | 含义 |
|---|---|
| `system_prompt` | 直接写在 config 里的短 prompt (inline) |
| `system_prompt_file` | 指向 `.md` 文件的相对路径（相对 config.json 所在目录），加载时读入 `system_prompt`，**不能跟 `system_prompt` 同时填** |
| `requires` | 该 role 需要 model 具备的 `capabilities`，驱动动态候选池筛选 |
| `output_schema` | `text` 或 `bbox_list`，决定走文本流程还是 bbox 容错提取+校验流程 |
| `default` | 标记默认 role（不指定 `-r` 时用它） |
| `multi_image` | 是否需要两张图（`compare` 用，要求必须传 `-i2`） |
| `query_mode` | `targeted`（给描述找元素）或 `enumerate_then_filter`（枚举全部再本地过滤），决定 `-p` 的语义 |
| `skip_context` | 是否忽略 `-c` 参数（`quick` 用） |
| `preferred_models` | 该 role 的定向 model 偏好（软性排序，可写 `provider/name` 或 `alias`） |

长 prompt 建议外置成文件：inline 字符串编辑麻烦，超过几行就拆成 `config/prompts/<role>.md`，
config 里改成 `{"system_prompt": null, "system_prompt_file": "prompts/comprehensive.md"}`。

## 小结：新 model 注册的完整流程

1. 确认 `api_format` 是否已有对应 adapter（四选一，或者自己写一个新的）
2. 先只打 `general`（如果它能看图），别急着打 `grounding`
3. 如果确实需要 grounding 能力，先跑 `--verify-grounding` 拿实测 IoU 再决定要不要打标签
4. 编辑 `vision-config.json` 加进对应 provider 的 `models` 数组（没有这个 provider 就新建一个），存盘即生效
5. 跑一次任意 role 确认没有触发死标签/不可满足/重名警告（stderr 里看）
