---
name: repomap-lite
description: "Use when entering an unfamiliar codebase and needing a structural overview, or when the user asks to \"map the codebase\", \"understand the project structure\", \"generate a repo map\", or wants a REPOMAP.md. Generates a zero-dependency structural map (top-level functions, classes, structs, interfaces, nesting) for fast cold-start orientation, covering Python, JS/TS, Vue, Go, Rust, Ruby, C#, Java, C/C++ (incl. Qt), shaders, and Makefile/Dockerfile in one pass. No third-party packages."
---

# repomap-lite

零外部依赖的多语言代码地图生成器。给 agent 冷启动进入陌生代码库时用，
快速拿到一份结构化的 `REPOMAP.md`：每个文件的顶层函数/类/结构体/接口
及其嵌套关系，格式紧凑，不需要读完整源码就能建立起项目结构的整体认知。

## 何时使用

- 用户要求生成项目地图、代码库结构概览、或明确提到 REPOMAP.md
- 你（agent）刚进入一个不熟悉的仓库，需要先建立整体结构认知，
  再决定去哪个文件找具体实现——优先用这个而不是逐个 `find`/`ls` 目录
- 需要判断某个功能可能实现在哪个文件时，先扫一遍地图里的符号名称，
  比盲目全仓库 grep 关键词更快定位候选文件
- 规划跨文件重构、需要先摸清受影响范围的大致轮廓

## 何时不用 / 何时该跳过直接读源码

- 已经明确知道目标文件路径或函数名——直接读文件或 grep，不必先生成地图
- 需要函数的具体实现细节、完整参数列表——地图只保留签名首行和单行摘要，
  看不到函数体，必须读源文件
- 目标语言不在支持范围内（见下方"支持范围"），文件会被直接跳过

## 支持范围

单次运行覆盖以下语言/技术栈，混合语言项目里各文件按扩展名自动分派到
对应的识别规则，互不干扰：

| 语言/技术栈 | 扩展名 |
|---|---|
| Python | `.py` |
| JavaScript / TypeScript | `.js` `.jsx` `.mjs` `.cjs` `.ts` `.tsx` |
| Go | `.go` |
| Rust | `.rs` |
| Ruby | `.rb` |
| C# | `.cs` |
| Java | `.java` |
| C（含 C++、Qt 框架代码） | `.c` `.cpp` `.cc` `.cxx` `.hpp` `.hxx` `.hh` `.h` |
| Vue 单文件组件 | `.vue`（解析 `<script>`/`<script setup>` 块，复用 JS/TS 规则） |
| Shader：GLSL(OpenGL/WebGL) / HLSL(DirectX) / WGSL(WebGPU) | `.glsl` `.vert` `.frag` `.hlsl` `.fx` `.wgsl` 等 |
| Makefile（提取 target，不是传统语言符号） | 文件名 `Makefile`/`makefile`/`GNUmakefile`，或 `.mk`/`.make` |
| Dockerfile（提取 `FROM...AS` 构建阶段） | 文件名 `Dockerfile`/`Dockerfile.*`，或 `.dockerfile` |

架构上是可扩展的——新增一种语言不需要改动核心调度代码，只需要新增一个
适配器文件，见 `references/adapter_guide.md`。当前**不支持**：Kotlin、
Swift、PHP、Scala 等语言（遇到会直接跳过，不报错，不影响其他文件的处理）。

这不是 tree-sitter 版 repomap 的替代品，是在无法安装第三方依赖的环境下
（沙箱、离线、CI 等）使用的轻量替代方案，基于正则+括号/缩进/关键字配对
状态机而非真正的语法树。**实测精度因语言而异**——Go/Rust 接近或达到
100% 准确，C#/Java/JS/Python/Ruby 在 70-99% 区间（Ruby 的数字需要
额外说明，见 `references/known_limitations.md`：参考基准工具本身对 Ruby
的支持有明显缺口，直接对比会低估本工具的真实表现），C++（尤其含大量
宏/模板元编程的代码）明显更低。不要假设所有语言的可靠程度一致，
具体数字、每个已修复 bug 的根因、以及当前仍存在的局限见
`references/known_limitations.md`。

## 用法

```bash
# 全量扫描当前 git 仓库，写入 REPOMAP.md
python3 scripts/repomap_lite.py -o REPOMAP.md

# 限制文件数（大仓库快速抽样）
python3 scripts/repomap_lite.py -o REPOMAP.md --max-files 50

# 包含 node_modules/vendor/dist/bin/obj 等默认排除目录
python3 scripts/repomap_lite.py -o REPOMAP.md --include-vendor

# 不读取仓库的 .gitignore（默认会读取，见下方说明）
python3 scripts/repomap_lite.py -o REPOMAP.md --no-gitignore

# 包含标注了自动生成标记的文件（默认会跳过，见下方说明）
python3 scripts/repomap_lite.py -o REPOMAP.md --include-generated

# 单文件改动后增量更新，不用全量重扫
python3 scripts/repomap_lite.py -o REPOMAP.md --update-file src/foo.py

# 查看当前已注册的语言适配器
python3 scripts/repomap_lite.py --list-adapters
```

