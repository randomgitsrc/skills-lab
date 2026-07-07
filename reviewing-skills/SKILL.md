---
name: reviewing-skills
description: "Use when evaluating, auditing, or reviewing an existing skill for quality, completeness, and effectiveness — before deployment or after major edits."
---

# Reviewing Skills

Systematic evaluation of agent skill quality. One pass, concrete findings, no hand-waving.

## When to Use / Not Use

**Use:** reviewing a skill before deployment, after major edits, or when skill seems ineffective in practice.

**Not use:** writing a new skill from scratch (→ writing-skills), debugging a specific skill failure (→ systematic-debugging), reviewing application code (→ requesting-code-review).

## Skill 类型与评审重点

不同类型的 skill，8 个维度的权重不同。先判断类型，再按权重评。

| 类型 | 特征 | 核心维度 | 次要维度 |
|------|------|----------|----------|
| **Discipline** | 强制规则（TDD、verification） | 可发现、健壮、精确 | 可执行、正确 |
| **Technique** | 具体方法（condition-based-waiting） | 可执行、正确、完整 | 可发现、渐进披露 |
| **Reference** | API 文档、命令参考 | 正确、完整、可执行 | 简洁、渐进披露 |

## 评审维度

每个维度独立打分 ✅/⚠️/❌，附具体行号和证据。

**核心维度**（所有类型都必须评）：

### 1. 可发现 (Discoverability) — 核心

Agent 能否在正确时机找到并加载这个 skill？

| 检查项 | 通过标准 |
|--------|----------|
| description 以 "Use when" 开头 | 触发条件明确，不含流程摘要 |
| description < 500 字符 | 超长则被截断或被当作快捷指令 |
| description 包含具体症状/场景 | 错误信息、工具名、技术关键词 |
| description 第三人称 | 不用 "I can" / "You can" |
| name 用 gerund 或 verb-first | `reviewing-skills` 不是 `skill-review` |
| name 只含字母数字连字符 | 无括号/特殊字符 |

**常见失败：** description 写了流程摘要 → agent 只读 description 不读 SKILL.md，行为走样。

### 2. 可执行 (Executability) — 核心

Agent 拿到 skill 后能否直接行动，不用猜？

| 检查项 | 通过标准 |
|--------|----------|
| 有完整最小可运行示例 | 不需要翻 template 就能跑（Reference skill 可豁免） |
| API 调用有完整签名 | 不只是函数名，含参数和返回值 |
| 代码片段可直接复制粘贴 | 无 `...` 省略、无 `TODO`、无占位符 |
| 运行命令明确 | 具体命令而非 "run the script" |
| 依赖已声明 | 包名、版本、安装方式 |

### 3. 正确 (Correctness) — 核心

信息是否经实测验证？

| 检查项 | 通过标准 |
|--------|----------|
| API 签名与实际库版本一致 | 无过时/虚构的参数 |
| 代码示例可运行 | 不是伪代码 |
| 超时值/常量有依据 | 不是拍脑袋的数字 |
| 环境特定信息标注了 | 如 "WSL2 only"、"Chrome 149+" |
| 无矛盾 | SKILL.md 与 reference/template 说法一致 |

**次要维度**（根据类型选择性深评）：

### 4. 精确 (Precision)

用/不用边界是否清晰？

| 检查项 | 通过标准 |
|--------|----------|
| "When to Use" 节存在 | 列出具体场景 |
| "Not use" 节存在 | 给出替代方案（→ other-skill / tool） |
| 无模糊触发词 | "helps with", "useful for" → 改为具体触发条件 |
| 用/不用无重叠 | 不会同时触发两个 skill |

### 5. 渐进披露 (Progressive Disclosure)

信息是否分层，各取所需？

| 检查项 | 通过标准 |
|--------|----------|
| SKILL.md < 500 行 | 超过则拆分到 reference/ |
| 核心规则在前，细节在后 | 铁律/Overview → Quick Ref → Troubleshooting |
| reference 文件只一层深 | SKILL.md → reference.md，不嵌套 |
| 低频操作折叠或降级 | `<details>` 或移到 reference |
| 无重复内容 | 同一信息只出现一次 |

