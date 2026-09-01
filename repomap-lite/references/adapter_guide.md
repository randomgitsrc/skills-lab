# 如何新增一种语言/技术栈的支持

`repomap_lite` 用适配器模式（见 `scripts/adapter_base.py`）组织语言支持：
每种语言/技术栈是一个独立文件，互不依赖，新增一种语言不需要改动
`repomap_lite.py`、`adapter_base.py` 或任何其他已有适配器。

Rust 支持（`scripts/adapters/rust_adapter.py`）是这个架构目前最完整的
实战案例——从"完全不支持"到"真实项目上 precision/recall 均在 95%+"，
过程中的具体教训写在下面"常见坑"里，新增语言前建议先读一遍。

## 最简步骤

1. 在 `scripts/adapters/` 下新建一个文件，例如 `ruby_adapter.py`
2. 实现一个满足接口的类（不需要真的继承 `LanguageAdapter`，Python 的
   `Protocol` 只做类型检查，鸭子类型即可）：

```python
from adapter_base import AdapterResult, Symbol, register
from pathlib import Path

class RubyAdapter:
    name = "ruby"  # 用于日志/诊断信息，唯一标识

    def match(self, filepath) -> bool:
        return Path(filepath).suffix == ".rb"

    def extract_symbols(self, filepath, lines: list[str]) -> AdapterResult:
        symbols = []
        # ... 具体的符号提取逻辑 ...
        return AdapterResult(symbols=symbols)

register(RubyAdapter())
```

3. 在 `scripts/adapters/__init__.py` 里加一行 import：
   ```python
   from adapters.ruby_adapter import RubyAdapter  # noqa: F401
   ```
4. 完成。`repomap_lite.py` 完全不需要改动——它只调用
   `find_adapter_for(filepath)` 来找匹配的适配器，不知道也不关心
   具体有哪些语言。

## 设计要点

### `match()` 通常按扩展名判断
大多数语言按文件扩展名区分就够了（见已有适配器的 `match()` 实现）。
如果需要按文件名整体判断（例如 `Makefile`、`CMakeLists.txt` 这类
没有扩展名的文件），`filepath` 参数是完整路径，可以用
`Path(filepath).name` 取文件名本身来匹配。

### `extract_symbols()` 返回的 `depth` 必须自洽
`depth=0` 表示顶层符号，`depth=1` 必须能追溯到某个更早出现的
`depth=0` 符号作为"父级"。渲染时直接用 `depth * 4` 个空格缩进，
不做任何额外的父子关系校验，所以适配器自己要保证这个不变量。

### `is_generated()` 是可选的，只在语言有明确的生成文件约定时才实现
如果这门语言的生态里有广泛使用的"本文件自动生成"标记约定（比如 Go 的
`// Code generated ... DO NOT EDIT.`，或者某个特定代码生成器的固定文案），
可以覆盖这个方法，跳过匹配到标记的文件，不把它们当作手写项目代码展示。
不实现这个方法完全没问题——`LanguageAdapter` 协议默认不要求它，
`adapter_base.adapter_says_generated()` 会安全处理"没实现"的情况。

```python
from adapter_utils import matches_generated_file_markers

_GENERATED_MARKERS = (
    re.compile(r"^\s*//\s*Some Tool Generated This File\.\s*$"),
)

class MyAdapter:
    ...
    def is_generated(self, lines: list[str]) -> bool:
        return matches_generated_file_markers(lines, _GENERATED_MARKERS)
```

**不要**为了"看起来更完整"而不加验证地猜测一个标记格式——如果没有真实
工具的输出可以核对，宁可不实现这个方法，也不要写一条自己编造的、
从未在真实生成文件里见过的规则。已经实现的例子里，Go 和 Python 的
规则都是拿真实工具（`protoc-gen-go`、`grpc_tools.protoc`）跑出来的
输出直接验证过的；Java 的 `@Generated` 规则则如实标注了"依据规范文档，
未用真实工具验证"，因为当时没有可用的网络权限去获取真实的注解处理器
（这个区分本身也该在新适配器里保持——不确定的规则要在代码注释里说清楚
它的验证程度，不要把"我觉得应该是这样"包装成"已验证"）。

