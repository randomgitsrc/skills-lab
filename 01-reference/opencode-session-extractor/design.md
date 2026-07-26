# OpenCode Session Extractor — 设计文档

> 从 OpenCode SQLite 数据库提取会话记录，产出结构化 Markdown 文档

## 1. 问题

OpenCode 会话 compact 后，LLM 上下文被截断，但**原始消息完整保留在 SQLite 数据库**中。需要一种方式从 DB 提取指定范围的会话记录，产出可读的 .md 文件，用于：

- 回顾任务执行过程（T048 开始到现在的所有交互）
- 提取特定会话的完整记录（给 session ID）
- 按时间/主题/项目筛选会话
- 为 retrospective / postmortem 提供原始素材

## 2. 数据源

### 2.1 数据库位置

```
~/.local/share/opencode/opencode.db   # 1.3GB, SQLite WAL
```

**只读访问**——opencode 运行时 WAL 模式，只读查询安全，绝不写入。

### 2.2 核心表结构

```
session (154条)
├── id              TEXT PRIMARY KEY    # ses_xxx
├── parent_id       TEXT                # 子会话指向主会话
├── title           TEXT                # 会话标题
├── path            TEXT                # 工作目录（相对 home）
├── agent           TEXT                # agent 类型：general/vision-helper/build/explore/frontend/backend/orchestrator
├── model           TEXT (JSON)         # {"id":"xopglm51","providerID":"xfmass","variant":"default"}
├── cost            REAL
├── tokens_input    INTEGER
├── tokens_output   INTEGER
├── time_created    INTEGER (ms)        # Unix 毫秒时间戳
├── time_updated    INTEGER (ms)
└── project_id      TEXT                # 关联 project 表

message (6643条)
├── id              TEXT PRIMARY KEY    # msg_xxx
├── session_id      TEXT FK→session.id
├── time_created    INTEGER (ms)
└── data            TEXT (JSON)
    ├── role        "user" | "assistant"
    ├── agent       "build" | "orchestrator" | "general" | "compaction" | ...
    ├── mode        "compaction" | "build" | ...
    ├── summary     true | false        # compaction 消息标记
    ├── parentID    TEXT                 # 前一条消息 ID
    ├── model       JSON                 # 使用的模型
    ├── cost        REAL
    └── tokens      JSON

part (26457条)
├── id              TEXT PRIMARY KEY
├── message_id      TEXT FK→message.id
├── session_id      TEXT FK→session.id
├── time_created    INTEGER (ms)
└── data            TEXT (JSON)          # 消息体，按 type 分：
    │
    ├── type="text"        (3395条)  LLM 输出 / 用户输入文本
    │   └── text           STRING
    │
    ├── type="tool"        (7116条)  工具调用
    │   ├── tool           "bash"|"read"|"edit"|"write"|"grep"|"glob"|"task"|"skill"|...
    │   ├── callID         STRING
    │   └── state
    │       ├── status     "completed" | "error"
    │       ├── input      JSON（工具参数）
    │       ├── output     STRING（工具输出，可能很长）
    │       ├── metadata   JSON（diff/truncated/exit code 等）
    │       └── title      STRING（工具调用标题）
    │
    ├── type="reasoning"   (2876条)  推理过程
    │   └── text           STRING
    │
    ├── type="compaction"  (19条)   compact 元数据
    │   ├── auto           BOOL     是否自动触发
    │   ├── overflow       BOOL     是否因溢出触发
    │   └── tail_start_id  STRING   截断后保留的第一条消息 ID
    │
    ├── type="patch"       (1129条) 文件变更记录
    │   ├── hash           STRING   git hash
    │   └── files          ARRAY    变更文件列表
    │
    ├── type="agent"       (6条)    subagent 声明
    │   └── name           STRING   "vision-helper" 等
    │
    ├── type="file"        (5条)    文件附件
    │
    ├── type="step-start"  (6056条) 步骤开始（snapshot hash）
    │
    └── type="step-finish" (6028条) 步骤结束（tokens/cost/finish reason）
```

### 2.3 会话层级

```
主会话 (parent_id = NULL 或空)
  │
  ├── 子会话 A (parent_id = 主会话.id)  ← Task 工具派发的 subagent
  │     agent: "general" / "frontend" / "vision-helper" / ...
  │
  ├── 子会话 B (parent_id = 主会话.id)
  │     agent: "explore"
  │
  └── ...
```

