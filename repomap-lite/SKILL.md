---
name: repomap-lite
description: Use when entering an unfamiliar codebase, or when the user asks to map/understand a project's structure or wants a REPOMAP.md. Generates a zero-dependency structural map (top-level functions, classes, structs, interfaces, nesting) for fast cold-start orientation, covering Python, JS/TS, Go, Rust, Ruby, C#, Java, C/C++, shaders, and Makefile/Dockerfile in one pass. No third-party packages.
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

# 不读取仓库的 .gitignore/.repomapignore（默认都会读取，见下方说明）
python3 scripts/repomap_lite.py -o REPOMAP.md --no-gitignore

# 包含标注了自动生成标记的文件（默认会跳过，见下方说明）
python3 scripts/repomap_lite.py -o REPOMAP.md --include-generated

# 单文件改动后增量更新，不用全量重扫
python3 scripts/repomap_lite.py -o REPOMAP.md --update-file src/foo.py

# 只扫某个子目录（monorepo 场景，见下方"按范围生成"）
python3 scripts/repomap_lite.py --root packages/some-package -o packages/some-package/REPOMAP.md

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

## 自定义排除：`.repomapignore`

`.gitignore` 回答"这个路径要不要被 git 追踪"，`.repomapignore` 回答一个
不同的问题——"这个路径要不要出现在结构地图里"。两者不总是一致，需要
`.repomapignore` 的典型场景：

- 项目里**确实提交到 git**的第三方代码快照、vendored 依赖、大批量测试
  fixture、示例代码——这些内容真实存在、需要被版本控制，但对"这个项目
  实际是怎么写的"这个问题没有信息量，`.gitignore` 对它们无能为力
  （因为它们本来就该被追踪）
- 内容不满足任何已支持语言的"自动生成文件"标记检测（见上文），但项目组
  自己知道这是生成物/不需要理解的内容
- 想针对"这份地图给 agent 用"这个场景单独调整排除范围，不想牵连 IDE/
  CI/部署脚本对同一批文件的处理方式

语法跟 `.gitignore` 完全一致（含嵌套子目录、通配符、否定模式），一份
`.repomapignore` 只管它所在目录及子树。用法：

```bash
# 项目根目录（或任意子目录）新建 .repomapignore
echo "vendored_snapshot/" >> .repomapignore
echo "legacy_examples/" >> .repomapignore

# 不想让 .repomapignore 生效时，跟 .gitignore 共用同一个开关
python3 scripts/repomap_lite.py -o REPOMAP.md --no-gitignore
```

**不需要、也不应该自动维护这个文件**——排除规则是项目组主动决定并维护的
静态配置，工具不会自动往里面加东西。原因：判断"某个路径不值得放进地图"
本质上需要人类/项目意图介入；如果让工具自动判断，风险是新增的真实代码
被意外归类为"不值得展示"而悄悄从地图消失，且没人会注意到——这比"忘记
排除一个生成目录、地图里多了点噪音"严重得多。跟 `.gitignore` 一样，
建议把 `.repomapignore` **提交到版本控制**，让排除规则本身成为团队共识
而不是某个人本地的临时设置。

## 按范围生成：`--root` 支持 monorepo 子项目地图

`--root` 指定的是**扫描范围的起点**，可以是仓库根目录（默认），也可以是
仓库内任意子目录——常见于 monorepo，只想看某个子包自己的结构：

```bash
cd packages/some-package
python3 /path/to/scripts/repomap_lite.py --root . -o REPOMAP.md
```

`.gitignore`/`.repomapignore` 判断和输出里的文件路径始终相对**仓库根
目录**（保持跟 git 语义一致，也让路径本身可读——即使只扫一个子包，
路径也是 `packages/some-package/src/foo.py` 这种完整仓库相对路径，
不会让人误以为这是整个仓库的地图）。想给多个子包分别生成地图，重复
调用即可，各自指定 `--root` 和 `-o`：

```bash
for pkg in packages/*/; do
  python3 scripts/repomap_lite.py --root "$pkg" -o "$pkg/REPOMAP.md"
done
```

不需要一个专门的"多地图批量生成"功能——`--root` + `-o` 这两个已有参数
组合起来就是这个工作流，没有必要为了同一件事再造一个新接口。

## REPOMAP.md 要不要提交到版本控制？建议不要，当作可随时重新生成的临时产物

**默认建议**：把 `REPOMAP.md` 加进 `.gitignore`，当作类似构建产物的东西
——需要的时候现场生成，不提交。理由：

