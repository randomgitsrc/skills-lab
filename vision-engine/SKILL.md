---
name: vision-engine
description: "Use when any agent needs to see or understand an image — screenshots, photos, diagrams, charts, UI mockups, maps, 3D scenes. Handles describe, OCR, compare, coordinate/element location, UI element enumeration, and reverse-engineering image generation prompts. The only vision skill; all image analysis must route here."
---

# vision-engine

统一视觉分析引擎：CLI + 多模型能力路由 + 分层识别 + 坐标/grounding + JSON结构化I/O。
替代原 `vision-analyzer`（已废弃，不做兼容层）。

## When to Use / Not Use

**Use：** 分析截图、验证UI渲染、读图表/图示、描述照片、对比视觉设计、从图片检查页面元素、
找出图中某元素/特效的精确坐标或边界框，任何需要视觉理解的任务。

**Not use：** 纯文本分析（→ Read/Grep）、非图片文件（PDF/视频）、不需要视觉理解的任务。

**主agent禁止直接用 Read 工具读图片**——主模型通常不支持图片输入，会报错或返回garbage。
必须通过本skill的CLI调用。

## Role 选择决策表

| 触发条件 | role |
|---|---|
| 一次性提问，不需结构化拆解，结果不会被下游脚本解析或复用 | `quick`（优先判断这条） |
| 未明确诉求，或看起来需要仔细拆解 | `comprehensive`（默认） |
| 明确要求仅提取文字/OCR | `ocr` |
| 明确说明是代码截图 | `code` |
| 明确要求对比两张图差异 | `compare` |
| 明确要求某元素/特效的精确边界框或坐标 | `locate` |
| 明确要求UI元素的交互坐标（"点哪里"） | `locate-ui` |
| 需要为图片生成Midjourney/DALL-E提示词，用于复刻相似图。写轮眼式复刻——看图→拆解→输出精准prompt | `promptify` |
| 不确定选哪个 | `comprehensive`，**禁止靠猜测强行拆分成窄role** |

## Quick Start

```bash
# 路径取决于安装位置，通常为 ~/.claude/skills/vision-engine/scripts/...
# 以下用相对路径简写

# 最简调用 — quick role，直接返回文本
python3 scripts/vision-analyze.py -i /path/to/image.png -r quick -p "描述这张图" -f text

# 结构化分析 — comprehensive role（默认），JSON 输出
python3 scripts/vision-analyze.py -i /path/to/image.png -p "分析UI布局"

# OCR 提取文字
python3 scripts/vision-analyze.py -i /path/to/image.png -r ocr -f text

# 对比两张图
python3 scripts/vision-analyze.py -i before.png -i2 after.png -r compare -f text

# 定位元素坐标（需 grounding 模型）
python3 scripts/vision-analyze.py -i /path/to/image.png -r locate -p "红色按钮"

# 枚举UI元素坐标
python3 scripts/vision-analyze.py -i /path/to/image.png -r locate-ui -p "登录按钮" -f json

# 生成生图提示词（自动选择MJ/DALL-E/SD格式）
python3 scripts/vision-analyze.py -i /path/to/image.png -r promptify -f text
```

## dispatch-context 使用规范

跨调用需要传递上下文时用 `-c`，但**不是把对话历史转发过去**——需要主agent提炼：

```json
{"task_goal": "一句话说明目的（硬上限120字符，Unicode码点数）",
 "domain_hint": "ui-web|ui-desktop|3d-viewport|map-gis|game-screenshot|design-mockup|unknown",
 "prior_context": [{"summary": "上一步发现了什么", "from_role": "comprehensive"}],
 "constraints": ["用户的真实限制条件"]}
```

- `task_goal` 超过120字符（`len(string)`，非UTF-8字节数，中英文同一把尺子）直接报错拒绝（exit 1），
  不静默截断也不照单全收。目标复杂就把细节放进 `constraints`/`prior_context`，不要硬塞进`task_goal`。
- `domain_hint` 不确定就填 `unknown`，不强猜——它只是"仅供参考"的提示，vision model会被告知
  "此提示可能不准确，请以图片实际内容为准"，不会被当作事实采信。
- **`quick` role 不需要提供 `-c`，提供了也会被忽略**——一次性提问不该先组织一遍context才能问。

## 坐标系统规范（关键：什么时候能信坐标，什么时候不能）

| 坐标类型 | 是否可信任具体数值 | 承担模型 |
|---|---|---|
| 相对/描述性位置（"左上角""居中偏右"） | 可以，通用模型强项 | 任意通用模型 |
| 2D像素/归一化bbox | **仅grounding模型**，坐标经adapter按模型约定转换 | Gemini/Qwen-VL，`locate` role |
| UI交互坐标（"点哪里"） | **仅UI-grounding专用引擎**，枚举后本地过滤 | LLM模型（Gemini Flash / Qwen等），`locate-ui` role |
| 3D世界坐标/地理坐标 | 仅画面有可读坐标轴/网格/标注文字时才给，否则不给 | 任意模型，标注来源"画面标注" |

