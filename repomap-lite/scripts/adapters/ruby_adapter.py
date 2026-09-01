#!/usr/bin/env python3
"""
ruby_adapter.py — Ruby 语言适配器。

Ruby 的嵌套结构既不是花括号（C系语言），也不是缩进本身语法意义
（Python），而是**关键字配对**：`def`/`class`/`module`/`if`/`unless`/
`while`/`until`/`for`/`case`/`begin`/`do` 这些关键字开启一个块，用
`end` 关键字收尾，配对关系靠"栈"而不是任何空白/符号来维护。这是本项目
目前支持的语言里第三种不同的嵌套范式（花括号 / 缩进 / end关键字配对），
不能复用 BraceDepthTracker 或 IndentStack，需要专门的"end 配对深度追踪"。

Ruby 特有的解析陷阱，本适配器需要正确处理：
- **单行修饰符 if/unless/while/until 不需要 end**：`puts "x" if cond` 是
  一条完整语句，不开启块；只有 `if cond\n  ...\nend` 这种块形式才需要
  配对的 end。区分标准：如果 if/unless/while/until 出现在行首（可能带
  缩进），就是块形式；如果出现在其他内容之后（行内修饰符位置），就不是。
- **单行定义**（`class Foo; end`、`def foo; end`）：开启和收尾在同一行，
  不需要压入等待配对的帧——这跟 C 系语言"同一行内花括号配平"是同一类
  问题的 Ruby 版本，处理方式类似：检测同一行内是否已经配平。
- **`do...end` 块**（用于代码块/迭代器，如 `[1,2].each do |x| ... end`）
  也需要 end 配对，但不是"定义"，不应该生成符号，只是深度追踪需要感知它，
  否则后面的真实定义会被误判嵌套层级。
- **字符串/注释里的关键字**（比如字符串字面量里恰好写了 "end" 这个词）
  必须被正确屏蔽，否则会打乱 end 配对计数——复用本项目已经验证过的
  "注释和字符串必须在同一次扫描里统一处理"这条架构经验，为 Ruby 写一个
  专属的屏蟒函数（Ruby 的字符串语法：单引号/双引号/`%w[]` 数组字面量/
  `=begin...=end` 块注释，跟已支持语言都不完全一样）。

识别范围：
- `def`（含 `self.method_name` 类方法定义）
- `class`（含 `class Foo < Bar` 继承语法）
- `module`

已知局限：
- `attr_accessor`/`attr_reader`/`attr_writer` 生成的隐式方法不识别
  （它们不是 def，是方法调用，真实基准大概率也不会展示这些）
- Ruby 的 `%w[]`/`%i[]` 等百分号字面量语法只做基础屏蔽，不追求完整覆盖
  所有百分号字面量变体（`%q`、`%Q`、`%r` 等）
- heredoc（`<<~TEXT ... TEXT`）不做专门处理，可能干扰深度追踪
"""

from __future__ import annotations

import re
from pathlib import Path

from adapter_base import AdapterResult, Dependency, Symbol, register

# --- 依赖识别 ---
#
# `require 'foo'`（可能是标准库/gem，也可能是项目自己的文件，无法从
# 单行本身区分——Ruby 的 require 路径解析规则跟 npm/Go 不同，没有
# "带 ./ 前缀才是本地"这种强制约定，纯 gem 名和本地文件路径可以长得
# 一模一样）；`require_relative 'foo'`（明确相对当前文件路径，只能是
# 项目内部文件，这是 Ruby 专门为了解决"require 分不清本地/gem"这个
# 问题而引入的语法）。两者语义不同，必须分开处理，不能都当成笼统的
# "require"：require_relative 是唯一能不靠额外信息就确定 internal 的
# 情况，普通 require 的目标即使是字符串字面量、拿到的内容也无法确定
# 内外部归类。
REQUIRE_RE = re.compile(r"""^\s*require\s+(['"])([^'"]*)\1""")
REQUIRE_RELATIVE_RE = re.compile(r"""^\s*require_relative\s+(['"])([^'"]*)\1""")

# Ruby 标准库常见模块（手动列举一份高频使用的核心库，不追求穷尽——
# Ruby 没有像 Python sys.stdlib_module_names 那样的运行时内置清单可以
# 直接查询，只能手动维护；这份清单如果遗漏了某个冷门标准库，效果是
# 保守地把它归为 unknown 而不是 external，不会导致误判，可以接受）。
_RUBY_STDLIB_MODULES = frozenset({
    "json", "set", "uri", "net/http", "net/https", "net/ftp", "net/smtp",
    "date", "time", "fileutils", "pathname", "logger", "digest", "base64",
    "yaml", "csv", "erb", "ostruct", "singleton", "forwardable", "delegate",
    "open-uri", "optparse", "securerandom", "tempfile", "tmpdir", "socket",
    "thread", "monitor", "benchmark", "pp", "pry", "irb", "abbrev",
    "shellwords", "English", "resolv",
})


