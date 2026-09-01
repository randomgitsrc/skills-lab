#!/usr/bin/env python3
"""
rust_adapter.py — Rust 语言适配器。

Rust 虽然也是花括号语言，但语法跟 C 系家族（C#/Java/C++）差异较大——没有
`public`/`private` 这类访问修饰符（用 `pub` 且位置/语义不同），核心定义关键字
是 `fn`/`struct`/`trait`/`enum`/`impl`/`mod`，`impl Trait for Type` 更是
C 系语言完全没有对应概念的构造。参考 references/adapter_guide.md 里的建议，
语法差异较大时新开一个独立适配器，而不是往 c_family_adapter.py 的 Dialect
系统里硬塞一个不匹配的方言。

复用点：仅花括号深度追踪（BraceDepthTracker）。注释/字符串屏蔽**没有**复用
共享的 mask_c_family_comments_and_strings，而是本文件自带一个 Rust 专属的
统一屏蔽函数（mask_rust_source）——开发过程中先后踩到三个真实 bug 才确认
这个必要性：

1. Rust 的生命周期标注（`'a`、`'static`）用单引号开头但不闭合，通用的
   C 系 masker 会把它们误判为未闭合字符字面量，吞掉所在行剩余内容
   （包括函数返回类型后面的 `{`）。最初尝试用独立的预处理阶段"中和"
   生命周期标注再喂给共享 masker，这一步单独看是有效的。
2. 但原始字符串 `r"..."`/`r#"..."#`（内容不转义、可跨行）不能同样拆成
   独立预处理阶段处理——判断"这是不是一个原始字符串"依赖"当前是否已经
   在注释内部"这个上下文，独立阶段天然不知道自己是否身处注释中。真实
   案例：`/*! ... "search worker" ... */` 文档注释里，"worker" 结尾的
   `r` 加后面的 `"` 恰好拼出看起来像原始字符串开头的 `r"`，被独立的
   原始字符串扫描器误判，导致该文件整体损毁。
3. Rust 允许块注释嵌套（`/* outer /* inner */ still outer */`），
   C/C++/Java/C# 都不允许，共享 masker 的块注释处理不支持嵌套计数。

这三点共同指向同一个结论：字符串/字符/生命周期/注释这几种语法元素的边界
判断互相依赖，必须在同一次线性扫描里用一套状态机处理，任何"拆成独立阶段
按顺序处理"的方案都会在某个阶段对另一个阶段负责的语法产生误判——这跟
C 系适配器上已经验证过的教训完全一致，只是 Rust 的语法元素集合不同
（多了生命周期和原始字符串，少了 C# 的逐字字符串），所以复用不了同一个
函数，需要一份独立实现。

识别范围：
- `fn`（含 `pub fn`、`async fn`、`pub async fn`）
- `struct` / `enum` / `trait`
- `impl Type { ... }` 和 `impl Trait for Type { ... }`（后者作为容器展示，
  内部方法按普通容器子层处理，跟 class 方法的处理方式一致）
- `mod` 模块块（作为容器，允许嵌套 fn/struct 等）

已知局限：
- 宏定义（`macro_rules! foo { ... }`）不识别
- 泛型约束（`where` 子句）不做完整解析，可能在复杂泛型场景下影响方法签名
  匹配的准确度
- 属性宏（`#[derive(...)]`、`#[test]` 等）直接跳过，不影响后续定义的识别，
  但属性本身不会被展示
- 字节字符串/字节字符字面量（`b"..."`、`b'x'`）的屏蔽逻辑跟普通字符串
  共用同一套规则，不单独区分语义（对花括号计数和符号识别没有影响）
"""

from __future__ import annotations

import re
from pathlib import Path

from adapter_base import AdapterResult, Dependency, Symbol, register
from adapter_utils import BraceDepthTracker, line_is_brace_balanced

