# 视觉模型调研方法与推荐配置（2026-08）

> 本文档记录 2026-08-25 对 Google Gemini / 阿里百炼 Qwen / 火山方舟豆包 视觉模型的调研过程（可复现），
> 以及据此产出的**推荐模型配置**（模型清单 + alias + 各 mode 模型链）与**限额/配额说明**。
>
> 本文档不自动加载，配置维护时人工查阅用。配套基础文档：
> - `references/models.md`——模型路由/alias/capabilities/quota 机制
> - `references/provider-rate-limits.md`——各 provider 官方限流政策
>
> 统一化口径（本报告所有金额/数据遵循）：
> - 计价单位：主单位 **USD / 百万 tokens**，附 CNY 原值
> - 统一汇率：**1 USD = 7.2 CNY**（固定参考汇率；open.er-api 实时约 6.74，仅参考）
> - 计价口径：在线推理常规价、基础档输入 [0,32]k；促销/长上下文分段单独标注
> - 类别范围：仅「图像/视频理解（分析）」，排除生成类

---

## 1. 调研方法与复现脚本

### 1.1 整体思路

1. **先读本地基线**：`config/vision-config.json`（已配模型/能力标签/配额）+ `references/provider-rate-limits.md`（限流快照），拿到已知模型名与限流值，避免重复劳动
2. **抓官方定价/模型文档页**：优先静态 HTML 可解析的页面（如 Google 定价页是服务端渲染，可直接逐表解析）
3. **遇到 JS 渲染站**：找页面内嵌的 SSR JSON（阿里百炼用 `window.__ICE_PAGE_PROPS__`）；火山走公开内容 API + 绕过 WAF
4. **权威验证用真实 API**：直接调 provider 的模型列表接口（带真实 key），确认模型存在性、免费层可用性、上下文、`-latest` 别名
5. **并行分工**：三家各派一个 subagent 深挖，统一输出格式（模型清单/定价/限流/排序），最后主 agent 合成
6. **统一化**：汇率、计价单位、口径、评价维度统一后再对比

### 1.2 Google（静态定价页 + API 模型列表）

定价页 `https://ai.google.dev/gemini-api/docs/pricing` 是**服务端渲染**，curl 直接可解析。按 `models-section` 分块，取每个模型的 `<code>` ID 与 Input/Output 价格行：

```python
import re, html as H
raw = open('g_pricing2.html', encoding='utf-8', errors='ignore').read()
for sec in re.split(r'<div class="models-section">', raw)[1:]:
    code = re.findall(r'<code[^>]*>(gemini-3[.\dA-Za-z-]+)</code>', sec)
    h2 = re.search(r'<h2[^>]*data-text="([^"]+)"', sec)
    name = H.unescape(h2.group(1)) if h2 else ''
    prices = {}
    for k in ['Input price', 'Output price']:
        mm = re.search(re.escape(k) + r'.*?<td>(.*?)</td>\s*<td>(.*?)</td>', sec, re.S)
        if mm:
            prices[k] = re.sub(r'\s+', ' ', H.unescape(re.sub(r'<[^>]+>', '', mm.group(2))).strip())
    if code and prices:
        print(f'{name} / {"/".join(code)}: {prices}')
```

**注意**：`-latest` 别名（`gemini-flash-latest` 等）**没有独立定价段**——继承当前指向那代的价格；促销价带 "through December 31, 2026" 标注。

模型清单权威验证——直接调 API（免费 key 也查得到全部）：

```bash
curl -sS "https://generativelanguage.googleapis.com/v1beta/models?key=$GOOGLE_API_KEY_FREE" | \
  python3 -c "import json,sys; print('\n'.join(sorted(m['name'].split('/')[-1] for m in json.load(sys.stdin)['models'])))"
```

关键结论（实测）：
- 免费 key 下可用：`gemini-flash-latest`、`gemini-flash-lite-latest`、`gemini-pro-latest`、`gemini-3.5-flash-lite`、`gemini-3.1-flash-lite` 等
- `-latest` 别名上下文 1M / 输出 64K，支持 generateContent/batch/cached（完整多模态）
- **字面 `gemini-flash-lite` 模型不存在**——lite 家族只有版本化（3.5/3.1）与浮动（`gemini-flash-lite-latest`）