def _classify_ruby_require_target(target: str) -> str:
    if target in _RUBY_STDLIB_MODULES:
        return "external"
    # 普通 require 的目标既可能是 gem 名，也可能是项目自己的文件路径
    # （Ruby 的 require 惯例是把 lib/ 目录加进 $LOAD_PATH，之后项目内部
    # 文件也用不带路径前缀的裸名字 require，跟外部 gem 写法完全一样），
    # 仅从这一行代码本身无法确定，归 unknown。
    return "unknown"

# 开启一个需要 end 收尾的块的关键字（作为独立单词出现在行首时）。
# do 单独处理（作为块修饰符出现在其他语句末尾，见 _opens_do_block）。
_BLOCK_OPEN_KEYWORDS = {"def", "class", "module", "if", "unless", "while", "until", "case", "begin", "for"}
_BLOCK_OPEN_LEADING_RE = re.compile(
    r"^\s*(def|class|module|if|unless|while|until|case|begin|for)\b"
)
# 单行修饰符形式：`puts "x" if cond`、`return unless valid?` ——关键字不在
# 行首，而是跟在其他内容后面，这种不开启块，不需要 end。
_MODIFIER_SUFFIX_RE = re.compile(r"\b(if|unless|while|until)\b")

DEF_RE = re.compile(r"^(\s*)def\s+(?:self\.)?([A-Za-z_][A-Za-z0-9_?!=]*)")
CLASS_RE = re.compile(r"^(\s*)class\s+([A-Za-z_][A-Za-z0-9_:]*)")
MODULE_RE = re.compile(r"^(\s*)module\s+([A-Za-z_][A-Za-z0-9_:]*)")

# `do` 作为块修饰符出现在行尾（可能后面跟 |参数| ），例如
# `[1,2].each do |x|` 或 `Thread.new do`。
_DO_BLOCK_SUFFIX_RE = re.compile(r"\bdo\s*(\|[^|]*\|)?\s*$")

_END_RE = re.compile(r"^\s*end\b")


def _mask_ruby_strings_and_comments(lines: list[str]) -> list[str]:
    """
    Ruby 专属的注释/字符串统一屏蔽函数，一次线性扫描同时处理：
    - `#` 行注释
    - `=begin` / `=end` 块注释（必须独占一行，Ruby 语法规定如此）
    - 单引号字符串 `'...'`（只有 `\\'` 和 `\\\\` 是转义）
    - 双引号字符串 `"..."`（支持 `#{...}` 插值，插值内部原样保留以维持
      括号计数，但插值外的字符串主体内容仍然屏蔽）
    - 基础的 `%w[...]`/`%i[...]` 数组字面量

    不复用 mask_c_family_comments_and_strings 或 mask_rust_source：Ruby 的
    字符串插值语法（`"#{expr}"`）和 `=begin/=end` 块注释是两者都没有的
    独特语法元素，跟以往每一种新语言一样，遵循本项目已经验证过的经验——
    字符串/注释边界必须在同一次扫描里统一处理，不能拆成独立阶段。
    """
    full_text = "".join(lines)
    out_chars: list[str] = []
    i = 0
    n = len(full_text)
    at_line_start = True

    while i < n:
        # `=begin` / `=end` 块注释必须独占一行开头
        if at_line_start and full_text[i : i + 6] == "=begin":
            while i < n:
                if full_text[i : i + 4] == "=end" and (i == 0 or full_text[i - 1] == "\n"):
                    while i < n and full_text[i] != "\n":
                        out_chars.append(" ")
                        i += 1
                    break
                out_chars.append(" " if full_text[i] != "\n" else "\n")
                i += 1
            at_line_start = True
            continue

        ch = full_text[i]

        if ch == "#":
            while i < n and full_text[i] != "\n":
                out_chars.append(" ")
                i += 1
            at_line_start = False
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
            at_line_start = False
            continue

        if ch == '"':
            out_chars.append('"')
            i += 1
            interp_depth = 0
            while i < n:
                if full_text[i : i + 2] == "#{" and interp_depth == 0:
                    out_chars.append("#{")
                    i += 2
                    interp_depth = 1
                    continue
                if interp_depth > 0:
                    if full_text[i] == "{":
                        interp_depth += 1
                    elif full_text[i] == "}":
                        interp_depth -= 1
                    out_chars.append(full_text[i])
                    i += 1
                    continue
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
                    continue
                out_chars.append(" ")
                i += 1
            at_line_start = False
            continue

        if full_text[i : i + 2] in ("%w", "%i") and i + 2 < n and full_text[i + 2] in "[({<":
            opener = full_text[i + 2]
            closer = {"[": "]", "(": ")", "{": "}", "<": ">"}[opener]
            out_chars.append(full_text[i : i + 3])
            i += 3
            while i < n and full_text[i] != closer:
                out_chars.append(" " if full_text[i] != "\n" else "\n")
                i += 1
            if i < n:
                out_chars.append(closer)
                i += 1
            at_line_start = False
            continue

        out_chars.append(ch)
        if ch == "\n":
            at_line_start = True
        elif ch not in (" ", "\t"):
            at_line_start = False
        i += 1

    masked_text = "".join(out_chars)
    return masked_text.splitlines(keepends=True)


