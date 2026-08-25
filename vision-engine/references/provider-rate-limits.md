# 各 Provider 官方限流政策参考

> 本文档汇总 vision-engine 接入的各 provider 官方限流政策，作为 config 里 `quotas` 字段配值的**依据**。
>
> **重要说明**：
> - 部分数字来自 provider **官方文档公开列出的标准值**（标注「官方文档」）
> - 部分数字来自 **AI Studio / 控制台后台截图实测**（标注「截图实测」），因为 provider 官方文档不公开列具体 RPM/RPD，只在各自后台按项目显示
> - 各 provider 普遍声明"限流值非保证，实际容量可能变化"——本表是写文档时的快照，**以各 provider 后台实时显示为准**
>
> 本文档不自动加载，是配置维护时人工查阅用。config 的 `quotas` 字段含义见 `references/models.md`。

---

## 1. Google Gemini（generativelanguage.googleapis.com）

### 来源
- 官方文档：https://ai.google.dev/gemini-api/docs/rate-limits
- 定价页：https://ai.google.dev/pricing
- 后台实时值：https://aistudio.google.com/rate-limit

> ⚠️ 下表 alias 列为**旧快照**（`gemini`/`gemini-lite`/`gemini-lite-free-v1` 等已弃用）。
> 现行 alias 规范以 `references/model-recommendation-2026.md` §2.2 为准（如 `gemini-flash-lite`/`gemini-flash-lite-free-35`）。

### 官方文档明确的内容

| 项目 | 值 |
|---|---|
| 限流维度 | RPM（每分钟请求数）/ RPD（每天请求数）/ TPM（每分钟 token 数） |
| 免费层 | 有 RPM/TPM/RPD 限制，实验/preview 模型限制更严，无 spend-based 限流 |
| Tier 1 | spend 限流 $10/10分钟，billing 上限 $250 |
| Tier 2 | spend 限流 $200/10分钟，billing 上限 $2,000 |
| Tier 3 | spend 限流 $200/10分钟，billing 上限 $20,000–$100,000+ |
| Priority inference | 标准 RPM 的 0.3x |
| 官方声明 | "Specified rate limits are not guaranteed and actual capacity may vary" |

**官方文档未公开列具体 RPM/RPD/TPM 数值**——这些值只在 AI Studio 后台按项目显示。下表数值来自 AI Studio 后台**截图实测**（见 config `_note`）：

| 模型（alias） | 层级 | RPM | RPD | TPM | 来源 |
|---|---|---|---|---|---|
| gemini-3.1-pro-preview (gemini) | Tier 1 付费 | 25 | 250 | 2,000,000 | 截图实测 |
| gemini-3.6-flash (gemini-flash) | Tier 1 付费 | 1,000 | 10,000 | 2,000,000 | 截图实测 |
| gemini-3.5-flash-lite (gemini-lite) | Tier 1 付费 | 4,000 | 150,000 | 4,000,000 | 截图实测 |
| gemini-3-flash-preview (gemini-flash-v3) | Tier 1 付费 | 1,000 | 10,000 | 2,000,000 | 截图实测 |
| gemini-3.6-flash (gemini-flash-free) | 免费层 | 5 | 20 | 250,000 | 截图实测 |
| gemini-3.5-flash-lite (gemini-lite-free) | 免费层 | 15 | 500 | 250,000 | 截图实测 |
| gemini-3.1-flash-lite (gemini-lite-free-v1) | 免费层 | 15 | 500 | 250,000 | 截图实测 |

### Batch API（仅公开此表）
| 模型 | Tier 1 | Tier 2 | Tier 3 |
|---|---|---|---|
| Gemini 3.1 Pro Preview | 5,000,000 | 500,000,000 | 1,000,000,000 |
| Gemini 3.6 Flash | 3,000,000 | 400,000,000 | 1,000,000,000 |
| Gemini 3.5 Flash-Lite | 10,000,000 | 500,000,000 | 1,000,000,000 |
| Gemini 3.1 Flash Lite | 10,000,000 | 500,000,000 | 1,000,000,000 |

### 注意
- **免费层 RPD=20 极紧**：gemini-flash-free 一天只能调 20 次，作为兜底而非主力
- config 用两个 provider 区分付费 key（`google`，env `GOOGLE_API_KEY_TIRE1`）和免费 key（`google-free`，env `GOOGLE_API_KEY_FREE`），各自走同一组模型但限流不同

### `-latest` 浮动别名（2026-08-25 实测补充）