**核心原则：没有专门训练过grounding的模型（Claude/GPT-4o/MiniMax等通用对话模型），一律不承担
"给出数值坐标"的职责，只负责相对位置描述。它们给出的具体坐标数值是编的，不是真的对像素做了定位。**

## CLI Reference

```
scripts/vision-analyze.py -i IMAGE [-i2 IMAGE2] [-r ROLE] [-p PROMPT] [-c CONTEXT]
                           [-f FORMAT] [--model MODEL] [--no-cache] [--config PATH] [-v]

-i, --image     图片路径 (png/jpg/gif/webp) [必需]
-i2, --image2   第二张图片路径（compare role时必需）
-r, --role      quick / comprehensive(默认) / ocr / code / compare / locate / locate-ui / promptify
-p, --prompt    提示词/查询描述：
                  targeted role(locate)：追加进system_prompt作为查询目标
                  enumerate_then_filter role(locate-ui)：仅作本地过滤关键词，不传给模型
                  quick role：即发给模型的唯一内容
-c, --context   dispatch-context：内联JSON字符串或文件路径，quick role忽略
-f, --format    json(默认) / text / yaml
--model NAME    强制指定单个model（provider/name完整ref、alias，或候选池内唯一时可用裸name），
                 跳过priority排序与fallback，专用于测试/验证单个model
--verify-grounding MODEL_NAME
                 用内置探测图实测某model的grounding准确度(IoU)，配置维护用途，
                 详见 references/models.md，指定后忽略-i/-r等参数
--self-test     遍历config里配了key的全部model，各发一次最小化探测请求，汇总存活/失效报告，
                 忽略-i/-r等参数。ui-grounding类model走健康检查而非chat探测
--clear-quotas  清除本地quota限流数据（~/.local/share/vision-engine/ratelimit/），
                 不发任何网络请求，下次调用自动重建
--no-cache      跳过缓存

退出码:
  0 成功
  1 参数错误（含文件不存在、task_goal超120字符硬上限、--model指定的model不满足capability要求）
  2 全部模型调用失败
  3 无可用模型（key缺失 / capability白名单校验失败）
  4 限流（候选模型均quota超限或429冷却中）
  5 bbox_list结果校验失败（全部条目非法，locate/locate-ui专用）

输出中（JSON格式）成功时会包含 usage 对象：{"input_tokens", "output_tokens", "total_tokens"}，
日志同步记录 tokens_in / tokens_out / usage_source。quota 的 tokens metric 基于此精确计数。
```

## Fallback 行为说明

- `text` 类role（comprehensive/ocr/code/compare/quick）：按`priority`逐个尝试候选模型，
  某模型失败/超时/429/本地quota超限都会自动尝试下一个，直到全部候选耗尽才报错。
  quota 检查顺序：cooldown → 逐条 `quotas` 规则（requests/tokens × 任意窗口）。
  quota 超限状态格式为 `quota_exceeded:{metric}:{window}s`（如 `quota_exceeded:requests:60s`）。
- `bbox_list` 类role（locate）：同样有多模型fallback，但**不做通用模型兜底**——candidates为空时
  不会退化到用Claude/GPT-4o之类的通用模型（它们给的坐标不可信）。
- bbox结果采用**部分接受**：一次返回里如果部分条目坐标越界或字段缺失，只丢弃非法条目，
  不会因为个别条目有问题就整体判定失败换模型，`attempts`里会记录`dropped_count`。
- `locate-ui`已支持多模型fallback（逐个尝试ui-grounding候选：Google Flash、Qwen等），
  不再局限于单个模型。

## Red Flags — Rationalization Table

| 想法 | 现实 |
|---|---|
| "这张图看起来主要是UI，用ocr role就够了" | 混合内容用`comprehensive`，别靠猜分类，模型会自己分层输出实际存在的层 |
| "模型给了具体坐标，看起来挺准" | 非grounding模型（Claude/GPT-4o等）的坐标是编的，只信`locate`/`locate-ui`的输出 |
| "locate-ui返回空数组，就是没有这个元素" | 先检查`total_elements_detected`——可能是本地关键词匹配没匹配上，而不是真的不存在；
  匹配不到时CLI会返回`filter_matched: false`+`note`字段说明,并把画面全部检测元素原样返回,不会静默给空数组 |
| "随口问一句也要先组织context" | `quick` role不需要`-c`，一次性提问直接问 |
| "promptify没必要，用quick也能生成prompt" | `promptify`有自检、格式自动适配（UI截图→DALL-E，艺术图→MJ，动漫→SD）和8192 token输出空间，结果比裸quick精准得多 |
| "CLI script对简单场景来说有点重" | `quick` role就是为此设计的轻量路径，`system_prompt`为空、`-f text`直接返回原始文本，
  没有分层框架的开销 |

## Troubleshooting

