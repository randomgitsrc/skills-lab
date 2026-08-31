#!/usr/bin/env python3
"""
dockerfile_adapter.py — Dockerfile 适配器。

跟 Makefile 一样，Dockerfile 不是传统编程语言，没有函数/类。但**多阶段
构建的 `FROM ... AS stage` 声明**是高价值的结构信息——一个新人/agent
进入陌生仓库，Dockerfile 有几个构建阶段（builder/runtime/debug等）、
每个阶段的基础镜像是什么，往往比某个内部实现细节更快建立起"这个项目
怎么打包/部署"的认知。这是本 skill 独立评审建议里明确提到的另一类高
价值信息。

匹配方式：跟 Makefile 一样不按扩展名匹配——常见约定是文件名恰好是
`Dockerfile`（无扩展名），或者 `Dockerfile.dev`/`Dockerfile.prod` 这类
环境后缀，或者 `*.dockerfile` 扩展名。

识别范围：
- `FROM <image> AS <stage>`：多阶段构建里的具名阶段，展示阶段名和来源镜像
- `FROM <image>`（没有 AS 子句的单阶段构建，或多阶段构建的最后一个未命名
  阶段）：展示为一个匿名阶段，用来源镜像本身作为标识

已知局限：
- 只识别 `FROM` 指令，不识别 `RUN`/`COPY`/`EXPOSE`/`CMD` 等其他指令
  ——这些是每个阶段内部的具体步骤，不是"阶段"本身，跟 REPOMAP 想突出的
  "有哪些构建阶段"这个信息目标不太相关，保持地图简洁
- 不解析构建参数（`ARG`）对镜像名的插值替换（`FROM ${BASE_IMAGE}`
  会原样展示插值表达式，不会去查找 ARG 的实际取值）
- 不处理 Dockerfile 的行继续符（反斜杠续行）在 FROM 行本身出现的极端情况
  （FROM 指令本身几乎不会跨行，这个局限影响面可以忽略）
"""

from __future__ import annotations

import re
from pathlib import Path

from adapter_base import AdapterResult, Symbol, register

# FROM <image>[:tag] [AS <stage>]，大小写不敏感（Docker 指令传统上大写，
# 但语法本身不区分大小写，真实 Dockerfile 里两种写法都存在）。
_FROM_RE = re.compile(r"^\s*FROM\s+(\S+)(?:\s+AS\s+(\S+))?\s*$", re.IGNORECASE)


class DockerfileAdapter:
    name = "dockerfile"

    def match(self, filepath) -> bool:
        p = Path(filepath)
        if p.name == "Dockerfile":
            return True
        if p.name.startswith("Dockerfile."):
            return True
        if p.suffix == ".dockerfile":
            return True
        return False

    def extract_symbols(self, filepath, lines: list[str]) -> AdapterResult:
        symbols: list[Symbol] = []

        for i, raw in enumerate(lines):
            stripped = raw.rstrip("\n")
            if not stripped.strip() or stripped.strip().startswith("#"):
                continue

            m = _FROM_RE.match(stripped)
            if not m:
                continue

            image, stage = m.group(1), m.group(2)
            if stage:
                display = f"FROM {image} AS {stage}"
            else:
                display = f"FROM {image}"
            symbols.append(Symbol(name=display, depth=0, line_no=i + 1))

        return AdapterResult(symbols=symbols)


register(DockerfileAdapter())
