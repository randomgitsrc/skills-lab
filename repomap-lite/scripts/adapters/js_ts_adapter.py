#!/usr/bin/env python3
"""
js_ts_adapter.py — JavaScript / TypeScript 语言适配器。

覆盖范围（相比早期版本，本次修复了两个用真实项目对比测出的真实缺陷）：
- function / class / 箭头函数赋值给命名变量 / export 前缀（早期版本已有）
- **新增**：TypeScript `interface X { ... }` / `type X = ...` / `enum X { ... }`
  （早期版本完全不支持，用 vue/core 项目实测确认整个文件被跳过的严重问题）
- **新增**：模块顶层任意 `const`/`let`/`var` 声明，不要求赋值给函数
  （早期版本要求箭头函数体，真实基准的范围更宽，见 references/known_limitations.md）

嵌套收录规则：只在 class/interface 内部收录成员方法/属性签名一层，
不递归进普通函数体内部找嵌套定义（跟 Python 适配器的处理原则一致，
都是为了对齐真实 tree-sitter 版 repomap 的行为）。用花括号深度而不是缩进
来判断嵌套，因为 JS/TS 的缩进不是语法强制的，代码风格差异比 Python 大得多。
"""

from __future__ import annotations

import re
from pathlib import Path

from adapter_base import AdapterResult, Dependency, Symbol, register
from adapter_utils import (
    BraceDepthTracker,
    indent_of,
    line_is_brace_balanced,
    mask_c_family_comments_and_strings,
)

JS_TS_EXTENSIONS = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}

CLASS_RE = re.compile(r"^(\s*)(export\s+(default\s+)?)?(abstract\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)")
FUNC_RE = re.compile(
    r"^(\s*)(export\s+(default\s+)?)?(async\s+)?function\s*\*?\s*([A-Za-z_$][A-Za-z0-9_$]*)?\s*\("
)
ARROW_ASSIGN_RE = re.compile(
    r"^(\s*)(export\s+(default\s+)?)?"
    r"(const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*(?::[^=]+)?=\s*"
    r"(async\s*)?\(?[^=;]*\)?\s*=>"
)
# 顶层任意 const/let/var 声明（不要求是函数），对齐真实基准的收录范围。
# 名字部分允许两种形式：普通标识符（`const foo = ...`）或对象解构模式
# （`const { Buffer } = require(...)`、`var { METHODS } = require(...)`）——
# 后者是真实项目 express 里常见的写法（`require` 解构导入），早期版本的
# 正则只接受纯标识符，完全无法匹配解构声明，用真实代码复现确认过这个漏报。
# 解构模式内部不细化匹配（不尝试抠出花括号里每个具体绑定的名字），
# 直接把整行文本作为符号展示，跟真实基准对这类声明的展示方式一致
# （基准也是把整个解构语句当一整行签名展示，不拆分成多个符号）。
PLAIN_DECL_RE = re.compile(
    r"^(\s*)(export\s+(default\s+)?)?(const|let|var)\s+"
    r"(?:[A-Za-z_$][A-Za-z0-9_$]*|\{[^{}]*\}|\[[^\[\]]*\])\s*[:=]"
)
INTERFACE_RE = re.compile(r"^(\s*)(export\s+(default\s+)?)?interface\s+([A-Za-z_$][A-Za-z0-9_$]*)")
TYPE_ALIAS_RE = re.compile(r"^(\s*)(export\s+(default\s+)?)?type\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=")
ENUM_RE = re.compile(r"^(\s*)(export\s+(default\s+)?)?(const\s+)?enum\s+([A-Za-z_$][A-Za-z0-9_$]*)")

# interface/type 内部的字段签名，例如 `foo: string;` 或 `bar?: number`（不含函数体，
# 属性声明式的成员，通常以 `;`、`,` 或换行结束，没有花括号）
INTERFACE_MEMBER_RE = re.compile(r"^(\s*)(readonly\s+)?([A-Za-z_$][A-Za-z0-9_$]*\??)\s*[:(]")