# Rust 需要一个专属的、单次线性扫描的注释+字符串屏蔽函数，不能像其他花括号
# 语言那样直接复用 adapter_utils.mask_c_family_comments_and_strings 或者
# 把"生命周期标注""原始字符串"各自拆成独立的预处理阶段。
#
# 这是本文件开发过程中踩过两次同一类坑之后才确认的结论：
#
# 1. 生命周期标注（`'a`、`'static`）用单引号开头但不闭合，共享的 C 系
#    masker 会把它们误判为未闭合字符字面量，吞掉所在行剩余内容
#    （包括函数体开启的 `{`）。最初尝试用一个独立的"生命周期中和"预处理
#    步骤在喂给共享 masker 之前先处理，这一步本身没问题。
# 2. 但原始字符串 `r"..."` / `r#"..."#` 不能同样用独立预处理步骤解决——
#    因为判断"这是不是一个原始字符串"这件事本身依赖"当前是否已经在注释
#    里"的上下文（真实案例：`/*! ... "search worker" ... */` 这个文档
#    注释里，"worker" 结尾的 `r` 加后面引号的 `"` 恰好拼出 `r"`，被独立的
#    原始字符串扫描器误判为开启了一个原始字符串，因为那次扫描完全不知道
#    自己正身处一个注释内部）。
#
# 这跟本项目在 C 系适配器上已经验证过的教训完全一致（见
# adapter_utils.mask_c_family_comments_and_strings 的文档字符串）：
# "注释"和"字符串"的语法边界互相依赖对方的状态才能正确判断，任何试图
# 拆成独立阶段处理的方案，不管拆几个阶段、按什么顺序拆，都会在某个阶段
# 对另一个阶段负责的语法产生误判。正确做法是一次遍历、一套状态机同时
# 跟踪全部语法环境（行注释/块注释/字符串/字符字面量/生命周期/原始字符串），
# 按字符实际出现顺序处理。
#
# Rust 特有的复杂度，本函数额外需要处理的（相比 C 系语言）：
# - 块注释可以嵌套（`/* outer /* inner */ still outer */`），C/C++/Java/C#
#   都不允许嵌套块注释，Rust 允许，用一个深度计数器而不是简单的布尔标志。
# - 原始字符串的井号数量可变（`r"`、`r#"`、`r##"`...），且必须精确匹配
#   开头和结尾的井号数量才算闭合。
# - 生命周期标注（`'a`）与字符字面量（`'a'`）单靠一个字符前瞻区分：
#   字符字面量后面紧跟另一个 `'`，生命周期标注不会。
_HEX_DIGIT = "0123456789abcdefABCDEF"