141/154 个 session 是子会话，13 个是主会话。子会话通过 `parent_id` 关联回主会话。

### 2.4 Compact 机制

1. OpenCode 检测上下文窗口接近上限，触发 `compaction` agent
2. compaction agent 读取当前会话所有消息，生成摘要
3. 摘要作为 `role=assistant, agent=compaction, summary=true` 的消息插入同一会话
4. compaction part 记录 `tail_start_id`——截断后 LLM 上下文从该消息开始
5. **原始消息不删除**——DB 中完整保留
6. 后续 LLM 调用只发送 `tail_start_id` 之后的消息 + 摘要

### 2.5 数据量统计

| part type | 数量 | 平均大小 | 最大大小 |
|-----------|------|---------|---------|
| tool      | 7116 | 10KB    | 3.1MB   |
| text      | 3395 | 376B    | 38KB    |
| reasoning | 2876 | 842B    | 25KB    |
| patch     | 1129 | 204B    | 2.7KB   |
| step-start| 6056 | 64B     | 75B     |
| step-finish|6028 | 197B    | 221B    |
| compaction| 19   | 87B     | 99B     |

**tool 子类型分布**：

| tool | 数量 | 平均大小 | 最大大小 |
|------|------|---------|---------|
| bash | 2814 | 2.9KB   | 97KB    |
| read | 1884 | 27KB    | 3.1MB   |
| edit | 891  | 3.5KB   | 39KB    |
| write| 341  | 6KB     | 51KB    |
| webfetch|341 | 10.8KB  | 54KB    |
| grep | 283  | 2KB     | 101KB   |
| task | 141  | 3.8KB   | 41KB    |
| glob | 135  | 611B    | 8.8KB   |
| todowrite|156 | 2.1KB   | 5KB     |
| skill| 41   | 9.6KB   | 25KB    |

**关键发现**：`read` 工具平均 27KB、最大 3.1MB，是体积最大的数据源。`bash` 输出也可能很长。**必须截断**。

## 3. 查询模式

### 3.1 按任务号（T048 → 现在）

```
输入: "T048"
解析: 
  1. 从 git log 或 docs/tasks/ 反查 T048 起始时间
  2. SELECT * FROM session WHERE time_created >= T048起始时间
  3. 按 parent_id 构建会话树
```

### 3.2 按会话 ID

```
输入: "ses_0be69a44cffetYLj0MROI9jYfy"
解析:
  1. SELECT * FROM session WHERE id = 输入ID
  2. 同时获取所有 parent_id 指向该会话的子会话
  3. 提取完整消息链
```

### 3.3 按时间范围

```
输入: "2026-07-06" 或 "2026-07-06~2026-07-08"
解析:
  1. 转换为 Unix 毫秒时间戳
  2. SELECT * FROM session WHERE time_created >= start AND time_created <= end
```

### 3.4 按主题/关键词

```
输入: "header redesign" 或 "light mode"
解析:
  1. SELECT * FROM session WHERE title LIKE '%header redesign%'
  2. 或搜索 message/part 中的文本内容（较慢，需 LIKE 或 FTS）
```

### 3.5 按项目/工作目录

```
输入: "peekview" 或 path="home/kity/oclab/peekview"
解析:
  1. SELECT * FROM session WHERE path LIKE '%peekview%'
```

### 3.6 组合查询

以上模式可组合：T048 + peekview 项目 + 时间范围。

## 4. 内容提取策略

### 4.1 保留/丢弃规则

| part type | 策略 | 理由 |
|-----------|------|------|
| `text` | ✅ 完整保留 | 核心内容：用户输入、LLM 回复 |
| `reasoning` | ✅ 保留 | 推理过程，理解决策依据 |
| `tool` | ⚠️ 压缩保留 | 见 4.2 |
| `compaction` | ✅ 保留元数据 | tail_start_id 用于定位截断点 |
| `patch` | ✅ 保留 | 文件变更记录（hash + 文件列表） |
| `agent` | ✅ 保留 | subagent 类型声明 |
| `step-start` | ❌ 丢弃 | 纯元数据（snapshot hash） |
| `step-finish`| ❌ 丢弃 | 纯元数据（tokens/cost），统计信息汇总到会话头 |
| `file` | ⚠️ 保留文件名 | 附件引用，不保留内容 |

### 4.2 Tool 压缩策略

