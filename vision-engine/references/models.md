# Model 路由与注册指南

> 这份文档**不是**分析图片时需要的，是配置维护类内容。
> SKILL.md 只在"分析图片"场景下加载，而"给 config 加新 model / 改路由逻辑"是配置维护任务，
> 需要主动打开本文件，不会自动加载。

## models 是资源清单，选择逻辑挂在 role 底下

`providers.<id>.models` 只是一份**资源清单**：列清楚有哪些 model、各自具备什么 `capabilities`，
不承载任何"该用谁"的决策逻辑。真正决定"这次调用选谁"的，是 role 自己的两层属性：

1. **`roles[].requires`**（自动，必经）：按 capability 筛出这个 role 能用的全部候选，不能跳过。
2. **`roles[].preferred_models`**（可选，role 自带的"快捷子集"）：从上一步筛出的候选里，指定一份优先
   顺序名单——**这就是"这个 role 该用哪几个"的地方**，直接写在 role 定义里：
   ```json
   "comprehensive": {
     "preferred_models": ["gemini-flash-free", "gemini-flash", "qwen3.7-plus"],
     ...
   }
   ```
   没列出的候选仍按 `priority` 排在后面兜底，不丢失 fallback 安全网。

`--model NAME`（CLI 级，一次性强制）是另一个维度：这次调用只试指定的 model，**不 fallback**——专用于
测试/验证某个 model 是否可用，不建议在生产调用里用（会丢失容灾能力）。指定的 model 若不满足该 role 的
capability 要求，直接报错(exit 1)，不会静默降级成别的 model。

## alias 与 provider/name 身份识别

**`name` 不是全局唯一标识，`provider/name` 才是**：同一 provider 下的 model name 不能重复（加载时校验，
重复直接拒绝），但跨 provider 允许同名——身份识别用的是完整的 `provider/name` 这个 ref（如
`anthropic/claude-sonnet-5`）。`--model`/`--verify-grounding` 优先按完整 ref 或 alias 匹配，
裸 name 在候选池里唯一时可以简写，跨 provider 重名时必须写全称或 alias，否则报"存在歧义"拒绝。

**alias**：给 model 起短名字，全局唯一，不能含 `/`：
```json
{"name": "claude-sonnet-5", "alias": "sonnet", ...}
```
`--model`、`--verify-grounding`、`preferred_models` 都认 alias，效果跟写完整 `provider/name` 一样。

## 没配 key 的 model 不会拖累调用

启动时预检每个候选 model 的 `api_key_env` 对应环境变量是否存在，没配 key 的直接从候选池剔除
（`-v` 模式可看到被剔除的名单），不会等到真正发请求才发现不可用，也不需要把 config 里全部 model
都配上 key 才能用。

## model 过时/被下线的处理

`deprecated: true` 把 model 标记为"要退役但还能兜底用"——无论 `priority` 多小，排序时一律排到
候选池最后，仍可被 fallback 用到，只是不会被优先选中。`-v` 模式下用到 deprecated model 会提醒
（可配 `deprecated_note` 说明迁移去向）。

定期用 `--self-test` 核实 model 是否被下线，不用等业务调用失败才发现。

## capabilities 注册表

只有 4 个 capability 会真正影响路由（被某个 role 的 `requires` 用到）：

| capability | 含义 | 判断依据 |
|---|---|---|
| `general` | 能看图并做基础描述/分析 | 能接收图片输入、给出有意义描述，就该打——门槛最低 |
| `ocr` | 文字提取**准确率**可靠 | 不是"能看图就行"，要求密集文本/小字号/表格识别准确率过关。没实测过先别打 |
| `grounding` | 经专门训练、像素级 bbox 坐标可信 | **门槛最高，最容易配错**。判断依据不是"model 说自己能给坐标"（通用模型问了也会给，但是编的），而是 provider 官方文档明确写了做过 grounding/detection 训练。**拿不准用 `--verify-grounding` 实测** |
| `ui-grounding` | 能枚举 UI 元素并给出坐标 | 支持 UI 元素枚举/检测的 VLM（如 Gemini Flash、Qwen-VL），或专用检测服务（如 OmniParser）。走 `locate-ui` role 的 `enumerate_then_filter` 模式 |

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

## `grounding` vs `ui-grounding` 怎么选

区分标准不是"擅不擅长 UI"，而是**交互接口和用途**：

| 场景 | 配什么 | 走哪个 role | 说明 |
|---|---|---|---|
| 给描述，返回对应元素坐标（targeted） | `grounding` | `locate` | "找到红色按钮" → 返回该元素的 bbox |
| 枚举全部可交互元素，本地过滤（enumerate） | `ui-grounding` | `locate-ui` | "页面上有哪些按钮" → 枚举全部，本地按关键词过滤 |

配错后果：targeted 接口的 model 硬配成 `ui-grounding`，`locate-ui` 的 `enumerate_then_filter` 逻辑
会拿到不符合预期的响应格式，本地过滤失效或报错。

## api_format 选择

只能是这四个值之一，对应 `scripts/adapters/` 下的四个 adapter：

| api_format | adapter | 适用场景 |
|---|---|---|
| `anthropic` | `anthropic_api.py` | Claude、MiniMax 等走 Anthropic 格式的服务 |
| `openai` | `openai_api.py` | GPT、Qwen 等走 OpenAI 兼容格式的服务；**自建服务（vLLM/Ollama/LM Studio）默认选这个** |
| `google` | `google_api.py` | Gemini 原生格式（grounding 基准） |
| `omniparser` | `omniparser_api.py` | OmniParser 本地 UI 检测服务（备用 adapter，当前未配置） |

如果你的 model 走完全不同的私有协议，需要新写 adapter 并在 `vision-analyze.py` 的
`ADAPTER_BY_FORMAT` 字典里注册——这是唯一必须碰代码的情况。

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
| `max_tokens` | role 级 `max_tokens` 覆盖，优先级：role > model > 默认 4096 |

长 prompt 建议外置成文件：inline 字符串编辑麻烦，超过几行就拆成 `config/prompts/<role>.md`，
config 里改成 `{"system_prompt": null, "system_prompt_file": "prompts/comprehensive.md"}`。

## 新 model 注册的完整流程

1. 确认 `api_format` 是否已有对应 adapter（四选一，或者自己写一个新的）
2. 先只打 `general`（如果它能看图），别急着打 `grounding`
3. 如果确实需要 grounding 能力，先跑 `--verify-grounding` 拿实测 IoU 再决定要不要打标签
4. 编辑 `vision-config.json` 加进对应 provider 的 `models` 数组（没有这个 provider 就新建一个），存盘即生效
5. 跑一次任意 role 确认没有触发死标签/不可满足/重名警告（stderr 里看）