def mask_rust_source(lines: list[str]) -> list[str]:
    full_text = "".join(lines)
    out_chars: list[str] = []
    i = 0
    n = len(full_text)

    while i < n:
        two = full_text[i : i + 2]

        if two == "//":
            while i < n and full_text[i] != "\n":
                out_chars.append(" ")
                i += 1
            continue

        if two == "/*":
            # 支持嵌套块注释：/* outer /* inner */ still outer */
            depth = 1
            out_chars.append("  ")
            i += 2
            while i < n and depth > 0:
                if full_text[i : i + 2] == "/*":
                    depth += 1
                    out_chars.append("  ")
                    i += 2
                    continue
                if full_text[i : i + 2] == "*/":
                    depth -= 1
                    out_chars.append("  ")
                    i += 2
                    continue
                out_chars.append(" " if full_text[i] != "\n" else "\n")
                i += 1
            continue

        # 原始字符串：r"..."、r#"..."#、r##"..."##（可选 b 前缀表示字节字符串，
        # 如 br"..."，同样处理）。必须在判断"普通字符串/生命周期"之前检查，
        # 因为 r 后面的这个 " 不该走普通字符串的转义规则。
        if full_text[i] in ("r", "b") or two in ("br", "Rb"):
            m = re.match(r"(?:b)?r(#*)\"", full_text[i:])
            if m:
                hashes = m.group(1)
                closer = '"' + hashes
                out_chars.append(full_text[i : i + m.end()])
                i += m.end()
                close_idx = full_text.find(closer, i)
                if close_idx == -1:
                    for ch in full_text[i:]:
                        out_chars.append(ch if ch == "\n" else " ")
                    i = n
                else:
                    for ch in full_text[i:close_idx]:
                        out_chars.append(ch if ch == "\n" else " ")
                    out_chars.append(full_text[close_idx : close_idx + len(closer)])
                    i = close_idx + len(closer)
                continue

        ch = full_text[i]

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
                out_chars.append(" " if full_text[i] != "\n" else "\n")
                i += 1
            continue

        if ch == "'":
            # 先看是不是字符字面量：`'x'`、`'\n'`、`'\u{1F600}'` 这类，
            # 判断标准是"往后找，跳过一个转义序列或单个字符后，紧跟着
            # 另一个单引号"。如果不满足，就是生命周期标注，只消费这个
            # `'` 本身和后面的标识符，不当作字符串屏蔽（标识符文本本身
            # 不影响花括号计数，留着不处理也无妨，只需要确保这个 `'`
            # 不会触发"未闭合字符字面量吞掉整行"的行为）。
            j = i + 1
            if j < n and full_text[j] == "\\":
                # 转义序列：\n \t \\ \' \" \0 \xHH \u{...} 等，找到转义序列
                # 结束的位置，再看后面是不是紧跟单引号。
                k = j + 1
                if k < n and full_text[k] == "u" and k + 1 < n and full_text[k + 1] == "{":
                    end_brace = full_text.find("}", k)
                    k = end_brace + 1 if end_brace != -1 else k + 2
                elif k < n and full_text[k] == "x":
                    k += 3  # \xHH
                elif k < n:
                    k += 1
                is_char_literal = k < n and full_text[k] == "'"
                literal_end = k  # 指向收尾单引号本身的位置，不是它之后一位
            else:
                is_char_literal = j < n and j + 1 < n and full_text[j + 1] == "'"
                literal_end = j + 1  # 指向收尾单引号本身的位置

            if is_char_literal:
                out_chars.append("'")
                i += 1
                while i < literal_end and i < n:
                    out_chars.append(" " if full_text[i] != "\n" else "\n")
                    i += 1
                if i < n and full_text[i] == "'":
                    out_chars.append("'")
                    i += 1
                continue
            else:
                # 生命周期标注：只消费 `'` 和紧跟的标识符字符，原样保留
                # （不影响花括号计数），继续正常扫描。
                out_chars.append("'")
                i += 1
                continue

        out_chars.append(ch)
        i += 1

    masked_text = "".join(out_chars)
    return masked_text.splitlines(keepends=True)


FN_RE = re.compile(
    r"^(\s*)(?:pub(?:\([^)]*\))?\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?(?:extern\s+\"[^\"]*\"\s+)?"
    r"fn\s+([A-Za-z_][A-Za-z0-9_]*)"
)
STRUCT_RE = re.compile(r"^(\s*)(?:pub(?:\([^)]*\))?\s+)?struct\s+([A-Za-z_][A-Za-z0-9_]*)")
ENUM_RE = re.compile(r"^(\s*)(?:pub(?:\([^)]*\))?\s+)?enum\s+([A-Za-z_][A-Za-z0-9_]*)")
TRAIT_RE = re.compile(r"^(\s*)(?:pub(?:\([^)]*\))?\s+)?trait\s+([A-Za-z_][A-Za-z0-9_]*)")
MOD_RE = re.compile(r"^(\s*)(?:pub(?:\([^)]*\))?\s+)?mod\s+([A-Za-z_][A-Za-z0-9_]*)")
# `impl Type { ... }` 或 `impl Trait for Type { ... }`，都当作容器处理，
# 展示时用整行文本（保留 "for Type" 这部分信息，跟真实基准的做法一致）。
IMPL_RE = re.compile(r"^(\s*)impl\b[^{;]*\{?\s*$")
ATTRIBUTE_RE = re.compile(r"^\s*#!?\[.*\]\s*$")

# --- 依赖识别 ---
#
# `use` 语句：`use std::collections::HashMap;`、`use crate::utils::helper;`。
# 只需要拿到 use 和分号/花括号之间的路径部分，不需要解析 `use foo::{a, b}`
# 这种花括号批量导入里具体导入了哪些符号（跟 Python 的 from...import 是
# 同一个道理：只关心"这个文件依赖哪个模块"，不关心从那个模块具体拿了
# 什么）。
USE_RE = re.compile(r"^(\s*)(?:pub(?:\([^)]*\))?\s+)?use\s+([A-Za-z_][A-Za-z0-9_:]*)")