- Google 官方提供浮动别名：`gemini-flash-latest`、`gemini-flash-lite-latest`、`gemini-pro-latest`，热替换到该家族最新版（破坏性变更前 2 周邮件通知）
- **免费 key 下可用**（实测 API `/v1beta/models` 返回含以上全部别名；上下文 1M / 输出 64K / 完整多模态）
- `-latest` **无独立定价段**：继承当前指向那代的价格（如 flash-lite-latest 现在按 3.5-flash-lite 价 $0.30/$2.50）
- 免费层限流未对 `-latest` 单独截图实测，建议按当前代近似配（flash-lite 档 15/500/250k）
- 字面 `gemini-flash-lite`（无版本号）**不存在**——lite 家族只有版本化（3.5/3.1）与浮动（-latest）
- 完整推荐配置与配额说明见 `references/model-recommendation-2026.md`

---

## 2. Anthropic Claude（api.anthropic.com）

### 来源
- 官方文档：https://platform.claude.com/docs/en/api/rate-limits
- 模型页：https://platform.claude.com/docs/en/docs/about-claude/models

### 限流机制

| 项目 | 值 |
|---|---|
| 限流维度 | RPM / ITPM（输入 token/分钟）/ OTPM（输出 token/分钟），按 model class 分开计 |
| 算法 | token bucket（持续回补，非固定周期重置） |
| 层级 | Start / Build / Scale / Custom，按历史用量自动晋级 |
| 月度 spend cap | Start $500 / Build $1,000 / Scale $200,000 |
| 缓存优势 | **cached input tokens 不计入 ITPM**（Haiku 3.5 除外），prompt caching 可大幅提高有效吞吐 |
| OTPM | 实时计算实际产出 token，`max_tokens` 不计入 OTPM |

### 标准限流（官方文档公开值）

| 模型 | 层级 | RPM | ITPM | OTPM |
|---|---|---|---|---|
| Claude Fable 5 | Start | 1,000 | 500,000 | 100,000 |
| Claude Fable 5 | Build | 2,000 | 1,500,000 | 300,000 |
| Claude Fable 5 | Scale | 4,000 | 4,000,000 | 800,000 |
| Claude Opus 5 / Sonnet 5 / Haiku 4.5 | Start | 1,000 | 2,000,000 | 400,000 |
| Claude Opus 5 / Sonnet 5 / Haiku 4.5 | Build | 5,000 | 5,000,000 | 1,000,000 |
| Claude Opus 5 / Sonnet 5 / Haiku 4.5 | Scale | 10,000 | 10,000,000 | 2,000,000 |
| Claude Opus 4.x（4.5/4.6/4.7/4.8 共享） | Start | 1,000 | 2,000,000 | 400,000 |
| Claude Opus 4.x | Build | 5,000 | 5,000,000 | 1,000,000 |
| Claude Opus 4.x | Scale | 10,000 | 10,000,000 | 2,000,000 |

**注**：Opus 4.x 限流是 4.5/4.6/4.7/4.8 合并的总量；Opus 5、Sonnet 5 各自独立。新组织起步限流可能低于上表标准值。

### config 现状
vision-engine 当前 claude-sonnet-5 配 `requests:60/60s`，`_note` 标注"保守默认值，待实测"——**未按官方 Start 层 1000 RPM 配**，是保守兜底值。如需放开可改为官方值。

### 定价（官方文档，2026-06-24 快照）
| 模型 | 输入 $/MTok | 输出 $/MTok |
|---|---|---|
| Claude Fable 5 | $10 | $50 |
| Claude Opus 5 / 4.x | $5 | $25 |
| Claude Sonnet 5 | $3（intro $2 至 2026-08-31） | $15（intro $10） |
| Claude Haiku 4.5 | $1 | $5 |

---

## 3. OpenAI（api.openai.com）

### 来源
- 官方文档：https://developers.openai.com/api/docs/guides/rate-limits
- 模型页：https://developers.openai.com/api/docs/models

### 限流机制

