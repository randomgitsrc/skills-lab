---
name: vision-engine
description: "Use whenever an agent that cannot natively see images needs to analyze, describe, OCR, compare, or locate elements/coordinates in a screenshot, design mockup, photo, diagram, chart, UI, 3D scene, map, or any visual content. Also use for bounding-box/coordinate lookups ('find X and give its position', 'locate the button', 'where is this element')  — those need the locate/locate-ui roles, not just description. Not bound to any single vision provider; routes across multiple multimodal models with automatic fallback."
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
| 不确定选哪个 | `comprehensive`，**禁止靠猜测强行拆分成窄role** |

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
| UI交互坐标（"点哪里"） | **仅UI-grounding专用引擎**，枚举后本地过滤 | OmniParser，`locate-ui` role |
| 3D世界坐标/地理坐标 | 仅画面有可读坐标轴/网格/标注文字时才给，否则不给 | 任意模型，标注来源"画面标注" |

**核心原则：没有专门训练过grounding的模型（Claude/GPT-4o/MiniMax等通用对话模型），一律不承担
"给出数值坐标"的职责，只负责相对位置描述。它们给出的具体坐标数值是编的，不是真的对像素做了定位。**

## CLI Reference

```
scripts/vision-analyze.py -i IMAGE [-i2 IMAGE2] [-r ROLE] [-p PROMPT] [-c CONTEXT]
                           [-f FORMAT] [--no-cache] [--config PATH] [-v]

-i, --image     图片路径 (png/jpg/gif/webp) [必需]
-i2, --image2   第二张图片路径（compare role时必需）
-r, --role      quick / comprehensive(默认) / ocr / code / compare / locate / locate-ui
-p, --prompt    提示词/查询描述：
                  targeted role(locate)：追加进system_prompt作为查询目标
                  enumerate_then_filter role(locate-ui)：仅作本地过滤关键词，不传给模型
                  quick role：即发给模型的唯一内容
-c, --context   dispatch-context：内联JSON字符串或文件路径，quick role忽略
-f, --format    json(默认) / text / yaml
--model NAME    强制指定单个model（provider/name完整ref、alias，或候选池内唯一时可用裸name），
                 跳过priority排序与fallback，专用于测试/验证单个model，
                 不适用于locate-ui（该role候选池本就只有单个ui-grounding model）
--verify-grounding MODEL_NAME
                 用内置探测图实测某model的grounding准确度(IoU)，配置维护用途，
                 详见 references/model-registration.md，指定后忽略-i/-r等参数
--self-test     遍历config里配了key的全部model，各发一次最小化探测请求，汇总存活/失效报告，
                 忽略-i/-r等参数。ui-grounding类model走健康检查而非chat探测
--no-cache      跳过缓存

退出码:
  0 成功
  1 参数错误（含文件不存在、task_goal超120字符硬上限、--model指定的model不满足capability要求）
  2 全部模型调用失败
  3 无可用模型（key缺失 / capability白名单校验失败 / omniparser健康检查失败）
  4 RPM限流（候选模型均超限）
  5 bbox_list结果校验失败（全部条目非法，locate/locate-ui专用）
```

## 关于"用哪个model" —— models是资源清单，选择逻辑挂在role底下

`providers.<id>.models`只是一份**资源清单**：列清楚有哪些model、各自具备什么`capabilities`，不承载
任何"该用谁"的决策逻辑。真正决定"这次调用选谁"的，是下面这两层，都是**role自己的属性**，不是外挂的：

1. **`roles[].requires`**（自动，必经）：按capability筛出这个role能用的全部候选，这一层不能跳过。
2. **`roles[].preferred_models`**（可选，role自带的"快捷子集"）：从上一步筛出的候选里，指定一份优先
   顺序名单——**这就是"这个role该用哪几个"的地方**，直接写在role定义里，不需要额外的CLI参数去触发：
   ```json
   "comprehensive": {
     "preferred_models": ["sonnet", "gpt"],
     ...
   }
   ```
   没列出的候选仍按`priority`排在后面兜底，不丢失fallback安全网。

`--model NAME`（CLI级，一次性强制）是另一个维度：这次调用只试指定的model，**不fallback**——专用于
测试/验证某个model是否可用，不建议在生产调用里用（会丢失容灾能力）。指定的model若不满足该role的
capability要求，直接报错(exit 1)，不会静默降级成别的model。

**alias：不想每次都写全`provider/name`**，可以给model起个短名字：
```json
{"name": "claude-sonnet-5", "alias": "sonnet", ...}
```
`--model`、`--verify-grounding`、`preferred_models`这几处都认alias，效果跟写完整`provider/name`一样。
alias是全局扁平命名空间（不像`name`按provider分区），必须整个config里唯一，且不能包含`/`（会跟
`provider/name`格式混淆），加载时都会校验。

**没配key的model不会拖累调用**：启动时会预检每个候选model的`api_key_env`对应环境变量是否存在，
没配key的直接从候选池剔除（`-v`模式可看到被剔除的名单），不会等到真正发请求才发现不可用，
也不需要每次都把config里全部model都配上key才能用。

**model过时/被下线的处理**：`providers[provider_id].models[].deprecated: true`把某model标记为"要退役但还能兜底用"——
无论`priority`数字多小，排序时一律排到候选池最后，依然可被fallback用到，只是不会被优先选中；
`-v`模式下用到deprecated model会提醒一句（可配`deprecated_note`说明迁移去向）。定期核实model是否
已经被下线，用`--self-test`遍历config里配了key的全部model各发一次最小化探测请求，汇总存活/失效报告，
不用等真正业务调用失败才发现某个model早就不能用了。

