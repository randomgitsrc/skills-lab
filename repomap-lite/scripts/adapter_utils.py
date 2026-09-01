#!/usr/bin/env python3
"""
adapter_utils.py — 各语言适配器共用的小工具。

把"缩进计算""括号平衡跳过多行字符串"等通用逻辑收在这里，避免每个适配器
（Python/JS/Go/C系语言...）重复写一遍缩进栈或字符串跳过逻辑。
"""

from __future__ import annotations

import re


def indent_of(line: str) -> int:
    """计算前导空白宽度，制表符按 4 计（启发式，不追求 100% 精确）。"""
    n = 0
    for ch in line:
        if ch == " ":
            n += 1
        elif ch == "\t":
            n += 4
        else:
            break
    return n


class IndentStack:
    """
    通用缩进栈：给定一行的缩进宽度，返回它应该处于的嵌套深度（depth），
    并维护栈内部状态。约 10 行代码，但几乎每个用缩进表达嵌套的语言
    （Python/JS 对象字面量/YAML风格配置等）都要用到，抽出来避免重复实现。

    用法：
        stack = IndentStack()
        for line in lines:
            indent = indent_of(line)
            depth = stack.push(indent)
    """

    def __init__(self) -> None:
        self._stack: list[tuple[int, int]] = []  # (indent_width, depth)

    def push(self, indent: int) -> int:
        while self._stack and self._stack[-1][0] >= indent:
            self._stack.pop()
        depth = self._stack[-1][1] + 1 if self._stack else 0
        self._stack.append((indent, depth))
        return depth

    def reset(self) -> None:
        self._stack.clear()


class BraceDepthTracker:
    """
    通用花括号深度追踪器，用于 C 系语言（C#/Java/C++/JS）按 `{`/`}` 计数
    判断嵌套层级，而不是依赖缩进（因为这些语言的缩进不是语法要求，
    真实代码风格千差万别，用缩进栈会比 Python 更不可靠）。

    用法：每处理一行，先算这一行"生效时"的深度（即定义行本身所在的深度，
    在遇到该行的左花括号之前），再更新累计深度。
    """

    def __init__(self) -> None:
        self.depth = 0

    def depth_before_line(self) -> int:
        return self.depth

    def update(self, line: str) -> None:
        # 简化处理：不做字符串/注释内花括号的精确排除（那需要完整词法分析），
        # 用一个常见的启发式：忽略被 // 行注释截断之后的部分。
        code_part = strip_line_comment_naive(line)
        self.depth += code_part.count("{")
        self.depth -= code_part.count("}")
        if self.depth < 0:
            self.depth = 0


def strip_line_comment_naive(line: str, marker: str = "//") -> str:
    """
    去掉一行里 `//` 之后的内容（启发式，不处理 `//` 出现在字符串字面量内部的情况，
    例如 `"http://example.com"` 会被误截断）。用于花括号计数这种粗粒度场景，
    偶尔的误差可以接受；不用于精确的符号名提取。
    """
    idx = line.find(marker)
    if idx == -1:
        return line
    # 粗略排除掉字符串内的 // ：如果 // 前面有奇数个未转义引号，大概率在字符串里，
    # 这个判断本身很粗糙，只是聊胜于无。
    prefix = line[:idx]
    if prefix.count('"') % 2 == 1:
        return line
    return prefix


def strip_block_comments(lines: list[str]) -> list[str]:
    """
    去掉 /* ... */ 风格的块注释（跨行也处理），返回同样行数的新列表
    （被注释覆盖的部分替换为空格，保持行号和大致列位置不变，
    避免打乱后续基于行号的处理）。

    这是启发式处理：不处理注释符号出现在字符串字面量内部的情况
    （例如某字符串常量里恰好写死了 "/*"），跟真实编译器的词法分析器
    比起来会有边界误差，但能覆盖绝大多数真实代码里的用法。
    """
    result = []
    in_block = False
    for line in lines:
        out_chars = []
        i = 0
        n = len(line)
        while i < n:
            if in_block:
                if line[i : i + 2] == "*/":
                    out_chars.append("  ")
                    i += 2
                    in_block = False
                else:
                    out_chars.append(" " if line[i] != "\n" else "\n")
                    i += 1
            else:
                if line[i : i + 2] == "/*":
                    out_chars.append("  ")
                    i += 2
                    in_block = True
                else:
                    out_chars.append(line[i])
                    i += 1
        result.append("".join(out_chars))
    return result