| 项目 | 值 |
|---|---|
| 限流维度 | RPM / RPD / TPM / IPM（图片/分钟）/ TPD |
| 层级 | Free / Tier 1–5，按累计付费晋级 |
| Tier 资格 | Tier1 $5 / Tier2 $50 / Tier3 $100 / Tier4 $250 / Tier5 $1,000 |
| 月用量上限 | Free $100 / T1 $100 / T2 $500 / T3 $1,000 / T4 $5,000 / T5 $200,000 |
| 长上下文 | GPT-5.5 等长上下文模型有单独的长上下文限流 |
| 官方说明 | 具体每模型 RPM/TPM 在 [开发者后台](https://platform.openai.com/settings/organization/limits) 显示，文档不公开列 |

### 模型信息（官方模型页，2026 快照）
| 模型 | Model ID | 输入 $/MTok | 输出 $/MTok | 上下文 | 最大输出 | 视觉 |
|---|---|---|---|---|---|---|
| GPT-5.6 Sol | `gpt-5.6-sol`（alias `gpt-5.6`） | $5 | $30 | 1.05M | 128K | ✓ |
| GPT-5.6 Terra | `gpt-5.6-terra` | $2.50 | $15 | 1.05M | 128K | ✓ |
| GPT-5.6 Luna | `gpt-5.6-luna` | $1 | $6 | 1.05M | 128K | ✓ |

### config 现状
vision-engine 当前 gpt-5.6-terra 配 `requests:60/60s`，`_note` 标注"保守默认值，待实测"——同 Claude，是保守兜底。官方具体 RPM/TPM 需登录开发者后台查看。

---

## 4. 阿里云百炼 / 通义千问（compatible-mode）

### 来源
- 官方文档：https://help.aliyun.com/zh/model-studio/developer-reference/rate-limit
- 模型列表：https://help.aliyun.com/zh/model-studio/getting-started/models

### 限流机制

| 项目 | 值 |
|---|---|
| 限流维度 | RPM + TPM 双维度，**超出任一即触发** |
| 限流粒度 | 主账号级，RAM 子账号/业务空间/API Key 调用量**合并计算** |
| 秒级限制 | 按 RPS（RPM/60）与 TPS（TPM/60）限制，短时爆发即使未达分钟上限也可能触发 |
| 稳定版 vs 快照版 | 稳定版/最新版限流**比带日期的快照版宽松** |
| Batch API | 部分模型（如 qwen-max）Batch 调用不受限流 |
| 提额 | 控制台"限流提额"页申请，临时额度 30 天有效 |

### 主要模型限流（华北2北京，官方文档公开值）

| 模型 | RPM | TPM |
|---|---|---|
| qwen3.7-max | 30,000 | 5,000,000 |
| qwen3-max | 30,000 | 5,000,000 |
| qwen-max | 1,200 | 1,000,000 |
| qwen-plus | 30,000 | 5,000,000 |
| qwen-plus-latest | 15,000 | 1,200,000 |
| qwen3.6-flash | 30,000 | 10,000,000 |
| qwen-flash | 30,000 | 10,000,000 |
| qwen-turbo | 1,200 | 5,000,000 |

### 地域差异
新加坡/美国/德国/日本限流普遍**低于北京**，如 qwen-plus 新加坡 15,000 RPM / 5,000,000 TPM。

### config 现状
vision-engine 当前 qwen3-vl-plus / qwen3.7-plus 配 `requests:30/60s`，`_note` 标注"Aliyun 百炼默认"——**远低于官方 qwen-plus 的 30,000 RPM**。原因：百炼默认对新模型/视觉模型给的初始额度可能较低，且文档列的是 max 值。config 用保守 30 RPM 兜底，如确认额度充足可上调。

### 注意
官方文档**未明确列出 qwen3-vl-plus / qwen3.7-plus（视觉模型）的具体限流**，上表是文本模型。视觉模型限流建议登录百炼控制台查看实际值。

---

## 5. 火山引擎方舟 / 豆包（ark.cn-beijing.volces.com）

### 来源
- 官方文档库：https://docs.volcengine.com/docs/82379/
- 实测验证：2026-08-25 直接调 API（models 列表 + chat/completions）核实

### 关键实测结论（2026-08-25，比官方文档更可信）

1. **base_url 必须用 `/api/v3`**：`/api/coding/v3` 是 CodingPlan 订阅专用端点，本账号无订阅会报 `InvalidSubscription`（HTTP 400）。标准 Ark 端点 `https://ark.cn-beijing.volces.com/api/v3` 实测可用。
2. **未版本化 ID 不存在**：`doubao-seed-2-0-lite` 直接 404 `InvalidEndpointOrModel.NotFound`——必须用带日期的 pin 版（如 `doubao-seed-2-0-lite-260428`）。
3. **本账号仅开通 `doubao-seed-2-0-lite-260428`**：`doubao-seed-2-0-mini`、`doubao-seed-2-0-pro` 均报 404 `ModelNotOpen`（未开通）。
4. **lite 多模态/OCR 实测通过**：quick（颜色/文字识别）、ocr（提取 HELLO 2026）均正确，单图固定 1280 token。
5. **lite grounding 实测不达标**：`--verify-grounding` 两次结果 IoU=0.33 / 0.0（一次框不准、一次无框），**不打 grounding/ui-grounding 标签**。

### 官方限流信息
方舟**无全局固定 QPS**，按模型限流（同账号同模型共享，平台设定不可手动调整，提额需工单）。指标：TPM / TPD / RPM / Inflight Batchsize；突发返回 429，可用 `X-Ark-Max-Wait-Timeout-Ms`（最长 300000ms）缓解。官方文档未公开具体数值，下表为模型列表公开典型值：

| 模型 | 最大 RPM | 最大 TPM | 备注 |
|---|---|---|---|
| doubao-seed-2-0-lite / mini / pro | 30,000 | 5,000,000 | 全系默认高配额 |
| doubao-seed-1-8 | 30,000 | 5,000,000 | 上代主力 |
| doubao-seed-2-1-pro / turbo | 500（高配 30,000） | 1,000,000 → 5,000,000 | 默认较低 |
| doubao-seed-1-6-vision | 30,000（部分档 5k/15k） | 5,000,000（部分档 1.2M/1.5M） | 即将下线 |

### config 现状（2026-08-25 更新）
- `volcengine` provider：base_url=`/api/v3`（已从错误的 `/api/coding/v3` 修正）
- `doubao-seed-2-0-lite-260428`：`requests:100/60s`（保守），`_note` 已标注实测结论（未版本化不存在、仅此版开通、多模态/OCR 通过、grounding 不达标）
- `doubao-seed-2-0-mini` / `doubao-seed-2-0-pro`：保留条目，`_note` 标注「本账号未开通（ModelNotOpen），预留待开通」

---

## 总结：config 配值合理性核对

| provider | 模型 | config 配值 | 官方依据 | 评估 |
|---|---|---|---|---|
| google (Tier1) | 全部 | 按截图实测配齐 RPM/RPD/TPM | AI Studio 截图 | ✅ 已核实 |
| google-free | 全部 | 按截图实测配齐 | AI Studio 截图 | ✅ 已核实，注意 RPD=20 |
| anthropic | sonnet | 60 RPM（保守） | 官方 Start=1000 RPM | ⚠️ 远低于官方值，可上调 |
| openai | gpt-5.6-terra | 60 RPM（保守） | 官方未公开，需后台查 | ⚠️ 保守值，待核实 |
| alibailian | qwen 系列 | 30 RPM（保守） | 官方 qwen-plus=30000 RPM | ⚠️ 远低于官方值，视觉模型待核实 |
| volcengine | doubao-lite | 100 RPM（保守） | 官方 30K RPM（模型列表公开值）+ 实测 | ✅ 已核实端点/开通状态/grounding；额度保守 |

**结论**：Google 两层（付费+免费）已按截图实测配齐，是唯一完全核实的 provider。其余 provider 用保守默认值——稳妥策略，实际可用额度更大。**volcengine 已于 2026-08-25 完成 API 实测**（端点/开通状态/多模态/grounding），是三家中唯一完成端到端实测的；如需放开限流，anthropic 和 alibailian 可直接套官方值，openai 需登录后台核实。

---

## 信息更新时间

| 内容 | 核实时间 |
|---|---|
| Google Gemini 限流（截图实测值） | config `_note` 标注日期，详见各模型条目 |
| Google Gemini 官方文档（tier/spend/batch） | 2026-07-27 核实 |
| Anthropic 限流 + 定价 | 2026-07-27 核实 |
| OpenAI 模型信息 + 限流机制 | 2026-07-27 核实 |
| 阿里云百炼限流 | 2026-07-27 核实 |
| 火山引擎方舟 | 2026-07-27 核实（官方未公开具体数值）；2026-08-25 WAF 绕过 + 内容 API 补全价格/限流（见 model-recommendation-2026.md） |
| Google `-latest` 别名免费可用性 | 2026-08-25 实测（API models 列表） |
| 火山方舟 API 端到端实测（/api/v3 端点、未版本化不存在、仅 lite-260428 开通、多模态/OCR 通过、grounding 不达标） | 2026-08-25 实测 |

> provider 政策会变。重新核对时优先看官方文档链接，截图实测值需重新到后台截图比对。