**`name`不是全局唯一标识，`provider/name`才是**：同一provider下的model name不能重复（加载时校验，
重复直接拒绝），但跨provider允许同名——身份识别用的是完整的`provider/name`这个ref（如
`anthropic/claude-sonnet-5`），这是多provider路由器的通行做法（OpenClaw等项目也是这样设计的）。
`--model`/`--verify-grounding`优先按完整ref或alias匹配，如果某个裸name在候选池里只对应一个model也
可以简写，但存在跨provider重名时必须写全称或alias，否则会报"存在歧义"拒绝而不是随便选一个。系统本身
无状态，不存在"当前锁定用哪个model"的持久化概念，每次调用独立重新计算候选池，`priority`数字本身就是
fallback尝试顺序，不是另有一套机制。

## Capabilities 与新model注册

**这部分内容不是分析图片时需要的**，是配置维护类内容。如果当前任务是"给vision-config.json加一个
新model"、"判断该给某个model打什么capability标签"、"改role的system_prompt"（长prompt可以用
`system_prompt_file`外置成`config/prompts/*.md`，不用挤在一行json字符串里编辑），这些都不是本skill的
分析场景，去读`references/model-registration.md`（里面有完整的capabilities判断标准、
`--verify-grounding`实测工具用法、role字段完整说明）。本skill正常分析图片时不需要关心这些。

## Fallback 行为说明



- `text` 类role（comprehensive/ocr/code/compare/quick）：按`priority`逐个尝试候选模型，
  某模型失败/超时/429/限流都会自动尝试下一个，直到全部候选耗尽才报错。
- `bbox_list` 类role（locate）：同样有多模型fallback，但**不做通用模型兜底**——candidates为空时
  不会退化到用Claude/GPT-4o之类的通用模型（它们给的坐标不可信）。
- bbox结果采用**部分接受**：一次返回里如果部分条目坐标越界或字段缺失，只丢弃非法条目，
  不会因为个别条目有问题就整体判定失败换模型，`attempts`里会记录`dropped_count`。
- `locate-ui`目前**没有fallback**（仅`omniparser-local`一个候选），这是接受的架构限制，
  不是bug。调用前会先做健康检查，服务不可用时立即返回exit 3并提示，而不是等真正调用超时才报错。

## Red Flags — Rationalization Table

| 想法 | 现实 |
|---|---|
| "这张图看起来主要是UI，用ocr role就够了" | 混合内容用`comprehensive`，别靠猜分类，模型会自己分层输出实际存在的层 |
| "模型给了具体坐标，看起来挺准" | 非grounding模型（Claude/GPT-4o等）的坐标是编的，只信`locate`/`locate-ui`的输出 |
| "locate-ui返回空数组，就是没有这个元素" | 先检查`total_elements_detected`——可能是本地关键词匹配没匹配上，而不是真的不存在；
  匹配不到时CLI会返回`filter_matched: false`+`note`字段说明,并把画面全部检测元素原样返回,不会静默给空数组 |
| "随口问一句也要先组织context" | `quick` role不需要`-c`，一次性提问直接问 |
| "CLI script对简单场景来说有点重" | `quick` role就是为此设计的轻量路径，`system_prompt`为空、`-f text`直接返回原始文本，
  没有分层框架的开销 |

## Troubleshooting

| 问题 | 排查 |
|---|---|
| exit 3, no_available_model | 检查对应`api_key_env`环境变量是否设置，或`~/.env`/项目`.env`是否包含该key |
| exit 3, omniparser_unavailable | 确认`omniparser-local`服务已启动，`base_url`在config里配置正确 |
| exit 5, bbox全部非法 | 换用`comprehensive`看模型原始输出是否偏离了坐标格式约定，必要时检查`coordinate_convention`配置 |
| .env权限/gitignore警告 | 按提示`chmod 600 .env`，并在`.gitignore`里加一行`.env` |
| 结果里`dropped_count`较大 | 说明grounding模型本次返回中有较多条目坐标越界/字段缺失，被部分丢弃，属正常容错行为 |

## 目录结构

```
vision-engine/
├── SKILL.md
├── references/
│   └── model-registration.md     — 配置维护类文档:capabilities判断标准、新model注册流程、
│                                     --verify-grounding用法(不在分析任务中自动加载,需主动读)
├── config/
│   ├── vision-config.json        — 模型列表(按provider分组)、role定义、capability白名单、坐标约定
│   └── prompts/                  — 长system_prompt外置文件(comprehensive.md/locate.md)，
│                                     role里用system_prompt_file指向这里，避免inline长字符串难编辑
└── scripts/
    ├── vision-analyze.py         — CLI主入口
    ├── bbox_utils.py             — bbox容错提取/坐标转换/部分校验/IoU计算
    ├── ratelimit.py              — flock RPM限流
    ├── cache.py                  — 缓存(hash+role)
    ├── logger.py                 — 审计日志(白名单字段+轮转)
    ├── context.py                — dispatch-context解析/校验/降权拼装
    ├── env_security.py           — API key安全读取/预检/权限检查
    ├── fixtures/                 — --verify-grounding的探测图/ground truth、--self-test的最小化探测图
    └── adapters/
        ├── common.py             — 图片编码/媒体类型探测/HTTP错误分类
        ├── anthropic_api.py      — Claude/MiniMax等anthropic格式
        ├── openai_api.py         — GPT-5.6/Qwen等openai兼容格式(也是接入自建OpenAI兼容服务的默认选择)
        ├── google_api.py         — Gemini原生格式(grounding基准)
        └── omniparser_api.py     — UI元素检测本地服务
```

## Integration with Other Skills

- 任何产出图片文件的skill（如截图工具）→ 用vision-engine分析结果。
- 需要"点击某UI元素"的自动化流程 → 先用`locate-ui`拿到坐标，再交给对应的浏览器/桌面自动化工具执行点击。