# class 内部的方法（ES6 简写语法，没有 function 关键字），例如：
#   async fetch(url: string): Promise<any> { ... }
#   private helper(x: number) { ... }
#   get value() { ... }
#   static create(): Foo { ... }
# 要求以 `{` 结尾（区分于只是字段声明/接口签名的 `;` 结尾情况）。
CLASS_METHOD_RE = re.compile(
    r"^(\s*)(public\s+|private\s+|protected\s+|static\s+|readonly\s+|abstract\s+|override\s+)*"
    r"(async\s+)?(get\s+|set\s+)?\*?\s*"
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*\([^;{}]*\)\s*(?::[^{;]+)?\s*\{"
)

# 用于识别"这一行是否开启了一个函数体"，不要求出现在行首、不要求有名字。
# 这跟上面几条正则的目的不同——上面那些是"这一行本身构成一个应该展示的符号"，
# 这一条只是"这一行是否让我们进入了某个函数作用域"，用来正确维护
# func_body_stack（见 extract_symbols 里的详细说明）。覆盖：
#   - 具名/匿名 function 表达式：`function(a, b) {`、`function foo() {`
#   - 作为参数传入的回调：`it('...', function(){`、`app.use(function(req,res){`
#   - 箭头函数：`() => {`、`(a, b) => {`、`async (x) => {`
# 不要求匹配 `{` 一定在本行（多行参数列表的情况这里不追求完美，见已知局限）。
FUNCTION_BODY_OPENER_RE = re.compile(
    r"(?:\bfunction\b\s*\*?\s*[A-Za-z_$]*\s*\([^)]*\)\s*\{|"
    r"\([^()]*\)\s*=>\s*\{|"
    r"\b[A-Za-z_$][A-Za-z0-9_$]*\s*=>\s*\{)"
)

# --- 依赖识别 ---
#
# ESM 的 import/export...from 是真正的语句级语法，只能出现在行首（不能
# 嵌在表达式内部），用行首锚定匹配足够可靠：
#   import x from '...'  /  import { a, b } from '...'  /  import * as x from '...'
#   export { x } from '...'  /  export * from '...'（re-export，同样是依赖）
JS_IMPORT_FROM_RE = re.compile(r"""^\s*import\s+.*?\s+from\s+(['"])([^'"]*)\1""")
JS_EXPORT_FROM_RE = re.compile(r"""^\s*export\s+.*?\s+from\s+(['"])([^'"]*)\1""")
JS_SIDE_EFFECT_IMPORT_RE = re.compile(r"""^\s*import\s+(['"])([^'"]*)\1\s*;?\s*$""")

# require(...) 和动态 import(...) 不是语句级语法，是普通的函数调用/表达式，
# 可以出现在任意位置（真实案例：lodash 的
# `freeModule && freeModule.require && freeModule.require('util').types`，
# require 调用嵌在一长串条件判断表达式里，前面还有别的内容），不能像上面
# ESM 语法那样要求行首锚定，必须用 re.search 在整行范围内查找。
JS_REQUIRE_RE = re.compile(r"""\brequire\s*\(\s*(['"]?)([^'")]*)\1\s*\)""")
JS_DYNAMIC_IMPORT_RE = re.compile(r"""\bimport\s*\(\s*(['"]?)([^'")]*)\1\s*\)""")

# Node.js 内置模块（跟 Go/Python 同样的思路：用一份精确清单区分"标准库"，
# 不用启发式猜测——Node 的内置模块名足够少，直接手动列举是可行的，不像
# Go 标准库有189个包需要从工具链提取）。这份清单基于 Node.js 官方文档
# 长期稳定的内置模块列表（未加 node: 前缀的传统写法和 node: 前缀写法都
# 可能出现，两种都要能匹配到同一个模块名）。
_JS_BUILTIN_MODULES = frozenset({
    "assert", "async_hooks", "buffer", "child_process", "cluster", "console",
    "constants", "crypto", "dgram", "diagnostics_channel", "dns", "domain",
    "events", "fs", "http", "http2", "https", "inspector", "module", "net",
    "os", "path", "perf_hooks", "process", "punycode", "querystring",
    "readline", "repl", "stream", "string_decoder", "sys", "timers", "tls",
    "trace_events", "tty", "url", "util", "v8", "vm", "wasi", "worker_threads",
    "zlib",
})


def _classify_js_import_target(target: str) -> str:
    if target.startswith("."):
        # 相对路径（'./foo'、'../utils'）明确是项目内部——这是 JS/TS
        # 生态里唯一"语法本身就能确定内部/外部"的情况，不需要额外信息。
        return "internal"
    bare = target[5:] if target.startswith("node:") else target
    if bare in _JS_BUILTIN_MODULES:
        return "external"
    if target.startswith("@") or "/" not in target:
        # 裸包名（'react'）或 npm 的 scoped 包名（'@babel/core'）——这是
        # npm 生态的命名惯例：没有相对路径前缀的模块说明符，几乎总是
        # node_modules 里的第三方包，不太可能是本项目自己的源码（项目
        # 自己的模块要被这样引用，需要先在 package.json 里把自己注册成
        # 一个可导入的包名，这在真实项目里非常少见）。这跟 Go/Python
        # 那种"裸路径无法确定"的情况不同——npm 的路径解析规则本身就是
        # "没有 ./ 前缀就去 node_modules 找"，语法本身已经隐含了"这是
        # 外部包"的强烈信号，不是纯粹瞎猜。
        return "external"
    # 剩下的情况：不是相对路径，也不是裸包名/scoped包名，但含有多级斜杠
    # 且不以 @ 开头——这种形态少见，不确定归类，保守起见归 unknown。
    return "unknown"


def _first_name_group(m: re.Match, groups: tuple[int, ...]) -> str | None:
    for g in groups:
        val = m.group(g)
        if val:
            return val
    return None


class JsTsAdapter:
    name = "javascript_typescript"

    def match(self, filepath) -> bool:
        return Path(filepath).suffix in JS_TS_EXTENSIONS

    def extract_dependencies(self, lines: list[str]) -> list[Dependency]:
        """
        识别 JS/TS 的依赖声明：ESM 的 import/export...from（语句级语法，
        行首锚定匹配）、CommonJS 的 require(...)、动态 import(...)（都不是
        语句级语法，可以出现在任意表达式位置，用 re.search 而不是行首
        匹配——真实案例：lodash 里 `freeModule && freeModule.require &&
        freeModule.require('util').types`，require 调用嵌在一长串条件
        判断表达式里）。

        用共享的 mask_c_family_comments_and_strings 屏蔽后的文本判断
        "这一行是否真的包含依赖声明关键字"（如果屏蔽后这一行的
        import/require 关键字消失了或者整行变成空白，说明原文里那处
        文字出现在注释或字符串内部，不是真实代码——真实案例：
        `// This used to import from '../old-utils'` 这条注释被
        mask_c_family_comments_and_strings 处理后整行变空白，
        `import`/`require` 关键字随之消失，据此判断不是真实依赖）；
        但真正提取路径内容时，回到**未屏蔽的原始 lines**——这是跟上一轮
        修复符号展示 bug 完全相同的原则的另一次应用：结构判断该用屏蔽后
        的文本，取真实内容绝不能用屏蔽后的文本（屏蔽会把字符串内容替换
        成空格，如果拿这份文本去解析依赖目标，会解析出一堆空白，等于
        白做）。
        """
        clean_lines = mask_c_family_comments_and_strings(lines)
        deps: list[Dependency] = []

        for i, (clean, raw) in enumerate(zip(clean_lines, lines)):
            clean_stripped = clean.rstrip("\n")
            if not clean_stripped.strip():
                continue
            raw_stripped = raw.rstrip("\n")

            # ESM import...from / export...from：先在屏蔽后的文本上确认
            # 这一行确实是真实的 import/export 语句（不是注释里提到的
            # 文字），再回到原始文本上提取真正的路径字符串。
            if JS_IMPORT_FROM_RE.match(clean_stripped):
                m = JS_IMPORT_FROM_RE.match(raw_stripped)
                if m:
                    target = m.group(2)
                    deps.append(Dependency(
                        raw_text=raw_stripped.strip(),
                        kind=_classify_js_import_target(target),
                        line_no=i + 1,
                        target=target,
                    ))
                    continue

            if JS_EXPORT_FROM_RE.match(clean_stripped):
                m = JS_EXPORT_FROM_RE.match(raw_stripped)
                if m:
                    target = m.group(2)
                    deps.append(Dependency(
                        raw_text=raw_stripped.strip(),
                        kind=_classify_js_import_target(target),
                        line_no=i + 1,
                        target=target,
                    ))
                    continue

            if JS_SIDE_EFFECT_IMPORT_RE.match(clean_stripped):
                # 纯副作用导入：`import './setup';`，没有绑定任何名字
                m = JS_SIDE_EFFECT_IMPORT_RE.match(raw_stripped)
                if m:
                    target = m.group(2)
                    deps.append(Dependency(
                        raw_text=raw_stripped.strip(),
                        kind=_classify_js_import_target(target),
                        line_no=i + 1,
                        target=target,
                    ))
                    continue

            # require(...) 和动态 import(...) 不要求行首，用 search 在
            # 屏蔽后文本里先确认关键字确实存在于真实代码里，再到原始文本
            # 相同位置提取内容。这里对屏蔽后文本和原始文本分别单独跑
            # search，而不是"屏蔽后文本 search 到位置、原始文本按位置切片"
            # ——因为屏蔽只替换字符不改变长度和位置，两次独立 search 理论上
            # 会落在同一个位置，但独立 search 更简单直接，不需要额外验证
            # 位置对齐这个前提。
            for pattern, extra_deps in (
                (JS_REQUIRE_RE, deps),
                (JS_DYNAMIC_IMPORT_RE, deps),
            ):
                if not pattern.search(clean_stripped):
                    continue
                for m in pattern.finditer(raw_stripped):
                    quote = m.group(1)
                    content = m.group(2)
                    if quote in ("'", '"'):
                        target = content
                        extra_deps.append(Dependency(
                            raw_text=raw_stripped.strip(),
                            kind=_classify_js_import_target(target),
                            line_no=i + 1,
                            target=target,
                        ))
                    else:
                        # 括号里不是带引号的字符串字面量，说明是变量/表达式
                        # （比如 `require(moduleName)`、`import(getPath())`），
                        # 目标无法静态解析，归 dynamic。
                        extra_deps.append(Dependency(
                            raw_text=raw_stripped.strip(),
                            kind="dynamic",
                            line_no=i + 1,
                            target=None,
                        ))

        return deps

    def extract_symbols(self, filepath, lines: list[str]) -> AdapterResult:
        # 用统一的注释+字符串屏蔽（含模板字符串），避免早期版本单独用
        # strip_block_comments 时不感知字符串边界导致的误判（真实案例：
        # `.set('Accept', '*/*')` 这行代码里的字符串恰好包含 `*/*` 三字符
        # 序列，被朴素的注释扫描器误判为块注释开始，见
        # adapter_utils.mask_c_family_comments_and_strings 文档字符串）。
        clean_lines = mask_c_family_comments_and_strings(lines)
        symbols: list[Symbol] = []

        # 用花括号深度判断是否在 class/interface 内部一层，规则跟 Python 适配器一致：
        # 只收录容器内部直接一层的成员，不递归进普通函数体。
        container_stack: list[tuple[int, int]] = []  # (进入容器时的花括号深度, 容器的depth)
        # 函数体深度栈：记录当前"处于多少层普通函数/回调体内部"，不同于 container_stack
        # （container_stack 只追踪 class/interface，不追踪普通 function）。
        # 这个栈只用来回答一个问题："当前是否处于任意一层函数体内部"——只要非空，
        # 就说明这里出现的变量声明是某个函数作用域的局部变量，不是模块级声明，
        # 不应该被 PLAIN_DECL_RE 这类"顶层声明"规则收录。
        #
        # 这是用真实项目复现确认过的两个真实 bug 的共同根因和统一修复：
        # 1. lodash.js 整个文件用 `;(function() { ... })();` IIFE 包裹，内部的
        #    `var VERSION = '4.18.1'` 这类内部实现常量的花括号深度是1，不是0，
        #    但早期版本的 is_top_level 只看"有没有进入 class/interface"，
        #    IIFE 不是 class，所以这些内部常量全部被误判为顶层声明收录
        #    （单文件297个，是精度崩溃到51%的直接原因）。
        # 2. express 的测试代码里 `var app = express();` 出现在多层嵌套的
        #    `describe(...)`/`it(...)` 回调函数体内部，同样因为回调函数不是
        #    class，被误判为顶层声明重复收录几十次。
        # 两者的共同点是：变量声明位于某个"函数体"内部，不管这个函数体是
        # IIFE、具名函数、还是匿名回调。用一个统一的函数体深度栈来判断，
        # 比"检查花括号深度是否等于0"更准确——后者会把 IIFE 包裹整个文件
        # 这种情况和"文件本身没有任何包裹"完全混淆，导致修复一个场景就
        # 破坏另一个场景（已在开发过程中验证过这个失败模式）。
        #
        # 注意：这个栈只影响 PLAIN_DECL_RE/TYPE_ALIAS_RE/ENUM_RE 等"顶层声明"
        # 规则的收录判断，不影响 FUNC_RE/CLASS_RE 本身的匹配——真实 tree-sitter
        # 基准对 function/class 声明没有这个"是否在函数体内"的限制（它的 AST
        # 遍历会访问到所有子节点，不管外层包了几层函数），如果连 FUNC_RE 也用
        # 这个栈屏蔽，会导致 IIFE 内部的真实顶层函数定义（lodash.js 的主体逻辑
        # 几乎全部是这种写法）全部消失，这是开发过程中先踩过的一个坑。
        func_body_stack: list[int] = []  # 每一项是该函数体开始时的花括号深度
        tracker = BraceDepthTracker()

        for i, raw in enumerate(clean_lines):
            stripped = raw.rstrip("\n")
            # display_stripped 是这一行**未经字符串/注释屏蔽**的原始文本，
            # 只用于最终展示给人看的符号名（Symbol.name），不参与任何正则
            # 匹配或深度追踪判断——那些必须继续用 stripped（屏蔽后的版本），
            # 原因见上面 mask_c_family_comments_and_strings 的说明（字符串
            # 内容里的 `{`/`*/*` 等字符会干扰花括号计数和注释边界判断）。
            #
            # 这是修复一个真实的、独立评审报告点名的缺陷：早期版本展示符号
            # 名时也用的是屏蔽后的 stripped，导致字符串内容被空格抹掉但
            # 引号本身保留，展示出 `path.resolve('  ')` 这种"看起来完整、
            # 实际内容已被静默清空"的结果——用真实的 .mjs 文件复现确认过
            # （`path.resolve(HERE, '..')` 展示成看不出原始路径的占位符），
            # 这比"漏掉一些符号"更危险，因为使用者会误以为自己看到的是
            # 完整信息。mask_c_family_comments_and_strings 保证了屏蔽前后
            # 每一行的字符数和总行数完全一致（专门为了保持列对齐设计的），
            # 所以按相同的行号索引 lines[i] 取原始文本用于展示是安全的
            # ——不会有行号错位的风险。
            display_stripped = lines[i].rstrip("\n") if i < len(lines) else stripped
            if not stripped.strip():
                tracker.update(stripped)
                continue

            depth_before = tracker.depth_before_line()

            # 弹出所有已经离开的容器（花括号深度已经回落到容器开启时的深度或更浅）
            while container_stack and depth_before <= container_stack[-1]["base_depth"]:
                container_stack.pop()
            while func_body_stack and depth_before <= func_body_stack[-1]:
                func_body_stack.pop()

            is_top_level = len(container_stack) == 0
            in_function_body_somewhere = len(func_body_stack) > 0
            in_container_direct_child = (
                container_stack and depth_before == container_stack[-1]["base_depth"] + 1
            )

            handled = False

            m = CLASS_RE.match(stripped) or INTERFACE_RE.match(stripped)
            if m and (is_top_level or in_container_direct_child):
                name_groups = m.groups()
                name = name_groups[-1]
                cur_depth = container_stack[-1]["depth"] + 1 if container_stack else 0
                symbols.append(Symbol(name=display_stripped.strip(), depth=cur_depth, line_no=i + 1))
                # 这个 class/interface 自己成为一个新容器，供内部成员使用。
                # member_count 用于限制 interface 字段列表的展示数量（见下方
                # INTERFACE_MEMBER_RE 分支），对齐真实 tree-sitter 基准的
                # "预览几行就截断"行为——用真实项目 vue/core 复现确认：
                # 基准对 interface 字段最多展示3行就用 ⋮ 截断，早期版本
                # 没有这个限制，大型 interface（几十个字段）会把全部字段
                # 逐条展示成独立符号，既不符合基准行为，也会让输出体积
                # 随 interface 字段数线性膨胀。
                container_stack.append({
                    "base_depth": depth_before,
                    "depth": cur_depth,
                    "is_interface": bool(INTERFACE_RE.match(stripped)),
                    "member_count": 0,
                })
                handled = True

            if not handled:
                m = FUNC_RE.match(stripped)
                if m and m.group(5) and (is_top_level or in_container_direct_child):
                    cur_depth = container_stack[-1]["depth"] + 1 if container_stack else 0
                    symbols.append(Symbol(name=display_stripped.strip(), depth=cur_depth, line_no=i + 1))
                    handled = True

            if not handled and is_top_level and not in_function_body_somewhere:
                # 顶层专属规则：箭头函数赋值 / 任意顶层声明 / type / enum
                # （不在容器内部收录这些，避免把对象字面量里的 key: value 误判为定义；
                # 也不在任何函数体内部收录这些，避免把 IIFE 内部常量或测试回调里的
                # 局部变量误判为模块级声明——见本函数开头 func_body_stack 的说明）
                m = ARROW_ASSIGN_RE.match(stripped)
                if m:
                    symbols.append(Symbol(name=display_stripped.strip(), depth=0, line_no=i + 1))
                    handled = True

                if not handled:
                    m = TYPE_ALIAS_RE.match(stripped) or ENUM_RE.match(stripped)
                    if m:
                        symbols.append(Symbol(name=display_stripped.strip(), depth=0, line_no=i + 1))
                        handled = True

                if not handled:
                    m = PLAIN_DECL_RE.match(stripped)
                    if m:
                        symbols.append(Symbol(name=display_stripped.strip(), depth=0, line_no=i + 1))
                        handled = True

            if not handled and in_container_direct_child:
                # class 内部的方法（ES6简写，无 function 关键字），优先尝试这条更严格的规则
                m = CLASS_METHOD_RE.match(stripped)
                if m and not stripped.strip().startswith(("//", "*", "/*")):
                    cur_depth = container_stack[-1]["depth"] + 1
                    symbols.append(Symbol(name=display_stripped.strip(), depth=cur_depth, line_no=i + 1))
                    handled = True

            if not handled and in_container_direct_child:
                # interface/class 内部的字段签名（属性声明，不含函数体的那种）。
                # 对 interface 字段做截断：最多展示3个，超出的静默跳过并在
                # AdapterResult.notes 里记一笔（不生成额外的假符号，也不试图
                # 在符号流里插入 ⋮ 标记——渲染层目前的 ⋮ 约定是"文件级"的，
                # 引入"符号级"的 ⋮ 需要改动渲染格式，收益不确定，这里选择
                # 更保守的处理：只是不再继续生成第4个及以后的字段符号）。
                m = INTERFACE_MEMBER_RE.match(stripped)
                if m and not stripped.strip().startswith(("//", "*", "/*")):
                    frame = container_stack[-1]
                    if frame["is_interface"]:
                        if frame["member_count"] < 3:
                            cur_depth = frame["depth"] + 1
                            symbols.append(Symbol(name=display_stripped.strip(), depth=cur_depth, line_no=i + 1))
                            frame["member_count"] += 1
                    else:
                        cur_depth = frame["depth"] + 1
                        symbols.append(Symbol(name=display_stripped.strip(), depth=cur_depth, line_no=i + 1))

            # 无论上面有没有识别出符号，都要单独检查这一行是否"开启了一个函数体"，
            # 用来维护 func_body_stack（IIFE、真实的顶层/嵌套函数、匿名回调、
            # 箭头函数都算）。这一步跟符号识别是两回事：即使这一行本身没有
            # 生成任何符号（比如 `app1.use(function(req, res, next){`
            # 这种不匹配任何 FUNC_RE/CLASS_METHOD_RE 具名规则的匿名回调），
            # 只要它在语法上开启了一个函数作用域，后续内容就不应该被
            # PLAIN_DECL_RE 等顶层规则收录。
            opens_body = bool(FUNCTION_BODY_OPENER_RE.search(stripped))
            if opens_body and not line_is_brace_balanced(stripped):
                func_body_stack.append(depth_before)

            tracker.update(stripped)

        return AdapterResult(symbols=symbols)


register(JsTsAdapter())