TRIPLE_QUOTE_OPEN_RE = re.compile(r'("""|\'\'\')')


def skip_python_style_triple_quoted_strings(lines: list[str]) -> list[bool]:
    """
    返回一个跟 lines 等长的布尔列表，标记每一行是否处于三引号字符串内部
    （用于 Python 提取器跳过字符串内的伪代码）。

    这是从 repomap_lite 早期版本里抽出来的逻辑，已经过真实项目验证
    （见 references/known_limitations.md 里记录的三引号误报修复过程）。
    """
    in_triple = [False] * len(lines)
    active = False
    delim = None
    for idx, raw in enumerate(lines):
        stripped = raw.rstrip("\n")
        if active:
            in_triple[idx] = True
            if delim in stripped:
                active = False
                delim = None
            continue
        m = TRIPLE_QUOTE_OPEN_RE.search(stripped)
        if m:
            d = m.group(1)
            if stripped.count(d) % 2 == 1:
                active = True
                delim = d
                # 开启这一行本身不算"内部"，因为可能是 `X = """` 这种，
                # 该行前半部分仍是有效代码；只标记后续行。
    return in_triple


def strip_raw_string_literals_naive(lines: list[str]) -> list[str]:
    """
    针对 Go/C++ 等语言里的反引号(`...`)或 R"(...)"" 风格原始字符串字面量，
    做一个粗糙的单行/跨行跳过处理：把反引号包裹的内容替换为空格。

    已知局限：不处理 C++11 的 R"delim(...)delim" 自定义分隔符语法，
    只处理最常见的 Go 反引号写法。这是刻意的取舍——完整支持需要真正的
    词法分析器，超出正则+启发式方案的合理范围，在 references 里如实记录。
    """
    result = []
    in_raw = False
    for line in lines:
        out = []
        i = 0
        n = len(line)
        while i < n:
            ch = line[i]
            if ch == "`":
                in_raw = not in_raw
                out.append(" ")
            elif in_raw:
                out.append(" " if ch != "\n" else "\n")
            else:
                out.append(ch)
            i += 1
        result.append("".join(out))
    return result