工具会从当前目录向上查找 `.git` 所在的仓库根目录；找不到则报错退出。
不指定 `-o` 时输出到 stdout。大仓库（输出超过约1万行）会在 stderr 打印
软性警告并给出建议，不会阻塞或截断输出——仍然拿到完整地图，只是被提醒
可能需要用 `--max-files` 或分目录处理来控制下游 token 消耗。

默认会读取仓库内的 `.gitignore`（含嵌套的子目录 `.gitignore`，语义
跟 git 一致——子目录规则只影响该子树），被忽略的文件/目录不会出现在
地图里，`node_modules` 这类常见目录即使不在 `.gitignore` 里也仍然会被
默认的硬编码排除列表挡住（两套机制独立生效，`--include-vendor` 只关闭
硬编码列表，`--no-gitignore` 只关闭 `.gitignore` 读取）。支持的
`.gitignore` 语法：`*`/`?` 通配符、行尾 `/` 表示仅目录、前导 `/` 表示
仅相对仓库根目录、`!` 开头的否定模式。不支持 `**` 的完整语义（按普通
通配符处理）、全局 gitignore 配置、`.git/info/exclude`，这些是相对少见
的写法，覆盖不到时按"未被忽略"处理，不会误删该出现的内容。

这个能力已经用真实项目的"先构建、再跑起调试服务"这种脏状态验证过——
不只是干净 clone 后直接跑：构建产物（`node_modules`、前端 `dist`、
Python 虚拟环境、散落在真实源码目录内部的 `__pycache__`）全部被正确
排除，同时确认了并修复了一个 `.gitignore` 匹配逻辑本身的正确性问题
（判断一个文件是否被忽略时，早期实现遗漏了检查它的祖先目录是否已被
整体忽略），详见 `references/known_limitations.md`。

`.gitignore`/目录黑名单解决的是"生成物集中在一个独立目录"的情况；
另一种常见情况是生成的源码文件跟手写代码混在同一个目录里，靠文件内容
自带的标记（比如 Go 的 `// Code generated ... DO NOT EDIT.`、Python
protobuf/gRPC 生成代码的标记注释）声明身份。默认会检测并跳过这类文件，
具体识别哪些标记是**每个语言适配器自己的可选能力**（`is_generated()`
方法），不是核心代码里的全局规则——目前 Go 和 Python 的检测规则用真实
工具输出验证过，Java 基于标准注解规范实现但未用真实工具验证，C#/C++
暂未实现。新增语言的生成文件识别，只需要在对应适配器里补一个方法，
不需要改动核心代码，见 `references/adapter_guide.md`。

## 输出格式

```
文件路径:
⋮
│def top_level_func():
│    """一行摘要"""
⋮
│class ClassName:
│    def method_one(self):
⋮
```

每个文件一个 block，`⋮` 表示省略的代码/空白间隔，`│` 前缀标出保留的定义行，
嵌套用 4 空格缩进表示。Makefile/Dockerfile 里"符号"的含义不同于编程
语言——分别是 target 和 `FROM...AS` 构建阶段，展示格式跟编程语言一致
（同样是 `│` 前缀 + 缩进），但不代表函数/类这类代码结构：

```
Makefile:
⋮
│build: main.o utils.o
│test: build
│clean:
⋮

Dockerfile:
⋮
│FROM golang:1.21 AS builder
│FROM alpine:3.18 AS runtime
⋮
```

空文件、无符号文件、不支持的语言/格式文件一律跳过，不生成 block。
输出顶部会有一行来源标记，注明这是无依赖正则版生成的，以及生成时间。

## 维护建议

- 单文件改动后，跑一次 `--update-file` 保持地图同步，比全量重扫快得多
- 大规模重构（批量增删文件）后，重新跑一次全量生成
- 如果发现某个文件的地图明显跟实际代码对不上，优先怀疑是不是撞上了
  `references/known_limitations.md` 里记录的某个已知局限（尤其是 C++
  项目，宏和模板元编程会明显拉低准确率），而不是假设整个工具不可靠

## 深入参考（按需加载，不要求默认读完）

- `references/known_limitations.md` — 每种语言在真实开源项目上实测的
  precision/recall 数字，以及每一个已修复 bug 的完整根因记录（用真实
  代码复现、诊断、修复、验证的过程，不是理论推测）
- `references/adapter_guide.md` — 如何新增一种语言的支持；如果用户要求
  扩展到当前不支持的语言（Kotlin、Swift 等），读这份文档再动手，
  里面记录了花括号语言常见的几个坑（包括 Rust/Ruby 适配器开发过程中
  新确认的"注释与字符串边界必须在同一次扫描里统一处理"这条重要经验），
  避免重新踩一遍已经踩过的雷
