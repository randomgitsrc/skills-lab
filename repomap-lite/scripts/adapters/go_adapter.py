#!/usr/bin/env python3
"""
go_adapter.py — Go 语言适配器。

覆盖范围：func（含方法接收器 `func (r *T) Method()`）、type X struct/interface。

相比早期版本的修正：新增反引号原始字符串跳过（早期版本用真实项目 cobra 测出
一个反引号字符串里嵌了 `func main() {...}` 伪代码被误判为真符号的 bug，
见 references/known_limitations.md）。

新增 `is_generated()`：识别 Go 官方约定的自动生成文件标记
（`// Code generated ... DO NOT EDIT.`，规范见 https://go.dev/s/generatedcode，
`cmd/go` 工具链自己也依赖这条规则）。用真实 `protoc-gen-go` 输出验证过——
不过滤的话，`.pb.go` 这类 protobuf/gRPC 生成代码会完整出现在地图里，
对理解"这个项目实际写了什么代码"没有价值。
"""

from __future__ import annotations

import re
from pathlib import Path

from adapter_base import AdapterResult, Symbol, register
from adapter_utils import IndentStack, indent_of, matches_generated_file_markers, strip_raw_string_literals_naive

GO_FUNC_RE = re.compile(r"^(\s*)func\s*(\([^)]*\))?\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(")
GO_TYPE_STRUCT_RE = re.compile(r"^(\s*)type\s+([A-Za-z_][A-Za-z0-9_]*)\s+(struct|interface)\b")

# Go 官方约定的生成文件标记：`// Code generated <工具名> DO NOT EDIT.`
# 中间可以是任意文本（工具名/命令行），首尾固定。真实生成器（protoc-gen-go、
# mockgen、stringer 等）都遵循这个格式。
_GENERATED_MARKERS = (
    re.compile(r"^\s*//\s*Code generated .* DO NOT EDIT\.\s*$"),
)


class GoAdapter:
    name = "go"

    def match(self, filepath) -> bool:
        return Path(filepath).suffix == ".go"

    def is_generated(self, lines: list[str]) -> bool:
        return matches_generated_file_markers(lines, _GENERATED_MARKERS)

    def extract_symbols(self, filepath, lines: list[str]) -> AdapterResult:
        clean_lines = strip_raw_string_literals_naive(lines)
        symbols: list[Symbol] = []
        stack = IndentStack()

        for i, raw in enumerate(clean_lines):
            stripped = raw.rstrip("\n")
            if not stripped.strip():
                continue

            m = GO_FUNC_RE.match(stripped) or GO_TYPE_STRUCT_RE.match(stripped)
            if not m:
                continue

            indent = indent_of(stripped)
            depth = stack.push(indent)
            symbols.append(Symbol(name=stripped.strip(), depth=depth, line_no=i + 1))

        return AdapterResult(symbols=symbols)


register(GoAdapter())