一个语言完全没有这种约定也是正常结果，不是缺陷——比如 Rust 的
`derive` 宏在编译期展开，不产出独立的生成源文件，真正需要处理的生成
文件反而都落在 `target/` 里，已经被目录级排除覆盖，不需要
`is_generated`。判断要不要实现这个方法之前，先用真实工具生成一份样例
文件确认这个语言确实存在"生成文件跟手写代码混在同一目录、需要靠内容
标记区分"这种模式，而不是假设所有语言都有。

### `extract_dependencies()` 是可选的，实现前先做逐语言分析，不要想当然

如果这门语言有规整的依赖声明语法（`import`/`require`/`#include`/`use`
这类），可以覆盖这个方法，让 REPOMAP 展示"这个文件依赖谁"。不实现完全
没问题，跟 `is_generated` 一样是安全的默认降级。

**实现前一定要先分析这门语言的依赖语法有哪些形式、哪些能可靠识别、
哪些不能**，不要拿到手就直接写正则——已经支持的6种语言（Go/Python/
JS-TS/C-family/Rust/Ruby）在实现过程中每一种都发现了至少一个"想当然
会出错"的地方，具体记录见 `references/known_limitations.md`"文件间
依赖识别"一节，最值得引以为戒的两条：

1. **不要用"看起来像"的正则猜测标准库/内部包**，用精确匹配一份真实
   清单（能从语言自带的运行时数据源拿到最好，比如 Python 的
   `sys.stdlib_module_names`；拿不到就手动固化一份，比如 Go 从真实
   工具链提取的189个标准库路径）。真实案例：Go 最初用"不含域名点号
   的裸路径=标准库"这条正则，被一个假设的项目 module 名
   `goreal`（不含点号的短小写单词，语法上跟真标准库包名完全无法区分）
   直接证伪。
2. **依赖声明语法不一定要求出现在行首**。ESM 的 `import`/`export...from`
   是语句级语法可以行首锚定，但 CommonJS 的 `require(...)` 是普通函数
   调用，可以嵌在任意表达式里——真实案例是 lodash 的
   `mod.require('util')` 藏在一长串条件判断里。新语言如果有类似的
   "既可能是语句关键字、也可能是函数调用"的依赖声明形式，先用真实
   项目源码搜一遍这种写法存在不存在，再决定用 `.match()`（行首锚定）
   还是 `.search()`（任意位置）。

```python
import re

from adapter_base import Dependency
from adapter_utils import extract_quoted_literal

# 标准库清单必须来自真实、可核对的来源（语言自带数据、工具链目录、
# 官方文档），下面这行只是占位示意——换成这门语言真实的标准库名单。
_MY_LANG_STDLIB = frozenset({"core_lib_a", "core_lib_b"})

MY_IMPORT_RE = re.compile(r"^\s*import\s+([A-Za-z_][\w.]*)")

def _classify_my_lang_target(target: str) -> str:
    if target.startswith("."):
        return "internal"          # 语法本身能确定
    if target in _MY_LANG_STDLIB:  # 精确匹配一份真实清单，不要用正则猜
        return "external"
    return "unknown"                # 证据不足，不要瞎猜

class MyAdapter:
    # ... 其余适配器方法 ...

    def extract_dependencies(self, lines: list[str]) -> list[Dependency]:
        deps = []
        for i, raw in enumerate(lines):
            m = MY_IMPORT_RE.match(raw.rstrip("\n"))
            if m:
                target = m.group(1)
                deps.append(Dependency(
                    raw_text=raw.strip(), kind=_classify_my_lang_target(target),
                    line_no=i + 1, target=target,
                ))
        return deps
```

`Dependency.kind` 是四选一（`internal`/`external`/`unknown`/`dynamic`），
完整语义说明见 `adapter_base.py` 里 `DependencyKind` 的文档字符串——
简单说：目标字符串知道、内外部归类不确定时用 `unknown`（不要武断分类
成 internal 或 external），目标本身是变量/表达式无法静态解析时用
`dynamic`（`target` 设为 `None`，不要瞎猜一个可能错的字符串）。判断
"括号里是不是一个字符串字面量"可以直接用共享工具
`adapter_utils.extract_quoted_literal()`，不需要每个适配器各写一遍。

如果这门语言的注释/字符串屏蔽机制会影响到依赖声明行本身（比如注释里
提到的 import 文字不该被误判成真实依赖），复用已有的屏蔽函数判断
"这一行是否真的是代码"，但提取真正的依赖内容时要回到**未屏蔽的原始
文本**——这是跟符号展示同一条原则的延伸，见
`references/known_limitations.md`"字符串字面量被静默抹成占位符"一节
的详细说明，不要在新语言上重新踩一遍这个坑。