### 1.3 阿里百炼（`__ICE_PAGE_PROPS__` SSR JSON）

百炼帮助中心三页（models / model-pricing / rate-limit）均为 **JS 渲染**，但 SSR 正文内嵌在 `window.__ICE_PAGE_PROPS__={...}` 的 `content` 字段，curl 抓取后按大括号配平解析 JSON 即可拿到完整价格/限流表（无头浏览器非必需）：

```python
import re, json
raw = open('ali.html', encoding='utf-8', errors='ignore').read()
m = re.search(r'window\.__ICE_PAGE_PROPS__\s*=\s*({.*?});?\s*</script>', raw, re.S)
data = json.loads(m.group(1))
content = data['content']   # 含模型价格/限流表正文
```

关键结论：`qwen3-vl-plus` / `qwen3.7-plus` 均在线且支持视觉；无版本号 ID（如 `qwen3-vl-plus`）**天然浮动**到最新快照，无需维护日期。

### 1.4 火山方舟（WAF + 公开内容 API）

方舟文档站 JS 渲染 + **WAF 挑战页**（SHA256 工作量证明），静态 curl 拿不到内容。解法：Python 实现其 WAF 算法 → 进程内立即带 cookie 重请求 → 调公开内容接口取结构化 Markdown：

```bash
# 概念示意（实现见调研会话脚本）
# 1. GET /api/doc/getDocDetail 需带 WAF cookie
# 2. 从模型列表页(doc 1330310)与价格页(doc 1544106)取 MDContent
```

关键结论：调研抓取到的是带日期快照 `doubao-seed-2-0-lite-260428`，确认在售且支持多模态理解（单图固定 1280 token，URL/Base64/文件路径均可，≤512MB）。
**⚠️ 实测修正（2026-08-25 API 验证）**：该 API **不存在未版本化 ID**（`doubao-seed-2-0-lite` 直接 404），必须用带日期的 pin 版（本账号仅开通 `doubao-seed-2-0-lite-260428`，pro/mini 均 ModelNotOpen）；base_url 必须用 `/api/v3`（`/api/coding/v3` 需 CodingPlan 订阅）。

### 1.5 调研产出物

| 产物 | 位置 |
|---|---|
| 三家原始调研报告 | `/tmp/gemini-vision-report-2026.md`、`/tmp/qwen_vision_report.md`、`/tmp/doubao_vision_report.md` |
| 统一对比报告 | 本仓库外草稿（`model-recommendation` 下方为定稿） |

---

## 2. 推荐模型配置（最终版）

### 2.1 设计原则

1. **浮动条目持有短 alias**：`gemini-flash-latest` → alias `gemini-flash`，`preferred_models` 写短名即自动漂移，版本迭代零维护；**免费浮动条目在短名基础上叠加 `-free` 后缀**（如 `gemini-flash-lite-latest` → `gemini-flash-lite-free`），故免费档 alias 略长，但语义统一
2. **pin 版带代际后缀**：`gemini-flash-36`、`gemini-flash-lite-free-35`——限流已知、行为稳定，作兜底
3. **免费层统一 `-free` 后缀**；免费层是同一批模型 ID + 免费 key（限流紧），不是独立模型
4. **bbox 类 mode（locate/locate-ui）只用 grounding 特训模型，严禁通用模型兜底**；未通过 `--verify-grounding`（IoU≥0.5）的模型一律不进池
5. **alias 去冗余去歧义**：alias ≠ name；短语义名（`qwen-vl`/`qwen-flash`/`doubao-lite`）
6. **alias 全局唯一**：同族不同代用代际后缀区分；alias 只在本 config 内有效，**与 provider 侧的真实模型 ID 同名属巧合，勿混**（如本配置 alias `qwen-flash` 与百炼稳定版模型 ID `qwen-flash` 是两回事）

### 2.2 模型清单（核心 20 个）+ alias + 能力标签

#### google（付费）
| name | alias | capabilities | 定位 |
|---|---|---|---|
| `gemini-3.1-pro-preview` | `gemini-pro` | general, ocr, **grounding** | grounding 主力（gemini_1000） |
| `gemini-flash-latest` | `gemini-flash` | general, ocr, ui-grounding | 浮动主 flash |
| `gemini-3.6-flash` | `gemini-flash-36` | general, ocr, ui-grounding | pin 兜底（促销价） |
| `gemini-flash-lite-latest` | `gemini-flash-lite` | general, ocr | 浮动 lite |
| `gemini-3.5-flash-lite` | `gemini-flash-lite-35` | general, ocr | pin lite |

