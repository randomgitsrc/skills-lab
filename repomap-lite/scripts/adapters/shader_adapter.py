#!/usr/bin/env python3
"""
shader_adapter.py — 着色器语言适配器，覆盖：
- GLSL (OpenGL / WebGL): .glsl .vert .frag .geom .tesc .tese .comp
- HLSL (DirectX): .hlsl .fx .cginc .compute
- WGSL (WebGPU): .wgsl

GLSL 和 HLSL 语法结构接近 C（返回类型在前：`vec3 foo(...) { }`），
WGSL 语法接近 Rust（`fn foo(...) -> vec3<f32> { }`），所以用两套正则规则，
按扩展名分派到对应规则，但共享同一个适配器类和花括号深度追踪逻辑。

识别范围：
- 顶层函数定义（GLSL/HLSL 的"类型 函数名(...)"，WGSL 的 "fn 函数名(...)"）
- struct 定义（三种语言都有，用于顶点/片元数据结构）
- HLSL 的 cbuffer（常量缓冲区，DirectX特有，概念上类似"顶层数据结构声明"）

已知局限：
- 不识别 GLSL/HLSL 的宏定义（#define）和预处理条件编译（#ifdef），
  预处理器指令按语言无关的方式跳过（不生成符号，也不参与嵌套判断）
- HLSL 的语义修饰符（`: SV_TARGET`、`: POSITION` 等）不影响符号识别，
  但也不会被单独展示为附加信息，函数名本身能正确抽出
- 不区分 vertex/fragment/compute shader 的入口点约定（比如 GLSL 的 `main`
  或 HLSL 里用 [numthreads] 标注的 compute kernel），一律按普通函数处理
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from adapter_base import AdapterResult, Symbol, register
from adapter_utils import BraceDepthTracker, strip_block_comments, strip_line_comment_naive

GLSL_EXTENSIONS = (".glsl", ".vert", ".frag", ".geom", ".tesc", ".tese", ".comp")
HLSL_EXTENSIONS = (".hlsl", ".fx", ".cginc", ".compute")
WGSL_EXTENSIONS = (".wgsl",)

# GLSL/HLSL: C风格，"[修饰符]* 返回类型 函数名(参数) [: 语义] {"
_C_STYLE_FUNC_RE = re.compile(
    r"^(\s*)(?:static\s+|inline\s+)*"
    r"[A-Za-z_][A-Za-z0-9_<>]*\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*(?::\s*[A-Za-z_][A-Za-z0-9_]*\s*)?\{?\s*$"
)
_C_STYLE_STRUCT_RE = re.compile(r"^(\s*)struct\s+([A-Za-z_][A-Za-z0-9_]*)")
_HLSL_CBUFFER_RE = re.compile(r"^(\s*)cbuffer\s+([A-Za-z_][A-Za-z0-9_]*)")

# WGSL: Rust风格，"fn 函数名(参数) [-> 返回类型] {"
_WGSL_FUNC_RE = re.compile(r"^(\s*)fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_WGSL_STRUCT_RE = re.compile(r"^(\s*)struct\s+([A-Za-z_][A-Za-z0-9_]*)")

_PREPROCESSOR_RE = re.compile(r"^\s*#")


@dataclass(frozen=True)
class ShaderDialect:
    key: str
    extensions: tuple[str, ...]
    style: str  # "c" or "wgsl"


DIALECTS = (
    ShaderDialect("glsl", GLSL_EXTENSIONS, "c"),
    ShaderDialect("hlsl", HLSL_EXTENSIONS, "c"),
    ShaderDialect("wgsl", WGSL_EXTENSIONS, "wgsl"),
)


class ShaderAdapter:
    """
    单个适配器实例处理全部三种 shader 方言（用扩展名区分风格），
    不像 C_FamilyAdapter 那样为每种方言单独注册一个实例，因为这里
    "同一文件只可能是一种方言"且方言间没有共享的容器/类概念，
    直接在 extract_symbols 里按扩展名分支即可，逻辑更简单。
    """

    name = "shader"

    def match(self, filepath) -> bool:
        suffix = Path(filepath).suffix
        return any(suffix in d.extensions for d in DIALECTS)

    def _dialect_for(self, filepath) -> ShaderDialect:
        suffix = Path(filepath).suffix
        for d in DIALECTS:
            if suffix in d.extensions:
                return d
        raise ValueError(f"unsupported shader extension: {suffix}")

    def extract_symbols(self, filepath, lines: list[str]) -> AdapterResult:
        dialect = self._dialect_for(filepath)
        clean_lines = strip_block_comments(lines)
        symbols: list[Symbol] = []
        tracker = BraceDepthTracker()
        container_stack: list[tuple[int, int]] = []

        func_re = _WGSL_FUNC_RE if dialect.style == "wgsl" else _C_STYLE_FUNC_RE
        struct_re = _WGSL_STRUCT_RE if dialect.style == "wgsl" else _C_STYLE_STRUCT_RE

        for i, raw in enumerate(clean_lines):
            stripped = strip_line_comment_naive(raw.rstrip("\n"))
            if not stripped.strip():
                continue
            if _PREPROCESSOR_RE.match(stripped):
                continue

            depth_before = tracker.depth_before_line()
            while container_stack and depth_before <= container_stack[-1][0]:
                container_stack.pop()
            is_top_level = len(container_stack) == 0

            m = struct_re.match(stripped)
            if m and is_top_level:
                symbols.append(Symbol(name=stripped.strip(), depth=0, line_no=i + 1))
                container_stack.append((depth_before, 0))
                tracker.update(stripped)
                continue

            if dialect.style == "c":
                m = _HLSL_CBUFFER_RE.match(stripped)
                if m and is_top_level:
                    symbols.append(Symbol(name=stripped.strip(), depth=0, line_no=i + 1))
                    container_stack.append((depth_before, 0))
                    tracker.update(stripped)
                    continue

            if is_top_level:
                m = func_re.match(stripped)
                if m:
                    symbols.append(Symbol(name=stripped.strip(), depth=0, line_no=i + 1))

            tracker.update(stripped)

        return AdapterResult(symbols=symbols)


register(ShaderAdapter())