### 复用 `adapter_utils.py` 里的通用工具，不要重复造轮子
已有的工具函数：
- `indent_of(line)` — 计算缩进宽度
- `IndentStack` — 基于缩进的嵌套深度追踪（适合 Python 这类缩进敏感的语言）
- `BraceDepthTracker` — 基于花括号的嵌套深度追踪（适合 C 系语言）
- `line_is_brace_balanced(line)` — 判断一行内花括号是否当场配平（`void foo() { }`
  这种单行函数体），C 系适配器和 JS/TS 适配器都在用，见下方"常见坑"第1条
- `mask_c_family_comments_and_strings(lines)` — **新语言如果用花括号表达
  嵌套，这基本是第一个要看的工具函数**。在同一次线性扫描里统一处理
  `//`/`/* */` 注释和 `"..."`/`'...'`/C#的`@"..."`/JS的模板字符串，
  按字符实际出现顺序判断"当前处于哪种语法环境"。**不要**只挑其中一部分
  功能用、自己再拼一个注释处理或字符串处理——这个函数存在的全部意义就是
  "注释和字符串的语法边界要在同一次扫描里一起判断"，拆开用等于放弃了
  它解决的核心问题（详见下方"常见坑"第3条，以及 Rust 适配器踩过的更极端
  版本）。
- `skip_python_style_triple_quoted_strings(lines)` — 处理三引号字符串
  （Python 专用）

如果新语言的注释/字符串语法跟现有的都不完全一样（比如有嵌套块注释、
不转义的原始字符串、不闭合的类似生命周期标注这种符号），`mask_c_family_
comments_and_strings` 可能不够用，这时候**不要**尝试给这个共享函数打
外部补丁或者拆成多个独立预处理阶段——直接照着它的实现思路，为新语言写
一个独立的、同样一次扫描处理全部相关语法元素的专属版本（Rust 适配器的
`mask_rust_source` 就是这么做的，见下方"常见坑"第4条的详细教训）。

### 花括号语言（C系）的常见坑，开发过程中反复验证过

1. **同一行内配平的函数体**（`void foo() { }`）容易让"是否已进入函数体"
   的状态机因为深度从未真正超过基线而卡死，导致该行之后的整个文件都被
   误判为"仍在函数体/容器内部"，无法识别任何后续符号。用
   `line_is_brace_balanced()` 检测规避。**同一类问题还有一个变体**：
   C 语言的前向声明（`struct Foo;`，只有分号没有花括号）如果被误判为
   "开启了一个等待花括号体的容器"，会导致同样的永久卡死——真实项目 Redis
   复现过这个案例，一个文件因此从本该识别的1200+符号骤降到8个。判断
   "是否需要压入等待帧"时，除了检查同行是否配平，还要检查是不是
   `;` 结尾且完全没有花括号的纯声明。

2. **函数体内部的语句被误判为新定义**（比如某个看起来像"类型 函数名(参数)"
   的调用语句，例如 Qt 的 `emit foo(x);`）。需要显式区分"容器声明层"和
   "函数体执行层"，进入执行层后完全跳过符号识别，不能只靠"深度是否等于
   父层+1"这一个条件区分——用统一的帧栈（区分 `container`/`body` 两种
   帧），比分别维护多个独立的深度变量更不容易出错。

3. **注释和字符串的语法边界互相依赖，不能拆成独立阶段处理**。这是本项目
   开发过程中反复验证、代价最高的一条教训，在 C 系语言和 Rust 上各自
   独立复现过：
   - 如果先屏蔽字符串再剥注释：英文注释里常见的撇号
     （`/* the caller's buffer */`）会被误判为字符字面量开始，如果连带
     吞掉了注释自己的收尾符 `*/`，会导致文件从这里往后全部被误判为
     仍在注释里。
   - 如果先剥注释再屏蔽字符串：字符串内容里恰好出现的 `/*` 或类似 `*/*`
     这样的三字符序列（从中间读恰好构成 `/*`）会被注释扫描器误判为
     真实注释开始。
   - Rust 上还遇到过更极端的版本：判断"这是不是一个原始字符串
     `r"..."`"这件事本身依赖"当前是否已经在注释里"——`/*! ... "search
     worker" ... */` 这种文档注释里，"worker" 结尾的字母 `r` 加紧跟的
     引号恰好拼出 `r"`，被独立的原始字符串扫描器误判。
   
   结论：新语言如果字符串/字符字面量语法跟已支持的语言有任何差异
   （不同的转义规则、不闭合的类似记号、可跨行的字面量、嵌套注释等），
   **一定要用一次线性扫描的单一状态机处理全部注释+字符串语法**，不要
   图省事拆成"先处理A再处理B"的流水线，不管拆成几个阶段、按什么顺序，
   都会在某处产生同样性质的误判。