#### google-free（免费，同 ID 不同 key）
| name | alias | capabilities | 定位 |
|---|---|---|---|
| `gemini-flash-latest` | `gemini-flash-free` | general, ocr, ui-grounding | 免费浮动质量档 |
| `gemini-flash-lite-latest` | `gemini-flash-lite-free` | general, ocr | **免费浮动 lite（quick 主力）** |
| `gemini-3.5-flash-lite` | `gemini-flash-lite-free-35` | general, ocr | 免费 pin（RPD 500 实测） |
| `gemini-3.1-flash-lite` | `gemini-flash-lite-free-31` | general, ocr | 免费 legacy |

#### alibailian（qwen，无版本 ID 天然浮动）
| name | alias | capabilities | 定位 |
|---|---|---|---|
| `qwen3-vl-plus` | `qwen-vl` | general, ocr, **grounding**, ui-grounding | VL 旗舰 |
| `qwen3-vl-235b-thinking` | `qwen-vl-235b` | general, ocr, grounding, ui-grounding | 最强 VL 推理 |
| `qwen3-vl-flash` | `qwen-vl-flash` | general, ocr, ui-grounding | 性价比王 |
| `qwen3.7-flash` | `qwen-flash` | general, ocr | 统一多模态最便宜 |
| `qwen3.7-plus` | `qwen-plus` | general, ocr | 1M 上下文，代码强 |
| `qwen3.8-max` | `qwen-max` | general, ocr | 最强旗舰 |

#### volcengine（doubao）
| name | alias | capabilities | 定位 |
|---|---|---|---|
| `doubao-seed-2-0-lite-260428`（唯一开通版） | `doubao-lite` | general, ocr | 主力（单图 1280 token）；**grounding 实测 IoU=0.33 不达标，不打标签** |
| `doubao-seed-2-0-mini` | `doubao-mini` | general, ocr | 性价比（本账号未开通，预留） |
| `doubao-seed-2-0-pro` | `doubao-pro` | general, ocr | 视觉定位（本账号未开通；开通后跑 verify-grounding 再定标签） |

#### 通用兜底
| name | alias | capabilities | 定位 |
|---|---|---|---|
| anthropic/`claude-sonnet-5` | `sonnet` | general, ocr | 通用兜底 |
| openai/`gpt-5.6-terra` | `gpt-5.6` | general | 通用兜底 |

> \* 豆包 grounding/ui-grounding：lite 已实测**不达标**（IoU 0.33 / 0.0 两次不稳定），pro/mini 本账号未开通——故三个模型均只标 general+ocr，**不打 grounding/ui-grounding 标签、不入 locate/locate-ui 池**。任何新模型要进池，仍须 `--verify-grounding`（IoU≥0.5）通过。
> 可选扩展：`gemini-pro-latest`（浮动旗舰）、`gemini-3.1-flash-lite`（付费最便宜 pin）、`qwen3.5-omni-plus`（全模态）、`doubao-seed-2-1-pro`（新一代旗舰）。
> **alias 规范更替**：本清单的 alias 规范**取代旧 config 中的旧 alias**（如旧 `gemini-lite` / `gemini-lite-free` → 新 `gemini-flash-lite` / `gemini-flash-lite-free`）。`provider-rate-limits.md` 中的 alias 列为旧快照，以本清单为准。

### 2.3 各 mode 的 preferred_models 链（主力 → 溢出 → 兜底）