def mask_c_family_comments_and_strings(lines: list[str]) -> list[str]:
    """
    单次线性扫描，同时处理 C 系语言（含 JavaScript/TypeScript，语法上同属
    "C风格花括号语言"家族）的注释（`//`、`/* */`）和字符串/字符字面量
    （`"..."`、C# 的 `@"..."`、`'...'`、JS/TS 的模板字符串 `` `...` ``），
    按字符实际出现的顺序正确判断"这段文本当前处于哪种语法环境"，输出把
    注释和字符串内容都替换为空格、只保留代码结构本身的版本。

    为什么不能分两阶段做（先剥注释再屏蔽字符串，或反过来）：
    - 如果先屏蔽字符串再剥注释：英文注释里大量出现的、跟字符串语法无关的
      撇号（比如 `/* the caller's buffer */`、`/* what's happening */`）
      会被字符串屏蔽逻辑误判为字符字面量的开始，从该撇号到下一个单引号
      之间的内容全部被当作"字符字面量内部"处理。已用真实项目 Redis 复现
      确认这个 bug 的具体触发路径：`src/cluster.c` 第54行注释
      `Hash what's between braces. */` 里，`'{...}'` 这个真实的字符字面量
      示例先被正确处理，但紧接着 `what's` 里的撇号又触发了第二次误判，
      吞掉了这一行剩余部分——包括这个注释自己的 `*/` 收尾符——导致
      后续的注释剥离阶段找不到这个注释的结束标记，把该文件从这里往后的
      全部真实代码都误判为"仍在多行注释内部"，全部清空，包括其中的花括号。
      这个 bug 的后果比它单独听起来更严重：一旦某个注释的收尾符被字符串
      屏蔽阶段意外吞掉，受影响范围是从触发点到**文件末尾**，不是局部错误。
    - 如果先剥注释再屏蔽字符串：注释内容里恰好写死的引号字符
      （比如注释里举例说明字符串格式）会被注释剥离逻辑正确忽略掉
      （不会有问题），但如果字符串内容里恰好写死了 `/*` 或 `//`
      （测试代码构造带注释语法的字符串样例时常见），会被注释剥离逻辑
      误判为真实注释的开始，同样导致后续代码被误吞。

    两种顺序都有各自的失败模式，根本原因是"注释"和"字符串"的语法边界
    互相依赖对方的状态才能正确判断（一个字符在不在注释里，取决于它是否
    在字符串里；反过来也一样），分两个独立阶段处理天然无法两者兼顾。
    这个函数用一次遍历、一套状态机同时跟踪"当前是否在字符串/字符字面量内"
    和"当前是否在注释内"，按字符实际出现顺序正确判断优先级，替代了早期
    版本里 `mask_c_family_string_literals()` + `strip_block_comments()`
    两阶段流水线的用法（该函数仍保留，但生产代码路径已改用这个版本）。

    这同一个函数也修复了 JS/TS 适配器早期使用的独立 `strip_block_comments()`
    的一个真实 bug：那个函数完全不感知字符串边界，纯粹按字符对扫描
    `/*`/`*/`，导致任何字符串内容里恰好包含 `*/*` 这个三字符序列的代码
    （例如 `.set('Accept', '*/*')`，真实项目 express 的测试代码里
    实际出现过）会被误判为"这里开启了一个块注释"——因为 `*/*` 这个序列
    从中间的 `/` 往后读恰好构成 `/*`。一旦误判为进入注释状态，从这里到
    文件里下一个真正的 `*/` 之间的全部内容（可能是几十行真实代码）都会
    被当作注释清空，引发和 C# 逐字字符串同一类的连锁失效。

    已知局限：
    - C++ 的 R"delim(...)delim" 自定义分隔符原始字符串不处理
    - 不处理反斜杠续行跨越注释/字符串边界的极端情况
    """
    full_text = "".join(lines)
    out_chars: list[str] = []
    i = 0
    n = len(full_text)

    while i < n:
        two = full_text[i : i + 2]

        # 行注释：// 到本行结尾（不吃掉换行符本身，保持行结构）
        if two == "//":
            while i < n and full_text[i] != "\n":
                out_chars.append(" ")
                i += 1
            continue

        # 块注释：/* ... */，可跨行，换行符本身要保留（不能替换成空格，
        # 否则会把多行注释拍扁成一行，打乱后续基于行号的处理）
        if two == "/*":
            out_chars.append("  ")
            i += 2
            while i < n:
                if full_text[i : i + 2] == "*/":
                    out_chars.append("  ")
                    i += 2
                    break
                out_chars.append(" " if full_text[i] != "\n" else "\n")
                i += 1
            continue

        ch = full_text[i]

        # C# 逐字字符串 @"..."，用 "" 表示字面双引号，可跨行
        if ch == "@" and i + 1 < n and full_text[i + 1] == '"':
            out_chars.append("@\"")
            i += 2
            while i < n:
                if full_text[i] == '"':
                    if i + 1 < n and full_text[i + 1] == '"':
                        out_chars.append("  ")
                        i += 2
                        continue
                    out_chars.append('"')
                    i += 1
                    break
                out_chars.append(" " if full_text[i] != "\n" else "\n")
                i += 1
            continue

        # 普通双引号字符串 "..."，\" 转义不视为结束，不允许跨行
        # （未闭合视为源码本身的问题或误判，保守地在换行处强制截断，
        # 避免一个误判的起始引号吞掉后面几十行真实代码）
        if ch == '"':
            out_chars.append('"')
            i += 1
            while i < n:
                if full_text[i] == "\\" and i + 1 < n and full_text[i + 1] != "\n":
                    out_chars.append("  ")
                    i += 2
                    continue
                if full_text[i] == '"':
                    out_chars.append('"')
                    i += 1
                    break
                if full_text[i] == "\n":
                    out_chars.append("\n")
                    i += 1
                    break
                out_chars.append(" ")
                i += 1
            continue

        # 单字符字面量 'x' / '\n'（C系语言）或 JS 的单引号字符串（可以任意长），
        # 两种语义不同但对"要不要跨行、遇到反斜杠转义怎么处理"的屏蔽逻辑是
        #一样的，用同一段代码处理，不额外区分调用方是哪种语言。
        if ch == "'":
            out_chars.append("'")
            i += 1
            while i < n:
                if full_text[i] == "\\" and i + 1 < n and full_text[i + 1] != "\n":
                    out_chars.append("  ")
                    i += 2
                    continue
                if full_text[i] == "'":
                    out_chars.append("'")
                    i += 1
                    break
                if full_text[i] == "\n":
                    out_chars.append("\n")
                    i += 1
                    break
                out_chars.append(" ")
                i += 1
            continue

        # JS/TS 模板字符串 `...`，可跨行，内部可能有 `${expr}` 插值表达式。
        # 这里采取最简单安全的策略：把整个模板字符串（包括内部的 `${...}`）
        # 一并当作字符串内容屏蔽掉，不尝试识别插值表达式内部可能出现的
        # 真实代码结构（比如 `${ items.map(x => ({ x })) }` 这种插值里
        # 完全可能藏着花括号）。这是刻意的保守选择——模板字符串本身很少是
        # 我们要识别的"定义"，但字符串里任意写死的花括号/引号字符（尤其是
        # 用模板字符串拼 HTML/JSON 片段时极其常见）如果不整体屏蔽，
        # 会重演 C# 逐字字符串同样的深度追踪错位问题。
        # 已知局限：如果插值表达式内部真的定义了函数/类（极端罕见的写法），
        # 会被一并屏蔽而不是被识别，可接受。
        if ch == "`":
            out_chars.append("`")
            i += 1
            while i < n:
                if full_text[i] == "\\" and i + 1 < n:
                    out_chars.append("  ")
                    i += 2
                    continue
                if full_text[i] == "`":
                    out_chars.append("`")
                    i += 1
                    break
                out_chars.append(" " if full_text[i] != "\n" else "\n")
                i += 1
            continue

        out_chars.append(ch)
        i += 1

    masked_text = "".join(out_chars)
    return masked_text.splitlines(keepends=True)