- **过时的地图比没有地图更危险**。本工具生成的成本极低（本地脚本，
  零外部依赖，秒级完成），比起"提交一份地图、寄希望于每次改代码后有人
  记得重新生成并提交"，现场生成一次的确定性高得多。一份没跟上最新代码的
  地图会让 agent 对着过时的结构做判断而不自知——这比"没有地图、多花一次
  工具调用现场生成"的代价高得多。
- **合并冲突**：两个分支各自改了代码结构，各自的地图内容也会不一样，
  合并时地图文件本身几乎必然冲突，而这个冲突毫无意义（解决的时候你也不会
  去手动合并两份符号列表，只会重新生成一遍）。

**什么时候可以合理地反过来提交它**：如果团队的实际使用场景是"人和 agent
都要看，且希望不需要额外一次生成步骤就能立刻打开"，或者仓库大到生成
耗时不可忽略（本工具本身很快，但配合 `--max-files`/分包生成的策略之后，
"完整走一遍生成流程"可能不再是纯粹的"随手就有"），提交也是合理选择——
这是团队自己的取舍，本工具不做技术性阻拦，只给出默认建议和理由。如果
决定提交，跟 `.repomapignore` 一样，建议明确写进项目约定，而不是有人
提交有人不提交、地图和源码的一致性变得不可预期。

## REPOMAP 是符号索引，不是架构文档——它能回答什么、不能回答什么

REPOMAP 只保留"定义行本身+一层嵌套"，不含函数体、不含调用关系、不含
跨文件的数据流向。用它能高效回答的问题：

- 某个功能大概实现在哪个文件（先看符号名，再决定读哪个文件）
- 一个文件/模块的顶层结构长什么样，有哪些类、哪些方法
- 项目的模块边界在哪（哪些目录对应哪些子系统）
- 构建/部署入口（Makefile 的 target、Dockerfile 的构建阶段、`main.py`/
  `main.go`/`main.ts` 这类入口文件本身）

它**回答不了**、需要另外读代码或读文档才能回答的问题：

- 调用关系/依赖关系（"这个函数被谁调用""这条链路怎么串起来的"）
- 路由/接口的语义（一个 API 端点具体的请求/响应结构）
- 数据模型之间的关联（外键关系、字段含义）
- 业务规则、安全逻辑这类"为什么这么写"的意图（防枚举、参数校验规则等）
- 具体实现细节（某个第三方库怎么被集成、某段算法的具体逻辑）

如果项目本身有 `AGENTS.md`/架构说明文档，两者不是互相替代关系，是互补：
文档负责"为什么这么设计"，REPOMAP 负责"现在实际长什么样"——文档可能会
因为没跟上重构而过时，REPOMAP 因为是从当前源码直接生成的，不会"撒谎"，
但它也确实不试图捕捉意图和语义。冷启动一个新仓库时，合理的顺序是：
先读项目自己的架构文档（如果有）建立"为什么"层面的心智模型，再用
REPOMAP 建立"现在有什么、在哪"这层坐标，需要深入理解某个具体机制时
再去读那几个关键文件的实际内容——不要指望仅凭 REPOMAP 就能回答"这个
系统是怎么运转的"这类问题，那超出了这个工具的设计范围，需要真正去读
代码或者用更重的工具（完整的静态分析/调用图生成，那是另一类工具要做的
事，不建议往这个"零依赖、跨语言、秒级生成"的工具里塞，会牺牲掉它现在
最大的优点）。

## 输出格式

REPOMAP.md 顶部是**文件清单（索引段）**——每个文件一行，列出符号数并从多到少
排序，让 agent 冷启动时先看到整个仓库摊开在哪、哪些文件是重点，再按需下钻：

```
<!-- 索引：文件清单 · 符号数（从多到少），供快速定位重点文件 -->
 20  src/core/main.py
 16  src/core/utils.py
 13  src/adapters/http.py
 10  src/state/store.py
  5  src/cli/args.py
```

符号数 = 该文件提取到的符号（函数/类/结构体等）数量，跟后面逐文件 block 里的
`│` 定义行一一对应。索引段之后才是逐文件 block：

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
输出顶部会有一行来源标记，注明这是无依赖正则版生成的，以及生成时间；
索引段和每个 block 也各带机器可读的注释（`<!-- 索引... -->`、
`<!-- symbols: N -->`），增量更新靠它们重建索引，agent 可忽略这些注释。

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