**层级模型：**
- **必读**（每次加载都看）：铁律、Overview、最小脚本
- **常读**（大多数任务看）：Quick Reference
- **按需**（卡壳时才看）：Troubleshooting、reference 文件

### 6. 完整 (Completeness)

常见场景是否不卡壳？

| 检查项 | 通过标准 |
|--------|----------|
| 高频操作全覆盖 | 根据领域：表单/查询/导航等 |
| 常见失败模式有诊断 | 挂死/连不上/结果为空等 |
| 边界情况有提及 | 空输入/超大数据/环境差异 |
| 缺失信息有指引 | "如需 X 见 reference/Y.md" |

### 7. 简洁 (Conciseness)

够用但不过量？

| 检查项 | 通过标准 |
|--------|----------|
| 无 LLM 已知的基础知识解释 | 不解释 "什么是 PDF"、"什么是 HTTP" |
| 无叙事/故事 | "In session 2025-10-03 we found..." → 删 |
| 一条信息只出现一次 | 重复 = 浪费 token + 潜在矛盾 |
| 表格优于段落 | API 参考、超时值用表格 |
| 代码注释只解释 WHY | 不解释语法 |

### 8. 健壮 (Robustness)

是否预判了失败模式？

| 检查项 | 通过标准 |
|--------|----------|
| 铁律/硬规则有反例 | "永远不用 browser.close()" + 原因 |
| 有 Rationalization Table | 列出 agent 常见借口和反驳（Discipline skill 必须） |
| 有 Red Flags | 自检清单 |
| Troubleshooting 按现象分组 | agent 按症状查表 |
| 脆弱操作有 guard | 超时、重试、fallback |

## 评审流程

- [ ] 1. 判断 skill 类型（Discipline / Technique / Reference）
- [ ] 2. 读 SKILL.md 全文
- [ ] 3. 读所有 reference/ 和 template/ 文件
- [ ] 4. 核心维度（1-3）必评；次要维度（4-8）按类型权重表选择深评
- [ ] 5. 逐维度打分（✅/⚠️/❌ + 行号证据）
- [ ] 6. 汇总：核心问题（❌）→ 改善建议（⚠️）→ 可选优化
- [ ] 7. 给出优先级排序的修改清单

小 skill（单文件）~10min，大 skill（多文件）~30min。

## 输出格式

```markdown
## 评审结果

**Skill 类型：** Technique

### 逐维度

| 维度 | 评分 | 关键发现 |
|------|------|----------|
| 可发现 | ✅ | description 触发条件清晰 |
| 可执行 | ⚠️ | 缺最小可运行示例 (L27-44) |
| 正确 | ❌ | API 签名与 v1.52 不一致 (L68) |

### 核心问题（必须修）

| # | 问题 | 位置 | 建议 |
|---|------|------|------|
| 1 | API 签名过时 | L68 | 更新为 v1.52 签名 |

### 改善建议（建议修）

| # | 问题 | 位置 | 建议 |
|---|------|------|------|
| 1 | 缺最小示例 | L27-44 | 铁律下方加 10 行完整骨架 |

### 可选优化

- Troubleshooting 可按现象分组（当前按技术分类）
```

## 常见评审陷阱

| 陷阱 | 正确做法 |
|------|----------|
| "看起来不错" | 必须逐维度打分，附行号证据 |
| 只评结构不评内容 | 正确性需要验证 API 签名和代码可运行 |
| 跳过 reference/template | 重复和矛盾常藏在这些文件里 |
| 建议太模糊 | "改善超时节" → "Timeouts 节只留超时值表，Known Slow 移到 Troubleshooting" |
| 一次改太多 | 按优先级：❌ 必修 → ⚠️ 建议修 → 可选 |
| 用自己偏好代替标准 | 按维度检查项逐条过，不是按感觉评 |
| 假设作者意图 | 如果某节看不懂，标注为"不明确"而非脑补意图 |
| 只评新增内容 | 修改后必须重新验证整体一致性 |
