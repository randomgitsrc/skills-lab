#!/usr/bin/env python3
"""
vue_adapter.py — Vue 单文件组件 (.vue) 适配器。

.vue 文件不是一门独立语言，而是 <template>/<script>/<style> 三段式容器格式。
设计上不重新发明一套 Vue 语法解析，而是：
1. 抽出 <script> 或 <script setup> 块的内容
2. 直接复用 JsTsAdapter 的核心提取逻辑（Vue 的 <script> 块内就是标准 JS/TS，
   包括 Vue 3 Composition API 的 `const x = ref(...)`、`function foo() {}`、
   `defineComponent({...})` 等都是普通 JS/TS 语法）
3. 把结果的行号按偏移量修正回整个 .vue 文件的真实行号

这是"适配器复用适配器"的例子：不需要为每种"容器里套一门已支持语言"的格式
（Vue只是一个例子，未来如果要支持 Svelte/Astro 这类单文件组件格式，
同样可以复用这个模式）重新实现符号提取逻辑。

已知局限：<template> 部分（Vue模板语法，包括其中的插值表达式、指令等）
完全不解析，只关注 <script> 块。这是刻意的范围限定——模板不是"定义"，
是渲染逻辑，跟 REPOMAP 想要展示的"顶层函数/类结构"关注点不同。
"""

from __future__ import annotations

import re
from pathlib import Path

from adapter_base import AdapterResult, Symbol, register
from adapters.js_ts_adapter import JsTsAdapter

_SCRIPT_BLOCK_RE = re.compile(
    r'<script(?:\s+[^>]*)?>(.*?)</script>', re.DOTALL | re.IGNORECASE
)


class VueAdapter:
    name = "vue"

    def match(self, filepath) -> bool:
        return Path(filepath).suffix == ".vue"

    def extract_symbols(self, filepath, lines: list[str]) -> AdapterResult:
        full_text = "".join(lines)
        symbols: list[Symbol] = []
        notes: list[str] = []

        match = _SCRIPT_BLOCK_RE.search(full_text)
        if not match:
            notes.append("未找到 <script> 块，.vue 文件的模板/样式部分不解析")
            return AdapterResult(symbols=symbols, notes=notes)

        script_tag = full_text[: match.start(1)]
        is_typescript = 'lang="ts"' in script_tag or "lang='ts'" in script_tag

        script_content = match.group(1)
        # 计算 <script> 块起始位置对应的原始行号偏移，用于把内部行号映射回整个文件
        prefix_before_script = full_text[: match.start(1)]
        line_offset = prefix_before_script.count("\n")

        script_lines = script_content.splitlines(keepends=True)
        js_ts_adapter = JsTsAdapter()
        # 虚构一个内部路径仅用于让 JsTsAdapter 满足接口签名；该适配器的
        # extract_symbols 本身不依赖扩展名做分支（TS/JS语法统一处理），
        # 这里的路径只是占位，不影响实际提取逻辑。
        fake_inner_path = Path("__vue_script_block__.ts" if is_typescript else "__vue_script_block__.js")
        inner_result = js_ts_adapter.extract_symbols(fake_inner_path, script_lines)

        for sym in inner_result.symbols:
            symbols.append(
                Symbol(
                    name=sym.name,
                    depth=sym.depth,
                    docstring=sym.docstring,
                    line_no=sym.line_no + line_offset,
                )
            )
        notes.extend(inner_result.notes)
        notes.append("仅解析 <script>/<script setup> 块，<template> 部分未解析")

        return AdapterResult(symbols=symbols, notes=notes)


register(VueAdapter())