| Mode | 模型链 | 逻辑 |
|---|---|---|
| **quick** | `gemini-flash-lite-free` → `gemini-flash-lite-free-35` → `qwen-flash` → `qwen-vl-flash` → `gemini-flash-lite` | 免费浮动打头（最新代、0 成本）→ 免费 pin 兜底（限流已知）→ 廉价付费溢出 |
| **comprehensive**（默认） | `gemini-flash` → `qwen-max` → `gemini-pro` → `qwen-vl` → `doubao-lite` | 质量平衡主力 → 中文最强 → 兜底 |
| **ocr** | `gemini-pro` → `gemini-flash` → `qwen-vl` → `qwen-vl-235b` → `doubao-lite` | Gemini 原生 OCR 最强 → 中文密集 qwen-vl |
| **code** | `gemini-flash` → `qwen-plus` → `qwen-max` → `gemini-pro` | 最新 flash 代码强 → qwen-plus 代码强 + 1M |
| **compare** | `gemini-pro` → `gemini-flash` → `qwen-max` → `qwen-vl` | 双图对比要最强视觉推理 + 一致性 |
| **locate**（bbox） | `gemini-pro` → `qwen-vl` → `qwen-vl-235b` | **仅 grounding 模型，无通用兜底**（doubao 不入池：lite 实测 IoU 0.33/0.0 不达标，pro 未开通） |
| **locate-ui**（枚举） | `qwen-vl` → `gemini-flash` → `qwen-vl-flash` | 仅 ui-grounding 模型（doubao-lite 实测 grounding 不达标，不入池） |
| **promptify** | `gemini-flash` → `gemini-pro` → `qwen-max` → `qwen-vl` | 细腻描述 + 长输出（8192） |

---

## 3. 限额与配额说明（config `quotas` 配值依据）

> 机制见 `references/models.md`。本地 quota 按「模型名」独立记账；`-latest` 浮动条目**也要单独配**。

### 3.1 Google Gemini

**免费层（google-free，同一批模型 + 免费 key）**

| 模型 | RPM | RPD | TPM | 备注 |
|---|---|---|---|---|
| gemini-3.5-flash-lite（free，`gemini-flash-lite-free-35`） | 15 | **500** | 250k | 免费主力（截图实测），quick 链中的 pin 兜底 |
| gemini-3.1-flash-lite（free，`gemini-flash-lite-free-31`） | 15 | **500** | 250k | 免费 legacy（截图实测） |
| gemini-3.6-flash（free，`gemini-flash-free-36`） | 5 | **20** | 250k | 极紧，仅兜底（截图实测） |
| gemini-flash-lite-latest（free，`gemini-flash-lite-free`） | 未实测（按 lite 免费档近似） | 500（近似） | 250k（近似） | **quick 默认打头**；`-latest` 免费限流未单独截图实测，建议按 3.5-flash-lite 免费档近似配 |
| gemini-flash-latest（free，`gemini-flash-free`） | 未实测（按 flash 免费档近似） | 20（近似） | 250k（近似） | 免费质量档，**非 quick 主力**（RPD 极紧） |

**付费层（google，Tier 1，截图实测）**

| 模型 | RPM | RPD | TPM |
|---|---|---|---|
| gemini-3.1-pro-preview | 25 | 250 | 2,000,000 |
| gemini-3.6-flash / 3-flash-preview | 1,000 | 10,000 | 2,000,000 |
| gemini-3.5-flash-lite | 4,000 | 150,000 | 4,000,000 |

> 官方文档**不公开具体 RPM/RPD**（仅 AI Studio 后台按项目显示），上表为快照，以后台实时为准。
> 促销价：3.6/3.7-flash $0.75/$3.75（至 2026-12-31，后恢复 $1.50/$7.50）；3.5-flash-lite $0.30/$2.50；3.1-flash-lite $0.25/$1.50；3.1-pro $2/$12（>200k 翻倍）。
> **注意区分**：`gemini-3.5-flash`（**非 lite**，$1.50/$9.00）官方在售，但**不纳入推荐**——其定位夹在 flash-lite（更便宜）与 3.6/3.7-flash（更强）之间，无独特价值；如需中等档可自行加入。

### 3.2 阿里百炼（RPM + TPM 双维度，超出任一即触发）

| 模型 | RPM | TPM | 备注 |
|---|---|---|---|
| qwen3-vl-plus / qwen3-vl-flash | 3,000 | 5,000,000 | Batch 不限流 |
| qwen3.7-flash / qwen3.7-plus | 30,000 | 5,000,000 | 快照版 600 / 1M |
| qwen3.8-max | 动态限流 | 动态限流 | 按消费档位，不可自助提额 |
| qwen3-vl-235b-* | 60 | 100,000 | |
| qwen3-vl-32b / 30b / 8b | 600 | 1,000,000 | |
| qwen3.5-omni-* | 60 | 100,000 | |