| tool | 保留内容 | 截断规则 |
|------|---------|---------|
| **bash** | 命令 + 退出码 + 输出摘要 | 输出 >500 字符截断，保留首尾各 200 字符 |
| **read** | 文件路径 + 行范围 | **不保留文件内容**（平均 27KB，无意义） |
| **edit** | 文件路径 + oldString/newString 摘要 | oldString/newString >200 字符截断 |
| **write** | 文件路径 | **不保留文件内容**（平均 6KB） |
| **grep** | 查询模式 + 结果文件数 + 前几条 | 结果 >10 条只保留前 5 + "…N more" |
| **glob** | 查询模式 + 结果文件数 + 前几条 | 结果 >20 条只保留前 10 + "…N more" |
| **task** | subagent_type + description + 输出摘要 | 输出 >500 字符截断 |
| **todowrite** | 完整保留 | 平均 2KB，可接受 |
| **skill** | skill 名称 + 摘要 | >500 字符截断 |
| **webfetch** | URL + 内容摘要 | >500 字符截断 |
| **websearch** | 查询 + 结果摘要 | >500 字符截断 |

### 4.3 Compact 摘要处理

**混合策略**：

1. 找到会话中所有 compaction 消息
2. compaction 消息的 text part 是 LLM 生成的摘要（Goal/Progress/Decisions/Next Steps 格式）
3. **compact 前的消息**：用摘要替代，标注 `[Compact Summary]`
4. **compact 后的消息**：保留原文（按 4.1/4.2 规则压缩）
5. 如果用户指定 `--full`，则忽略 compact 摘要，提取所有原始消息

### 4.4 敏感信息过滤

自动脱敏规则：
- `pv_[a-zA-Z0-9]+` → `pv_***`（API key）
- `Authorization: Bearer ...` → `Authorization: Bearer ***`
- `~/.bash_env` 内容 → 不保留
- `~/.npmrc` 内容 → 不保留
- 环境变量值含 `key`/`token`/`secret`/`password` → `***`

## 5. 产出格式

### 5.1 文件命名

```
session-log-<范围标识>.md
```

示例：
- `session-log-T048-T052.md`
- `session-log-ses_0be69a44.md`
- `session-log-20260706-20260711.md`

### 5.2 文档结构

```markdown
# Session Log: T048–T052 (2026-07-06 → 2026-07-11)

> Generated: 2026-07-11 21:30 | DB: ~/.local/share/opencode/opencode.db
> Sessions: 25 | Messages: 2441 | Compactions: 13 | Cost: $X.XX

---

## 📊 Overview

| Metric | Value |
|--------|-------|
| Time Range | 2026-07-06 14:30 → 2026-07-11 21:10 |
| Main Sessions | 2 |
| Subagent Sessions | 23 |
| Total Messages | 2441 |
| Total Cost | $X.XX |
| Models Used | xfmass/xopglm51, minimax-cn/MiniMax-M3, opencode/deepseek-v4-flash-free |

### Session Tree

```
ses_0be69a44 "PeekView 开发四组" (主会话, 2441 msgs)
├── ses_0b440685 "P3 TDD 测试设计" (@general, 40 msgs)
├── ses_0b438e9d "P4 实现 header 重设计" (@general, 60 msgs)
├── ses_0b41276a "Vision: mobile sheet" (@vision-helper, 3 msgs)
├── ses_0b4127b2 "Vision: desktop header" (@vision-helper, 3 msgs)
└── ...
```

---

## 🏠 Main Session: ses_0be69a44 "PeekView 开发四组"

- **Model**: xfmass/xopglm51
- **Duration**: 2026-07-06 14:30 → 2026-07-11 21:10
- **Messages**: 2441 | **Compactions**: 13 | **Cost**: $X.XX

### [Compact #1] 2026-07-06 15:30

> **Summary** (replaces 89 messages before this point)
>
> ## Goal
> - Update and verify AGENTS.md for the PeekView repository...
>
> ## Progress
> - Verified all architecture table entries...
> - ...

---

### 2026-07-06 15:35 — 👤 User

再+ 你对架构设计掌握非常清晰...

### 2026-07-06 15:36 — 🤖 Assistant (build)

收到。已加载决策框架，准备就绪...

### 2026-07-06 15:37 — 🔧 bash

```bash
$ make debug-start
```
> Starting debug server on :8888... (output truncated, 47 chars omitted)

### 2026-07-06 15:38 — 🔧 edit: `frontend-v3/src/views/EntryDetailView.vue`

- oldString: `rgba(18,24,34,0.88)` (30 chars)
- newString: `var(--c-glass-bg)` (16 chars)

### 2026-07-06 15:39 — 🤖 Assistant (build)

修改完成。`--c-glass-bg` 变量在 dark theme 下...

---

## 🔀 Subagent: P3 TDD 测试设计 (@general)

- **Session**: ses_0b440685
- **Duration**: 2026-07-07 10:00 → 2026-07-07 11:30
- **Messages**: 40 | **Cost**: $X.XX

### 2026-07-07 10:00 — 🤖 Assistant (general)

根据 P2 方案，设计 TDD 测试用例...

### 2026-07-07 10:05 — 🔧 read: `frontend-v3/src/views/EntryDetailView.vue`

(文件内容已省略，path: /home/kity/oclab/peekview/frontend-v3/src/views/EntryDetailView.vue)

### 2026-07-07 10:10 — 🔧 write: `frontend-v3/__tests__/t052-header-redesign.test.ts`

(文件内容已省略)

### 2026-07-07 10:15 — 🤖 Assistant (general)

22 个测试用例设计完成，覆盖...

---

## 📊 Session Statistics

| Session | Agent | Model | Messages | Cost | Input Tokens | Output Tokens |
|---------|-------|-------|----------|------|-------------|---------------|
| ses_0be69a44 | build/orchestrator | xopglm51 | 2441 | $X.XX | XXX | XXX |
| ses_0b440685 | general | deepseek-v4-flash-free | 40 | $0 | XXX | XXX |
| ... | ... | ... | ... | ... | ... | ... |
| **Total** | | | **XXXX** | **$X.XX** | **XXX** | **XXX** |
```