| 问题 | 排查 |
|---|---|
| exit 2, all_models_failed | 所有候选模型调用均失败，用 `-v` 查看每个模型的错误详情；常见原因：API key 无效/余额不足/模型已下线 |
| exit 3, no_available_model | 检查对应`api_key_env`环境变量是否设置，或`~/.env`/项目`.env`是否包含该key |
| exit 4, 限流 | 所有候选均因本地quota超限（requests/tokens × 任意窗口）或429冷却被跳过。用 `-v` 看每个候选的status（`cooldown` 或 `quota_exceeded:{metric}:{window}s`）。可改config的 `quotas` 字段调整限制，或清 `~/.local/share/vision-engine/ratelimit/` 重置计数 |
| exit 5, bbox全部非法 | 换用`comprehensive`看模型原始输出是否偏离了坐标格式约定，必要时检查`coordinate_convention`配置 |
| .env权限/gitignore警告 | 按提示`chmod 600 .env`，并在`.gitignore`里加一行`.env` |
| 结果里`dropped_count`较大 | 说明grounding模型本次返回中有较多条目坐标越界/字段缺失，被部分丢弃，属正常容错行为 |
| 限流数据残留 | 使用 `--clear-quotas` 一键清除本地限流计数（仅在调试验证时使用，正常无需操作） |

## 目录结构

```
vision-engine/
├── SKILL.md
├── references/
│   ├── models.md                  — model 路由/alias 身份识别、capabilities 判断、quota 框架、新 model 注册流程
│   └── provider-rate-limits.md    — 各 provider 官方限流政策（Google/Anthropic/OpenAI/百炼/火山），config quotas 配值依据
├── config/
│   ├── vision-config.json        — 模型列表(按provider分组)、role定义、capability白名单、坐标约定
│   └── prompts/                  — 各role的system_prompt外置文件(7个.md)，
│                                     role里用system_prompt_file引用，config中无inline长字符串
├── scripts/
│   ├── vision-analyze.py         — CLI主入口
│   ├── vision-stats.py           — 调用统计+限流矫正工具（summary/quota/set/sync/clean/reset）
    ├── bbox_utils.py             — bbox容错提取/坐标转换/部分校验/IoU计算
    ├── ratelimit.py              — 通用quota限流(requests/tokens × 任意窗口) + 429冷却
    ├── cache.py                  — 缓存(hash+role)
    ├── logger.py                 — 审计日志(白名单字段+轮转)，记录tokens_in/tokens_out/usage_source
    ├── context.py                — dispatch-context解析/校验/降权拼装
    ├── env_security.py           — API key安全读取/预检/权限检查
    ├── tests/                    — pytest 测试(ratelimit/usage/logger/integration)
    ├── fixtures/                 — --verify-grounding的探测图/ground truth、--self-test的最小化探测图
    └── adapters/
        ├── common.py             — 图片编码/媒体类型探测/HTTP错误分类
        ├── usage.py              — 从provider响应提取token usage(多格式归一化)
        ├── anthropic_api.py      — Claude/MiniMax等anthropic格式
        ├── openai_api.py         — GPT-5.6/Qwen等openai兼容格式(也是接入自建OpenAI兼容服务的默认选择)
        ├── google_api.py         — Gemini原生格式(grounding基准)
        └── omniparser_api.py     — UI元素检测本地服务（备用adapter，当前未配置）
```

## Integration with Other Skills

- 任何产出图片文件的skill（如截图工具）→ 用vision-engine分析结果。
- 需要"点击某UI元素"的自动化流程 → 先用`locate-ui`拿到坐标，再交给对应的浏览器/桌面自动化工具执行点击。

## 配置维护

修改 model 路由、注册新 model、调整 capabilities → 见 `references/models.md`。

## 调用统计与限流矫正（vision-stats.py）

本地 ratelimit 计数可能跟服务端不同步（其他客户端调用、quota 重置、残留数据）。
`vision-stats.py` 是独立 CLI，用于统计和矫正。

```bash
# 路径同 vision-analyze.py，在 scripts/ 下

# 查看调用统计（从审计日志）
python3 scripts/vision-stats.py summary

# 查看当前各模型各窗口 quota 使用率
python3 scripts/vision-stats.py quota

# 手动矫正：Google AI Studio 显示 RPD 已用 15/20，同步到本地
python3 scripts/vision-stats.py set google-free/gemini-3.6-flash --used 15 --metric requests --window 86400

# 自动矫正：从 Anthropic/OpenAI 响应头读取真实余量并覆写本地
python3 scripts/vision-stats.py sync

# 清理已删模型的残留限流数据
python3 scripts/vision-stats.py clean --yes

# 重置某模型全部限流数据（清零，下次调用自动重建）
python3 scripts/vision-stats.py reset google-free/gemini-3.6-flash --yes
```

### Provider 矫正能力

| provider | 自动(sync) | 手动(set) | 说明 |
|---|---|---|---|
| Anthropic | ✅ | ✅ | 响应头含 `anthropic-ratelimit-*-remaining` |
| OpenAI | ✅ | ✅ | 响应头含 `x-ratelimit-remaining-*` |
| Google | ❌ | ✅ | **无 rate limit header**，只能从 AI Studio 后台看后手动 set |
| 百炼/火山 | ❌ | ✅ | 未公开 header，只能从控制台看后手动 set |