# `mod foo;`（声明一个子模块，对应磁盘上的 foo.rs 或 foo/mod.rs，这是
# Rust 里唯一真正回答"这个文件依赖哪个别的文件"的语句——`use` 更多是
# "引用一个已知路径下的东西"，很多时候引用的是同一个 crate 内部或者
# 外部 crate，不直接等价于"这里有一个新文件"。已有的 MOD_RE 用来识别
# `mod foo { ... }` 这种内联子模块定义（生成符号），这里单独识别
# `mod foo;` 这种"声明外部文件模块"的形式（生成依赖），两者语法很像但
# 语义不同：前者子模块内容直接写在同一个文件里，不对应额外的文件；
# 后者必须存在一个对应的 foo.rs 才能编译通过。
MOD_DECL_RE = re.compile(r"^(\s*)(?:pub(?:\([^)]*\))?\s+)?mod\s+([A-Za-z_][A-Za-z0-9_]*)\s*;")

# Rust 标准库/官方 crate 前缀（std/core/alloc 是编译器内置，不需要在
# Cargo.toml 里声明；test/proc_macro 是编译器提供的特殊 crate）。跟 Node.js
# 内置模块判断同样的思路：Rust 顶级 crate 命名空间里，这几个是官方保留、
# 全局唯一、不会跟真实项目的 crate 名冲突的前缀，可以直接列举判断。
_RUST_STDLIB_CRATES = frozenset({"std", "core", "alloc", "test", "proc_macro"})


def _classify_rust_use_target(path: str) -> str:
    first_segment = path.split("::")[0]
    if first_segment in ("crate", "self", "super"):
        # `crate::`（当前 crate 根开始）、`self::`（当前模块）、
        # `super::`（父模块）都是明确的、不需要额外信息就能确定的
        # "同一个 crate 内部"引用。
        return "internal"
    if first_segment in _RUST_STDLIB_CRATES:
        return "external"
    # 剩下的裸 crate 名（比如 `serde`）——语法上跟"当前 crate 里一个顶层
    # 模块名"完全没有区别（Rust 2018+ 的模块路径解析规则允许两种情况
    # 用同样的写法），仅从这一行代码本身无法确定这是 Cargo.toml 里声明的
    # 外部依赖，还是当前 crate 自己某个未加 crate:: 前缀直接引用的顶层
    # 模块，归 unknown，不猜测。
    return "unknown"