4. **"类型独占一行，函数名在下一行"的写法**（`static void\nfoo() {`，
   Linux kernel/jemalloc 等大量真实 C 代码库的主流风格）：如果判断
   "上一行是不是纯类型"时用固定的关键字白名单（`static`/`inline`/
   `extern`/`const`），会漏掉项目自定义的宏前缀（比如 jemalloc 的
   `JEMALLOC_ALWAYS_INLINE void`，或者 xxHash 的 `XXH_PUBLIC_API
   XXH_errorcode`）。改成接受任意标识符序列更稳健，但要记得排除
   `return`/`if`/`for` 等控制流关键字单独占一行的情况，避免把
   `return\n    some_call(x);` 这种罕见但合法的写法误判为函数定义。

### 优先考虑扩展现有适配器，而不是新增一个
如果新语言的语法跟已有的花括号语言（C#/Java/C++）足够相似
（访问修饰符 + 类型 + 名字 + 花括号这套结构），优先考虑在
`c_family_adapter.py` 的 `DIALECTS` 字典里加一个新的 `Dialect` 配置，
而不是从零写一个新适配器——这是当前架构里"方言参数化"的设计目的。
反之，如果语法结构差异较大，新开一个独立适配器文件更清晰。**Rust 是
一个已验证的例子**：虽然也是花括号语言，但没有 C 系的访问修饰符系统，
核心关键字（`fn`/`impl`/`trait`/`mod`）和 `impl Trait for Type` 这种
构造跟 C 系家族的相似度不够高，做成了独立适配器（`rust_adapter.py`），
复用了 `BraceDepthTracker` 但没有复用字符串屏蔽函数（原因见上方"常见坑"
第3条）。

### 容器格式（一个文件里套多种语言）参考 Vue 适配器的做法
如果新格式是"一个文件里嵌了另一种已支持语言的代码块"（类似 Vue 的
`<script>` 块、Svelte、Astro 等单文件组件格式），不要重新实现一遍
内部语言的符号提取逻辑——参考 `vue_adapter.py` 的做法：抽取出内部代码块，
直接调用已有适配器的 `extract_symbols()`，再把返回的行号按偏移量
修正回容器文件的真实行号。

### 方言参数化模式参考 Shader 适配器
如果新增的是一组语法接近但存在方言差异的技术栈（类似 GLSL/HLSL/WGSL
都是"着色器语言"但语法风格不同），参考 `shader_adapter.py` 的做法：
一个适配器类覆盖全部方言，按扩展名内部分支到不同的正则规则集，
而不是为每个方言单独注册一个适配器实例。

## 测试新适配器

写完之后，最低限度应该做这几件事（本项目在开发过程中就是这么做的，
见 `references/known_limitations.md` 记录的每一个 bug 都是这样发现的）：

1. **写几个合成测试样本**，覆盖该语言最常见的写法（类/结构体、方法、
   嵌套、单行 vs 多行、构造函数等语言特有概念），确认基本能跑通、
   depth 计算正确。
2. **在混合语言项目里跑一遍**，确认新适配器不会跟其他适配器的
   `match()` 冲突（正常情况下不会，因为都是按扩展名区分，但如果新适配器
   的 `match()` 用了更宽松的判断逻辑就要小心）。
3. **如果可能，找一个真实的开源项目跑一遍，人工抽查输出**，而不是只用
   自己编的合成样本——本项目里发现的大多数严重 bug（C# 逐字字符串、
   Java `throws` 子句、单行方法体匹配失败）都只有在真实代码上跑才会暴露，
   合成测试样本天然会绕开真实代码里的"意外写法"。
4. **更新 `references/known_limitations.md`**，如实记录新适配器的已知局限，
   不要只写"待办"，要写清楚具体在什么情况下会失效。
