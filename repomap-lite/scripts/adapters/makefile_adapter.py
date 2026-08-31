#!/usr/bin/env python3
"""
makefile_adapter.py — Makefile 适配器。

Makefile 不是传统编程语言，没有函数/类这类"定义"，但对"agent 冷启动理解
一个项目"这个场景，**target（目标）** 是最有信息量的结构单元——一个新人
或 agent 进入陌生仓库，Makefile 里有哪些可执行 target（build、test、
clean、install...）往往比某个内部函数签名更快建立起"这个项目怎么跑起来"
的认知。这是本 skill 独立评审建议里明确提到的一类高价值信息，值得单独
支持，即使它不是传统意义上的"代码符号"。

匹配方式：Makefile 是本项目第一个**不按扩展名匹配**的适配器——常见约定
是文件名恰好是 `Makefile`/`makefile`（无扩展名），或者 `*.mk`/
`*.make` 这类扩展名，或者 `Makefile.foo` 这种约定后缀。match() 需要同时
检查文件名本身和扩展名。

识别范围：
- 顶层 target 定义：`target_name: dependencies...`（行首、冒号前是
  target 名，可以有依赖列表）
- `.PHONY` 声明的伪目标本身不算独立符号（它只是修饰紧随其后的真实target
  定义，展示上跟普通 target 一样）

已知局限：
- 变量赋值（`CC = gcc`）不展示为符号——这些是配置常量，不是"可执行动作"，
  跟 REPOMAP 想突出的"项目怎么跑起来"这个信息目标不太相关，保持地图简洁
- 模式规则（`%.o: %.c`）按普通 target 展示，不做模式规则语义上的特殊标注
- 条件指令（`ifeq`/`ifdef` 等）和 `include` 指令不影响 target 识别，
  但本身也不生成符号
- 多行 target（用反斜杠续行的依赖列表）只识别声明行本身，不追踪续行内容
"""

from __future__ import annotations

import re
from pathlib import Path

from adapter_base import AdapterResult, Symbol, register

# target 定义：行首（不能有前导空白，Makefile 语法要求 target 定义顶格），
# 冒号前是一个或多个目标名（这里只取第一个，因为多目标规则少见且第一个
# 通常最有代表性），冒号后可以跟依赖列表。排除 `:=`/`::=`/`?=`/`+=` 这些
# 变量赋值操作符（尽管这些前面不该有变量名格式的 target，但用负向前瞻
# 保守起见排除，避免误判变量赋值为 target）。
_TARGET_RE = re.compile(
    r"^([A-Za-z_%][A-Za-z0-9_./%-]*)\s*:(?!=)"
)
# 特殊 target（.PHONY、.SUFFIXES 等）本身不算普通 target，虽然语法上
# 长得一样，但语义上是构建系统的元指令，不代表"可以执行的动作"。
_SPECIAL_TARGET_PREFIXES = (
    ".PHONY", ".SUFFIXES", ".DEFAULT", ".PRECIOUS", ".INTERMEDIATE",
    ".SECONDARY", ".DELETE_ON_ERROR", ".IGNORE", ".SILENT", ".EXPORT_ALL_VARIABLES",
    ".NOTPARALLEL", ".ONESHELL", ".POSIX",
)


class MakefileAdapter:
    name = "makefile"

    def match(self, filepath) -> bool:
        p = Path(filepath)
        if p.name in ("Makefile", "makefile", "GNUmakefile"):
            return True
        if p.suffix in (".mk", ".make"):
            return True
        return False

    def extract_symbols(self, filepath, lines: list[str]) -> AdapterResult:
        symbols: list[Symbol] = []

        for i, raw in enumerate(lines):
            stripped = raw.rstrip("\n")
            if not stripped.strip() or stripped.startswith("#"):
                continue
            # Makefile 用 tab 缩进表示"这是某个 target 的命令行"，这类行
            # 不应该被当作新的 target 定义（虽然 _TARGET_RE 要求顶格，
            # 一般不会误判，但显式跳过更清晰、也更安全）。
            if stripped.startswith("\t") or stripped.startswith("    "):
                continue

            m = _TARGET_RE.match(stripped)
            if not m:
                continue
            name = m.group(1)
            if any(name.startswith(p) for p in _SPECIAL_TARGET_PREFIXES):
                continue

            # 目标专属变量赋值（`install: SKIP_BUILD ?= 1`）语法上跟真正的
            # "target: 依赖列表"长得一样，但冒号后面是一次变量赋值
            # （含 ?=/:=/+=/= 操作符），不是依赖声明。用真实项目 Redis 的
            # 顶层 Makefile 复现确认过这个模式。判断标准：冒号后如果整行
            # 是"标识符 赋值操作符 ..."这种形式（不是空格分隔的依赖名列表），
            # 就跳过，不生成符号。
            #
            # 注意：同一个 target 名字被多行分别声明依赖是合法且常见的
            # GNU Make 写法（依赖会累加，不是互相覆盖），例如：
            #   build: main.o
            #   build: utils.o
            # 这种情况**不做去重**——两行都是真实的、独立的信息，去重会
            # 丢失第二行 `utils.o` 这个真实依赖，之前尝试按 target 名字
            # 去重后用这个例子验证过确实会丢信息，所以改成只过滤"变量
            # 赋值伪装成 target"这一种情况，不做任何基于名字的去重。
            rest = stripped[m.end() :].strip()
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*(?:\?=|:=|\+=|=)", rest):
                continue

            symbols.append(Symbol(name=stripped.strip(), depth=0, line_no=i + 1))

        return AdapterResult(symbols=symbols)


register(MakefileAdapter())