### 5.3 排版约定

- 👤 User 消息
- 🤖 Assistant 消息（标注 agent 类型）
- 🔧 Tool 调用（标注工具名 + 关键参数）
- 💡 Reasoning（折叠显示）
- `[Compact #N]` compact 摘要块
- `> ` 引用块用于截断的输出
- 代码块用于 bash 命令和代码片段

## 6. 查询实现

### 6.1 时间锚定

任务号 → 时间戳的映射策略（按优先级）：

1. **git log**：`git log --oneline --grep="T048" | tail -1` → 提交时间
2. **任务文档**：`ls docs/tasks/T048-*/P0-brief.md` → 文件修改时间
3. **会话标题搜索**：`SELECT * FROM session WHERE title LIKE '%T048%'`
4. **手动指定**：用户直接给日期

### 6.2 SQL 查询模板

```sql
-- 按时间范围查主会话
SELECT id, title, parent_id, agent, model, cost, 
       tokens_input, tokens_output, time_created, time_updated, path
FROM session 
WHERE time_created >= :start_ts AND time_created <= :end_ts
  AND (parent_id IS NULL OR parent_id = '')
ORDER BY time_created ASC;

-- 查子会话
SELECT id, title, parent_id, agent, model, cost,
       tokens_input, tokens_output, time_created, time_updated
FROM session
WHERE parent_id IN (:main_session_ids)
ORDER BY parent_id, time_created ASC;

-- 查会话的所有消息
SELECT id, data, time_created
FROM message
WHERE session_id = :session_id
ORDER BY time_created ASC;

-- 查消息的所有 parts
SELECT id, data, time_created
FROM part
WHERE message_id = :message_id
ORDER BY time_created ASC;

-- 查 compact 摘要
SELECT m.id, m.time_created, p.data
FROM message m
JOIN part p ON p.message_id = m.id
WHERE m.session_id = :session_id
  AND json_extract(m.data, '$.agent') = 'compaction'
  AND json_extract(p.data, '$.type') = 'text'
ORDER BY m.time_created ASC;

-- 按标题模糊搜索
SELECT id, title, time_created, time_updated
FROM session
WHERE title LIKE :keyword
ORDER BY time_created ASC;
```

### 6.3 会话树构建

```python
def build_session_tree(sessions: list[Session]) -> dict:
    """构建 parent → children 映射"""
    tree = {}
    children_map = defaultdict(list)
    for s in sessions:
        if s.parent_id:
            children_map[s.parent_id].append(s)
        else:
            tree[s.id] = {"session": s, "children": []}
    for parent_id, children in children_map.items():
        if parent_id in tree:
            tree[parent_id]["children"] = children
        else:
            # parent 不在当前查询范围，作为独立根
            tree[parent_id] = {"session": None, "children": children}
    return tree
```