> 价格（基础档）：qwen3-vl-plus 1/10 元、qwen3-vl-flash 0.15/1.5 元、qwen3.7-flash 0.2/0.8 元、qwen3.7-plus 2/8 元、qwen3.8-max 12/36 元（USD = ÷7.2）。

### 3.3 火山方舟（无全局 QPS，按模型限流，同账号同模型共享）

| 模型 | 最大 RPM | 最大 TPM | 备注 |
|---|---|---|---|
| doubao-seed-2-0-lite / mini / pro | 30,000 | 5,000,000 | 全系默认高配额 |
| doubao-seed-1-8 | 30,000 | 5,000,000 | 上代主力 |
| doubao-seed-2-1-pro / turbo | 500（高配 30,000） | 1,000,000 → 5,000,000 | 默认较低 |
| doubao-seed-1-6-vision | 30,000（部分档 5k/15k） | 5,000,000（部分档 1.2M/1.5M） | 即将下线 |

> 价格（基础档 [0,32]k，CNY/M，USD = CNY ÷ 7.2）：lite 0.6/3.6 元（≈$0.08/$0.50）、mini 0.2/2.0 元（≈$0.03/$0.28）、pro 3.2/16 元（≈$0.44/$2.22）、2-1-pro 6/30 元（≈$0.83/$4.17）。图片按像素折 token（seed-2.0 单图固定 1280 token），无单独按图计价。

> **⚠️ 实测附注（2026-08-25，直接调 API）**：
> - base_url 必须用 `/api/v3`（`/api/coding/v3` 需 CodingPlan 订阅，本账号无订阅 → 400 InvalidSubscription）
> - 未版本化 ID（`doubao-seed-2-0-lite`）**不存在**（404），必须用日期 pin 版 `-260428`
> - 本账号**仅开通 lite-260428**；mini / pro 均为 ModelNotOpen（未开通），开通后才能用
> - lite 多模态/OCR 实测通过；grounding 实测不达标（IoU 0.33 / 0.0），不入 locate/locate-ui 池
> - 详见 `provider-rate-limits.md` §5

### 3.4 本地 quota 配置建议

```json
// google-free（示例：免费浮动 lite，按 flash-lite 档近似）
{"name": "gemini-flash-lite-latest", "quotas": [
  {"metric": "requests", "window_seconds": 60,  "limit": 15},
  {"metric": "requests", "window_seconds": 86400, "limit": 500},
  {"metric": "tokens",   "window_seconds": 60,  "limit": 250000}]}

// google（示例：pro，截图实测）
{"name": "gemini-3.1-pro-preview", "quotas": [
  {"metric": "requests", "window_seconds": 60,  "limit": 25},
  {"metric": "requests", "window_seconds": 86400, "limit": 250},
  {"metric": "tokens",   "window_seconds": 60,  "limit": 2000000}]}
```

要点：
1. `-latest` 浮动条目单独配 quota，且额度按「当前代」近似——漂移后如连续 429，回到对应 pin 版
2. 免费层限流**极紧**（flash RPD 20），免费档只在 quick/兜底等低量场景打头
3. quick 免费打头用 `gemini-flash-lite-free`（= `gemini-flash-lite-latest` 免费浮动，限流按 lite 免费档近似 15/500/250k）→ 若连续 429 或行为异常，立即落到 `gemini-flash-lite-free-35`（= 3.5-flash-lite 免费，RPD 500 截图实测）
4. 429 冷却只在 `quota_exceeded` 时触发；`--self-test` / `--verify-grounding` 不受限流

---

## 信息更新时间

| 内容 | 时间 |
|---|---|
| Google 定价页逐表核对 + API 模型列表验证（含 -latest 免费可用性） | 2026-08-25 |
| 百炼 SSR JSON 解析（模型/价格/限流） | 2026-08-25 |
| 火山 WAF 绕过 + 内容 API（模型/价格/限流） | 2026-08-25 |
| 汇率参考（open.er-api，仅参考） | 2026-08-25 |
| 独立评审通过（含 P1/P2/P3 修复） | 2026-08-25 |
| 火山方舟端到端实测（端点/开通状态/多模态/OCR/grounding） | 2026-08-25 |