class RustAdapter:
    name = "rust"

    def match(self, filepath) -> bool:
        return Path(filepath).suffix == ".rs"

    def extract_dependencies(self, lines: list[str]) -> list[Dependency]:
        """
        识别 Rust 的 `use` 语句（引用路径，可能内部可能外部，见
        _classify_rust_use_target 的说明）和 `mod foo;`（声明外部文件
        模块，明确是 internal——这才是 Rust 里真正对应"这个文件依赖
        哪个别的文件"的语句）。

        复用 mask_rust_source 屏蔽注释/字符串/生命周期标注，避免文档
        注释里举例提到的 `use foo::bar;` 这类文字被误判为真实依赖
        （原则跟 JS/TS、C-family 的实现一致：用屏蔽后的文本判断"这一行
        是否真的是代码"，但这里 use/mod 语句本身不含字符串字面量，
        不需要像 JS/TS 那样额外回到原始文本提取内容——Rust 的模块路径
        写法本身就不带引号，屏蔽机制不会影响到它）。
        """
        clean_lines = mask_rust_source(lines)
        deps: list[Dependency] = []

        for i, raw in enumerate(clean_lines):
            stripped = raw.rstrip("\n")
            if not stripped.strip():
                continue

            m = MOD_DECL_RE.match(stripped)
            if m:
                target = m.group(2)
                deps.append(Dependency(
                    raw_text=stripped.strip(),
                    kind="internal",
                    line_no=i + 1,
                    target=target,
                ))
                continue

            m = USE_RE.match(stripped)
            if m:
                target = m.group(2)
                deps.append(Dependency(
                    raw_text=stripped.strip(),
                    kind=_classify_rust_use_target(target),
                    line_no=i + 1,
                    target=target,
                ))

        return deps

    def extract_symbols(self, filepath, lines: list[str]) -> AdapterResult:
        # 用 Rust 专属的统一注释/字符串屏蔽函数（见 mask_rust_source 的文档
        # 字符串，说明了为什么不能像早期版本那样拆成多个独立预处理阶段）。
        clean_lines = mask_rust_source(lines)
        symbols: list[Symbol] = []

        # 统一帧栈，跟 C 系适配器同样的设计：区分 container（struct/trait/
        # impl/mod，展示内部成员）和 body（fn 函数体，屏蔽内部语句）。
        frames: list[dict] = []
        tracker = BraceDepthTracker()

        def pop_exited(depth_before: int) -> None:
            while frames and frames[-1]["entered"] and depth_before <= frames[-1]["base_depth"]:
                frames.pop()
            if frames and not frames[-1]["entered"] and depth_before > frames[-1]["base_depth"]:
                frames[-1]["entered"] = True

        def nearest_container_depth() -> int:
            for f in reversed(frames):
                if f["kind"] == "container":
                    return f["container_depth"]
            return -1

        for i, raw in enumerate(clean_lines):
            stripped = raw.rstrip("\n")
            if not stripped.strip():
                tracker.update(stripped)
                continue

            if ATTRIBUTE_RE.match(stripped):
                # 属性宏（#[derive(Debug)]、#[test]等）跳过，不影响深度追踪
                # 也不生成符号——真实基准同样不会把属性本身展示为独立符号。
                tracker.update(stripped)
                continue

            depth_before = tracker.depth_before_line()
            pop_exited(depth_before)

            in_body = bool(frames) and frames[-1]["kind"] == "body" and frames[-1]["entered"]
            if in_body:
                tracker.update(stripped)
                continue
            top_is_pending_body = bool(frames) and frames[-1]["kind"] == "body" and not frames[-1]["entered"]
            if top_is_pending_body:
                tracker.update(stripped)
                continue

            container_frames = [f for f in frames if f["kind"] == "container"]
            is_top_level = len(container_frames) == 0
            in_container_direct_child = (
                frames and frames[-1]["kind"] == "container" and depth_before == frames[-1]["base_depth"] + 1
            )

            handled = False

            for pattern in (STRUCT_RE, ENUM_RE, TRAIT_RE, MOD_RE):
                m = pattern.match(stripped)
                if m and (is_top_level or in_container_direct_child):
                    cur_depth = nearest_container_depth() + 1
                    symbols.append(Symbol(name=stripped.strip(), depth=cur_depth, line_no=i + 1))
                    ends_as_forward_decl = stripped.rstrip().endswith(";") and "{" not in stripped
                    if not line_is_brace_balanced(stripped) and not ends_as_forward_decl:
                        frames.append({
                            "base_depth": depth_before,
                            "kind": "container",
                            "container_depth": cur_depth,
                            "entered": False,
                        })
                    handled = True
                    break

            if not handled and (is_top_level or in_container_direct_child):
                m = IMPL_RE.match(stripped)
                if m:
                    cur_depth = nearest_container_depth() + 1
                    symbols.append(Symbol(name=stripped.strip(), depth=cur_depth, line_no=i + 1))
                    if not line_is_brace_balanced(stripped):
                        frames.append({
                            "base_depth": depth_before,
                            "kind": "container",
                            "container_depth": cur_depth,
                            "entered": False,
                        })
                    handled = True

            if not handled and (is_top_level or in_container_direct_child):
                m = FN_RE.match(stripped)
                if m:
                    cur_depth = nearest_container_depth() + 1
                    symbols.append(Symbol(name=stripped.strip(), depth=cur_depth, line_no=i + 1))
                    ends_as_forward_decl = stripped.rstrip().endswith(";") and "{" not in stripped
                    if not line_is_brace_balanced(stripped) and not ends_as_forward_decl:
                        frames.append({
                            "base_depth": depth_before,
                            "kind": "body",
                            "container_depth": -1,
                            "entered": False,
                        })
                    handled = True

            tracker.update(stripped)

        return AdapterResult(symbols=symbols)


register(RustAdapter())