class RubyAdapter:
    name = "ruby"

    def match(self, filepath) -> bool:
        return Path(filepath).suffix == ".rb"

    def extract_dependencies(self, lines: list[str]) -> list[Dependency]:
        """
        识别 `require`（无法从单行确定内外部，归 unknown）和
        `require_relative`（明确相对当前文件路径，归 internal）。复用
        _mask_ruby_strings_and_comments 屏蔽 `#` 注释和 `=begin/=end`
        块注释，避免注释里提到的 require 示例文字被误判为真实依赖。
        字符串遮蔽后的内容不影响这里的判断——require 的参数需要
        是一个真正的字符串字面量才能匹配正则本身，遮蔽只影响引号
        内部字符不影响引号和关键字，判断结构不需要额外回到原始文本
        （跟 Rust 同理：这里提取的是路径字符串，不涉及展示层的
        遮蔽/未遮蔽区别，因为这个正则直接在原始 lines 上跑，不经过
        遮蔽这一步——遮蔽只用于排除掉注释里的诱饵）。
        """
        clean_lines = _mask_ruby_strings_and_comments(lines)
        deps: list[Dependency] = []

        for i, (clean, raw) in enumerate(zip(clean_lines, lines)):
            clean_stripped = clean.rstrip("\n")
            if not clean_stripped.strip():
                continue
            raw_stripped = raw.rstrip("\n")

            if REQUIRE_RELATIVE_RE.match(clean_stripped):
                m = REQUIRE_RELATIVE_RE.match(raw_stripped)
                if m:
                    target = m.group(2)
                    deps.append(Dependency(
                        raw_text=raw_stripped.strip(),
                        kind="internal",
                        line_no=i + 1,
                        target=target,
                    ))
                continue

            if REQUIRE_RE.match(clean_stripped):
                m = REQUIRE_RE.match(raw_stripped)
                if m:
                    target = m.group(2)
                    deps.append(Dependency(
                        raw_text=raw_stripped.strip(),
                        kind=_classify_ruby_require_target(target),
                        line_no=i + 1,
                        target=target,
                    ))

        return deps

    def extract_symbols(self, filepath, lines: list[str]) -> AdapterResult:
        clean_lines = _mask_ruby_strings_and_comments(lines)
        symbols: list[Symbol] = []

        # end 配对深度栈：每一项是 (是否是"容器"即def/class/module，
        # 用于计算展示深度)。用简单计数追踪嵌套层级——不同于 C 系语言的
        # BraceDepthTracker（那是基于花括号字符计数，这里是基于关键字
        # 配对计数），语义等价但实现独立。
        # 栈里的每一项：("container"|"other", display_depth)
        # "container" 才计入符号展示的嵌套 depth，"other"（if/while/do等）
        # 只用于正确维护 end 配对，不影响展示深度。
        stack: list[tuple[str, int]] = []

        def container_depth() -> int:
            for kind, d in reversed(stack):
                if kind == "container":
                    return d
            return -1

        for i, raw in enumerate(clean_lines):
            stripped = raw.rstrip("\n")
            if not stripped.strip():
                continue

            # 单行定义：`class Foo; end`、`def foo; end` —— 开启和收尾在
            # 同一行，不压入栈。检测标准：这一行同时匹配"开启关键字在行首"
            # 和"包含独立的 end"（用分号分隔的复合语句形式）。
            single_line_end = bool(re.search(r";\s*end\s*$", stripped))

            leading_match = _BLOCK_OPEN_LEADING_RE.match(stripped)

            m_def = DEF_RE.match(stripped)
            m_class = CLASS_RE.match(stripped)
            m_module = MODULE_RE.match(stripped)

            if m_def:
                cur_depth = container_depth() + 1
                symbols.append(Symbol(name=stripped.strip(), depth=cur_depth, line_no=i + 1))
                if not single_line_end:
                    stack.append(("container", cur_depth))
                continue

            if m_class:
                cur_depth = container_depth() + 1
                symbols.append(Symbol(name=stripped.strip(), depth=cur_depth, line_no=i + 1))
                if not single_line_end:
                    stack.append(("container", cur_depth))
                continue

            if m_module:
                cur_depth = container_depth() + 1
                symbols.append(Symbol(name=stripped.strip(), depth=cur_depth, line_no=i + 1))
                if not single_line_end:
                    stack.append(("container", cur_depth))
                continue

            if leading_match and leading_match.group(1) in _BLOCK_OPEN_KEYWORDS:
                # if/unless/while/until/case/begin/for 在行首，视为块形式，
                # 需要 end 配对，但不生成符号（不是"定义"）。
                if not single_line_end:
                    stack.append(("other", -1))
                continue

            if _DO_BLOCK_SUFFIX_RE.search(stripped):
                # do...end 代码块，同样只影响深度不生成符号
                stack.append(("other", -1))
                continue

            if _END_RE.match(stripped):
                if stack:
                    stack.pop()
                continue

        return AdapterResult(symbols=symbols)


register(RubyAdapter())