## 7. Skill 接口设计

### 7.1 触发方式

```
"提取session记录" / "查会话日志" / "session log" / "提取T048会话"
```

### 7.2 输入参数

| 参数 | 必需 | 说明 | 示例 |
|------|------|------|------|
| `query` | ✅ | 查询标识 | `T048` / `ses_xxx` / `2026-07-06` / `header redesign` |
| `end` | ❌ | 结束标识（默认到现在） | `T052` / `2026-07-11` |
| `output` | ❌ | 输出路径 | `/tmp/session-log-T048.md` |
| `mode` | ❌ | `compact`(默认) / `full` | `full` 保留所有原始消息 |
| `project` | ❌ | 项目筛选 | `peekview` |
| `include_subagents` | ❌ | 是否包含子会话 | `true`(默认) / `false` |
| `tool_detail` | ❌ | tool 输出详细度 | `summary`(默认) / `verbose` / `minimal` |

### 7.3 输出

- Markdown 文件写入指定路径（默认 `/tmp/session-log-<range>.md`）
- 控制台输出摘要：会话数、消息数、时间范围、文件路径

## 8. 实现方案

### 8.1 方案 A：Python 脚本

**优点**：
- SQLite 操作原生支持
- JSON 解析方便
- 可独立运行，不依赖 Agent

**缺点**：
- 需要额外维护脚本
- Skill 需要通过 bash 调用

**结构**：
```
opencode-session-extractor/
├── SKILL.md              # Skill 描述
├── extract.py            # 主脚本
├── design.md             # 本文档
└── tests/
    └── test_extract.py   # 测试
```

### 8.2 方案 B：Agent 内联实现

**优点**：
- 无需额外脚本
- Skill 指导 Agent 直接用 sqlite3 + python3 提取

**缺点**：
- 每次执行都要重新写 SQL + 格式化逻辑
- 不可复用，不可测试

### 8.3 方案 C：Python 脚本 + Skill 引导

**推荐**。脚本处理数据提取和格式化，Skill 指导 Agent 如何调用脚本、如何处理参数。

```
opencode-session-extractor/
├── SKILL.md              # Skill 描述 + 使用指南
├── extract.py            # 提取脚本（可独立运行）
├── design.md             # 本文档
└── tests/
    └── test_extract.py
```

**调用方式**：
```bash
# 按任务号
python3 extract.py --query T048 --end T052 --output /tmp/session-log-T048-T052.md

# 按会话 ID
python3 extract.py --session ses_0be69a44cffetYLj0MROI9jYfy --output /tmp/session-log-ses_0be69a44.md

# 按时间范围
python3 extract.py --from 2026-07-06 --to 2026-07-11 --output /tmp/session-log-20260706-0711.md

# 按关键词
python3 extract.py --keyword "header redesign" --output /tmp/session-log-header.md

# 完整模式（不使用 compact 摘要）
python3 extract.py --query T048 --mode full --output /tmp/session-log-T048-full.md
```

## 9. 技术风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| DB 被锁（WAL 模式） | 读取失败 | 用 `PRAGMA journal_mode=WAL` 确认；只读查询不会锁；重试 3 次 |
| read tool 输出巨大 | 产出文件过大 | 默认不保留 read 内容；`--tool-detail verbose` 才保留 |
| 敏感信息泄露 | API key 等暴露 | 自动脱敏；`--no-redact` 可关闭（需确认） |
| 会话树不完整 | parent_id 指向的会话不在查询范围 | 提示用户扩大范围；或标记 `[parent not in range]` |
| 时间锚定不准 | T048 起始时间偏差 | 多源交叉验证（git + 文档 + 会话标题） |
| compact 摘要质量 | 摘要遗漏关键决策 | `--mode full` 可回退到原始消息 |
| 产出文件过大 | 几千条消息的 .md 难以阅读 | 分文件输出（每会话一个 .md）+ 汇总文件 |

## 10. 后续扩展

- **交互式查询**：不产出文件，直接在对话中回答（"T048 用了什么模型？"）
- **统计仪表盘**：token 消耗趋势、模型使用分布、cost 分析
- **diff 追踪**：提取所有 edit/write 操作，重建文件变更时间线
- **多平台支持**：Claude Code 的会话存储格式不同，需适配层
- **实时监控**：watch 模式，新消息实时追加到 .md