def line_is_brace_balanced(line: str) -> bool:
    """
    判断一行代码里的花括号是否"当场配平"——即这一行里至少出现过一次 `{`，
    且 `{` 和 `}` 的数量相等（典型场景：`void Foo() { }` 或 `() => { return 1; }`
    全部写在同一行）。这种情况下函数体/块在这一行内就开始并结束，不会有更深层的
    后续内容需要屏蔽；如果误判为"还需要继续屏蔽直到深度回落"，会因为深度
    从未真正超过基线而导致屏蔽帧永远卡在栈里弹不出去。

    这是 C 系语言适配器和 JS/TS 适配器共用的判断（两边都用花括号+帧栈追踪
    函数体边界，各自都独立踩到过"同一行内配平的函数体让屏蔽帧永久卡死"
    这个同一类 bug，抽到这里避免以后新增第三个花括号语言适配器时再踩一遍）。
    """
    opens = line.count("{")
    closes = line.count("}")
    return opens > 0 and opens == closes


def mask_c_family_string_literals(lines: list[str]) -> list[str]:
    """
    已弃用于生产路径：这个函数单独存在时，字符串/字符字面量的屏蔽发生在
    注释剥离**之前**（如果调用方接着自己调用 strip_block_comments），
    会被英文注释里大量存在的、跟字符串语法无关的撇号（比如
    `/* the caller's buffer */`）误判为字符字面量的开始，进而把从这个
    撇号到文件里下一个单引号之间的内容都当作字符字面量屏蔽掉。更糟的是，
    如果这中间恰好还有一个"看似完整"的字符字面量（比如注释里举例用的
    `'{...}'`），会让状态机提前"闭合"，然后被同一行后面的另一个撇号
    （比如 "what's"）重新触发，吞掉这一行剩余部分——如果这行恰好是
    某个块注释的收尾行，连注释的 `*/` 都会被一起吞掉，导致后续剥离
    注释的阶段找不到这个注释的结束标记，把文件从这里往后全部误判为
    仍在注释内部，全部清空。用真实项目 Redis 的 `src/cluster.c` 复现
    确认过这个具体链条（第54行注释 "Hash what's between braces. */"）。

    生产路径已改用 `mask_c_family_comments_and_strings()`，在同一次线性
    扫描里同时处理注释和字符串。本函数保留仅供参考/历史对照，不建议调用。
    """
    full_text = "".join(lines)
    out_chars: list[str] = []
    i = 0
    n = len(full_text)

    while i < n:
        ch = full_text[i]

        if ch == "@" and i + 1 < n and full_text[i + 1] == '"':
            out_chars.append("@\"")
            i += 2
            while i < n:
                if full_text[i] == '"':
                    if i + 1 < n and full_text[i + 1] == '"':
                        out_chars.append("  ")
                        i += 2
                        continue
                    out_chars.append('"')
                    i += 1
                    break
                out_chars.append(" " if full_text[i] != "\n" else "\n")
                i += 1
            continue

        if ch == '"':
            out_chars.append('"')
            i += 1
            while i < n:
                if full_text[i] == "\\" and i + 1 < n and full_text[i + 1] != "\n":
                    out_chars.append("  ")
                    i += 2
                    continue
                if full_text[i] == '"':
                    out_chars.append('"')
                    i += 1
                    break
                if full_text[i] == "\n":
                    out_chars.append("\n")
                    i += 1
                    break
                out_chars.append(" ")
                i += 1
            continue

        if ch == "'":
            out_chars.append("'")
            i += 1
            while i < n:
                if full_text[i] == "\\" and i + 1 < n and full_text[i + 1] != "\n":
                    out_chars.append("  ")
                    i += 2
                    continue
                if full_text[i] == "'":
                    out_chars.append("'")
                    i += 1
                    break
                if full_text[i] == "\n":
                    out_chars.append("\n")
                    i += 1
                    break
                out_chars.append(" ")
                i += 1
            continue

        out_chars.append(ch)
        i += 1

    masked_text = "".join(out_chars)
    return masked_text.splitlines(keepends=True)


# 每个适配器需要检查的"文件头部标记"的默认扫描窗口。真实的代码生成器
# （protoc、mockgen、各类脚手架工具）几乎总是把"本文件自动生成"的声明
# 放在文件最开头几行，不需要全文扫描——这既是性能考量，也是准确性考量：
# 全文扫描会增加"手写文件某处碰巧提到 generated 这个词"的误判风险。
DEFAULT_GENERATED_MARKER_CHECK_LINES = 5


def matches_generated_file_markers(
    lines: list[str],
    markers: tuple,
    check_lines: int = DEFAULT_GENERATED_MARKER_CHECK_LINES,
) -> bool:
    r"""
    通用的"文件头部是否包含自动生成标记"检查函数，供各语言适配器的
    `is_generated()` 实现调用，避免每个适配器各写一遍相同的"取前N行、
    逐条模式匹配"循环。

    每种语言/生态自己的标记正则由调用方（各适配器）提供，这个函数本身
    不内置任何具体语言的标记知识——这是有意的设计：语言相关的标记格式
    是每个适配器自己的知识范围，这个函数只负责通用的扫描机制。

    用法示例（在某个适配器里）：
        _GENERATED_MARKERS = (
            re.compile(r"^\s*//\s*Code generated .* DO NOT EDIT\.\s*$"),
        )

        def is_generated(self, lines):
            return matches_generated_file_markers(lines, _GENERATED_MARKERS)
    """
    for line in lines[:check_lines]:
        for pattern in markers:
            if pattern.match(line):
                return True
    return False


# ---------------------------------------------------------------------------
# 依赖识别的共享判断逻辑
# ---------------------------------------------------------------------------
# 跨语言通用的一个判断："这个 import/require/include 语句的目标部分，是不是
# 一个能直接读出内容的字符串字面量"——是的话可以归为 internal/external（能
# 解析出具体目标），不是（是变量/表达式）的话必须归为 dynamic（见
# adapter_base.py 里 Dependency.kind 的详细说明，为什么不能瞎猜目标）。
#
# 用一个共享的"提取一对引号之间内容"的函数，而不是让每个适配器各写一遍
# 引号匹配正则，理由很直接：这个判断本身不含任何语言特定的知识（不管是
# Python 的 import 还是 JS 的 require，"参数是不是一个带引号的字符串"这个
# 问题的答案方式是一样的），属于真正通用的机制层，跟 mask_c_family_
# comments_and_strings 是同一个"字符串边界判断"问题在更小范围内的复用。
_QUOTED_LITERAL_RE = re.compile(r'''^\s*['"]([^'"]*)['"]\s*$''')


def extract_quoted_literal(text: str) -> str | None:
    """
    如果 text（去除首尾空白后）恰好是一个被单引号或双引号包裹的字符串
    字面量，返回引号内的内容；否则返回 None（说明这是变量/表达式/其他
    任何不是简单字符串字面量的东西，调用方应该把对应的依赖归为
    kind="dynamic"，不要尝试进一步解析）。

    故意只处理"整个 text 就是一个字面量"这种最简单、最没有歧义的情况，
    不处理字符串拼接（`'a' + 'b'`）、模板字符串插值（`` `${x}/foo` ``）
    这类"部分是字面量、部分不是"的混合情况——这些情况的目标本质上仍然
    是运行时才能确定的（除非真的对表达式求值），强行拼出一个"看起来像"
    的字符串反而是本文件反复强调要避免的"编一个可能错的答案"。
    """
    m = _QUOTED_LITERAL_RE.match(text)
    return m.group(1) if m else None
