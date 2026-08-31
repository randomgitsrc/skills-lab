#!/usr/bin/env python3
"""
repomap_lite.py — 零外部依赖的 REPOMAP 生成器（多语言适配器架构）

用途：agent 冷启动进入新代码库时，快速获取结构化项目地图（顶层函数/类/嵌套结构）。

架构说明：本文件只负责"调度"——遍历文件、找到匹配的语言适配器、渲染输出、
处理增量更新——完全不包含任何具体语言的识别正则。所有语言相关的规则都在
adapters/ 目录下的独立文件里，每种语言/技术栈一个适配器，互不依赖。

当前已注册的适配器（见 adapters/__init__.py）：
- Python (.py)
- JavaScript / TypeScript (.js/.jsx/.mjs/.cjs/.ts/.tsx/.mts/.cts)，含 TS 的
  interface/type/enum
- Go (.go)
- C# (.cs) / Java (.java) / C++含Qt (.cpp/.cc/.cxx/.hpp/.h等)
- Vue 单文件组件 (.vue)，复用 JS/TS 适配器解析 <script> 块
- Shader: GLSL(.glsl/.vert/.frag等) / HLSL(.hlsl/.fx等) / WGSL(.wgsl)

新增语言支持：见 references/adapter_guide.md（本文件不需要任何改动）。

硬约束（务必保持）：
- 不依赖任何第三方库，仅使用 Python 标准库
- 不调用外部 API，不启动常驻服务，不做 embedding/向量检索
- 依赖的外部命令仅限系统自带的 git（用于识别仓库根目录）

本文件由「无依赖正则版」实现，非 tree-sitter 版。识别基于正则 + 花括号/缩进
状态机，已知局限见 references/known_limitations.md（含真实基准对比数据）。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

# 让 adapters 包和 adapter_base 可以被相对当前文件的方式 import，
# 不依赖 PYTHONPATH 设置（保证脚本可以从任意位置直接运行）。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from adapter_base import AdapterResult, Symbol, adapter_says_generated, find_adapter_for  # noqa: E402
import adapters  # noqa: F401,E402  # 触发所有适配器的注册

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

DEFAULT_EXCLUDE_DIRS = {
    "node_modules", "vendor", "dist", "build", ".git", "__pycache__",
    ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache", "target",
    ".next", ".nuxt", "out", "coverage", "bin", "obj",  # bin/obj: C#/Java 常见构建产物目录
}

SOURCE_MARKER = (
    "<!-- REPOMAP-SOURCE: 本文件由无依赖正则版(repomap_lite, 多语言适配器架构)生成，"
    "非 tree-sitter 版。已支持语言见 adapters/ 目录；已知局限见 references/known_limitations.md -->"
)

INDENT_WIDTH = 4  # 输出中统一用 4 空格模拟嵌套

# 输出体积的软提示阈值：超过这个行数就在 stderr 提示用户，而不是默默生成
# 一个几十万行的文件让用户毫无预警地在下游消耗大量 token。这是用真实项目
# 独立评审发现的问题：TypeScript 编译器仓库（约3.1万有效源文件）跑出来的
# REPOMAP.md 达到17MB/44万行，直接违背这个工具"压缩后省 token 快速冷启动"
# 的核心卖点，而 --max-files 只是一个可选参数，不是超过阈值就必须启用的
# 强制安全阀。这里选择"生成完之后警告"而不是"生成前就拒绝/截断"，因为
# 截断可能让用户拿到一份不完整、他们不知道不完整的地图，比一份完整但很大
# 的地图更危险；warn-after 保留完整信息的同时给出足够的信号让用户自己决定
# 要不要用 --max-files 或分批处理。具体阈值（10000行）不是精确科学，
# 是一个"明显偏大，值得引起注意"的量级，用户可以按需调整自己的判断。
LARGE_OUTPUT_LINE_WARNING_THRESHOLD = 10000


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------

class FileMap:
    __slots__ = ("path", "symbols", "skipped_reason", "adapter_name", "notes")

    def __init__(self, path: str, symbols=None, skipped_reason=None, adapter_name=None, notes=None):
        self.path = path
        self.symbols: list[Symbol] = symbols or []
        self.skipped_reason = skipped_reason
        self.adapter_name = adapter_name
        self.notes: list[str] = notes or []


# --------------------------------------------------------------------------
# 文件遍历
# --------------------------------------------------------------------------

def find_repo_root(start: Path) -> Optional[Path]:
    cur = start.resolve()
    for p in [cur, *cur.parents]:
        if (p / ".git").exists():
            return p
    return None


# --------------------------------------------------------------------------
# 自动生成文件检测（内容标记，不依赖目录名/扩展名）
# --------------------------------------------------------------------------
# 目录名黑名单（node_modules/target/dist等）只能挡住"生成物集中放在一个
# 独立目录"这种情况，挡不住另一种常见模式：生成的源码文件跟手写代码
# 混在同一个目录里，靠文件内容自带的"本文件是自动生成的"标记声明身份，
# 而不是靠路径。这在真实项目里非常常见，且跨多种语言——但每种语言的标记
# 格式完全不同（Go 用 `// Code generated ... DO NOT EDIT.` 这样的注释行，
# Python 的 protobuf/gRPC 生成代码用另一种注释文案，Java 有的工具链用
# `@Generated` 注解而不是注释）。
#
# 具体的标记规则**不放在这里**，而是作为每个语言适配器自己的
# `is_generated(lines)` 方法实现（可选，见 adapter_base.py 里
# `LanguageAdapter.is_generated` 的说明和 `adapter_says_generated` 的
# 调用方式）——这跟整个适配器架构的设计原则一致："文件内容的哪种模式
# 算作生成代码"是语言相关的知识，不应该塞进一份跨语言的全局正则列表，
# 否则新增一种语言的生成文件识别就必须改动 repomap_lite.py 核心代码，
# 违背了"新增语言只需要新增一个适配器文件"这条架构承诺。已经实现了
# `is_generated` 的适配器：Go（`// Code generated ... DO NOT EDIT.`，
# 用真实 protoc-gen-go 输出验证）、Python（protobuf/gRPC 的两种标记
# 文案，用真实 grpc_tools.protoc 输出验证）。具体标记正则见各自的
# 适配器文件（`adapters/go_adapter.py`、`adapters/python_adapter.py`），
# 不在这里重复维护第二份。

# --------------------------------------------------------------------------
# .gitignore 支持
# --------------------------------------------------------------------------
# 早期版本只有一份写死的目录名黑名单（node_modules/vendor/dist等），
# 完全不读项目自己的 .gitignore。这在两个方向上都不对：
# 1. 项目自定义命名的生成目录（比如 `custom_generated/`、monorepo 里
#    `packages/*/dist` 这种不叫 `dist` 本身的路径）不会被排除，会被
#    完整扫描并出现在 REPOMAP.md 里，而这些内容本来就不该被关心。
# 2. 反过来，写死的黑名单也可能排除掉某个项目明确想要保留（没写进
#    .gitignore）的同名目录，硬编码的名单没有办法感知项目自己的意图。
#
# 这里实现的是 .gitignore 规则里最常用、价值最高的子集，不是 git 官方
# 那份完整规范（真正完整的 .gitignore 语义相当复杂，包含转义字符、
# `**` 多层通配、否定模式与父目录规则的优先级交互等边界情况）：
# - 逐行读取 pattern，跳过空行和 `#` 注释
# - 支持行尾 `/` 表示"仅匹配目录"
# - 支持前导 `/` 表示"仅相对仓库根目录匹配"（不加则任意深度都可能匹配）
# - 支持 `*`/`?` 通配符（转换成正则）
# - 支持 `!` 开头的否定模式（取消之前规则的排除）
# - 支持嵌套 .gitignore（每个目录自己的 .gitignore 只影响该目录及其子树，
#   这是 git 的真实行为，也是很多monorepo实际依赖的行为）
#
# 已知局限：不处理 `**` 的完整语义（这里按普通通配符处理，可能在深层
# 嵌套的边界情况下跟真实 git 的判断不完全一致）；不处理 .git/info/exclude
# 或全局 gitignore 配置；否定模式的优先级简化为"按声明顺序，后面的规则
# 覆盖前面的"，没有完整实现 git 规范里"父目录已被排除时否定模式失效"
# 这条边界规则。这些局限对绝大多数真实项目的 .gitignore 写法没有影响，
# 覆盖不到的是相对少见的复杂模式。
def _gitignore_pattern_to_regex(pattern: str) -> tuple[re.Pattern, bool, bool]:
    """把一条 .gitignore pattern 转换成 (正则, 仅匹配目录, 仅相对根目录)。"""
    dir_only = pattern.endswith("/")
    if dir_only:
        pattern = pattern[:-1]
    anchored = pattern.startswith("/")
    if anchored:
        pattern = pattern[1:]

    # 转成正则：*  -> 除 / 外任意字符；?  -> 单个非 / 字符；其余字符转义
    regex_parts = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                regex_parts.append(".*")
                i += 2
                continue
            regex_parts.append("[^/]*")
        elif ch == "?":
            regex_parts.append("[^/]")
        else:
            regex_parts.append(re.escape(ch))
        i += 1
    body = "".join(regex_parts)
    if anchored:
        regex = re.compile(rf"^{body}(?:/.*)?$")
    else:
        regex = re.compile(rf"(?:^|.*/){body}(?:/.*)?$")
    return regex, dir_only, anchored


class GitignoreMatcher:
    """
    聚合仓库内全部 .gitignore 文件（含嵌套）的规则，提供一个
    `is_ignored(path_relative_to_repo_root, is_dir)` 判断接口。

    每条规则记录它所在的 .gitignore 文件相对仓库根目录的目录前缀，
    判断时只应用"规则文件所在目录 == 待判断路径的前缀目录"的规则，
    这是 git 嵌套 .gitignore 的核心语义（子目录的规则只影响子树）。
    """

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        # 每条规则: (适用的目录前缀(相对根目录, '' 表示根目录本身),
        #            正则, 是否否定模式, 是否仅目录)
        self._rules: list[tuple[str, re.Pattern, bool, bool]] = []
        self._load_all_gitignores()

    def _load_all_gitignores(self) -> None:
        for dirpath, dirnames, filenames in os.walk(self.repo_root):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            if ".gitignore" not in filenames:
                continue
            gitignore_path = Path(dirpath) / ".gitignore"
            prefix = str(Path(dirpath).relative_to(self.repo_root))
            if prefix == ".":
                prefix = ""
            try:
                content = gitignore_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                negate = line.startswith("!")
                if negate:
                    line = line[1:]
                if not line:
                    continue
                regex, dir_only, _anchored = _gitignore_pattern_to_regex(line)
                self._rules.append((prefix, regex, negate, dir_only))

    def is_ignored(self, rel_path: str, is_dir: bool) -> bool:
        """
        判断 rel_path（相对仓库根目录）是否应该被忽略。

        关键点：不仅要检查 rel_path 自己是否匹配某条规则，还要检查它的
        每一级祖先目录是否被规则标记为"整个目录忽略"——git 的真实语义是
        "一个目录被 .gitignore 排除后，它下面的全部内容（不管子路径本身
        长什么样）都跟着被排除"，只检查 rel_path 自身的写法覆盖不到这一点。

        这是一个真实的正确性 bug：早期实现的注释声称"目录被忽略后所有
        子文件跟着忽略"这件事是成立的，但只是描述了意图，代码里从未真正
        实现——`iter_source_files` 恰好用 os.walk 的目录剪枝规避了这个问题
        （被忽略的目录从不会被下钻，其内部文件自然不会被单独调用
        `is_ignored` 判断到），但如果调用方不是通过 `iter_source_files`
        的这套目录剪枝逻辑、而是直接对一个文件路径调用 `is_ignored`，
        会得到错误的"未被忽略"结果。用真实项目复测确认过这个 bug：直接
        调用 `is_ignored('backend/peekview/static/assets/foo.js',
        is_dir=False)` 在只检查文件自身路径时返回 False，即便
        `backend/peekview/static/` 整个目录已经在 .gitignore 里被排除。
        """
        rel_path = rel_path.replace(os.sep, "/")

        # 先检查每一级祖先目录是否被"仅目录"或普通规则标记为忽略——
        # 只要有任何一级祖先被忽略，整条路径就该被忽略，不需要再看
        # rel_path 自身更具体的规则（父目录一旦被排除，git 不会再深入
        # 查看子路径是否有否定规则试图"救回来"，这也是本文件顶部注释里
        # 提到的"简化，未完整实现父目录已排除时否定模式失效"的那条局限
        # ——这里选择跟这条局限一致的简化处理：祖先目录一旦被判定忽略，
        # 直接返回 True，不再继续检查子路径的否定规则）。
        parts = rel_path.split("/")
        for depth in range(1, len(parts)):
            ancestor = "/".join(parts[:depth])
            if self._is_directly_ignored(ancestor, is_dir=True):
                return True

        return self._is_directly_ignored(rel_path, is_dir)

    def _is_directly_ignored(self, rel_path: str, is_dir: bool) -> bool:
        """只检查 rel_path 自身（不查祖先目录）是否匹配某条规则。"""
        ignored = False
        for prefix, regex, negate, dir_only in self._rules:
            if dir_only and not is_dir:
                continue
            if prefix and not (rel_path == prefix or rel_path.startswith(prefix + "/")):
                continue
            # 规则里的 pattern 是相对该 .gitignore 所在目录写的，匹配时
            # 要去掉前缀部分再比较
            path_for_match = rel_path[len(prefix) + 1 :] if prefix else rel_path
            if regex.match(path_for_match):
                ignored = not negate
        return ignored


def iter_source_files(
    root: Path,
    exclude_dirs: set[str],
    max_files: Optional[int] = None,
    gitignore: Optional["GitignoreMatcher"] = None,
):
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs and not d.startswith(".git")]
        if gitignore is not None:
            kept_dirnames = []
            for d in dirnames:
                dir_rel = str((Path(dirpath) / d).relative_to(root))
                if gitignore.is_ignored(dir_rel, is_dir=True):
                    continue
                kept_dirnames.append(d)
            dirnames[:] = kept_dirnames
        for fname in sorted(filenames):
            path = Path(dirpath) / fname
            if gitignore is not None:
                file_rel = str(path.relative_to(root))
                if gitignore.is_ignored(file_rel, is_dir=False):
                    continue
            if find_adapter_for(path) is not None:
                if max_files is not None and count >= max_files:
                    return
                count += 1
                yield path


# --------------------------------------------------------------------------
# 单文件处理
# --------------------------------------------------------------------------

def build_filemap(path: Path, root: Path, skip_generated: bool = True) -> FileMap:
    rel = str(path.relative_to(root))
    adapter = find_adapter_for(path)
    if adapter is None:
        return FileMap(path=rel, skipped_reason="unsupported")

    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return FileMap(path=rel, skipped_reason="read-error")

    if not text.strip():
        return FileMap(path=rel, skipped_reason="empty")

    lines = text.splitlines(keepends=True)

    if skip_generated and adapter_says_generated(adapter, lines):
        return FileMap(path=rel, skipped_reason="generated-file")

    try:
        result: AdapterResult = adapter.extract_symbols(path, lines)
    except Exception as e:  # noqa: BLE001 — 单个适配器出错不应中断整个扫描
        return FileMap(path=rel, skipped_reason=f"adapter-error({adapter.name}: {e})")

    if not result.symbols:
        return FileMap(path=rel, skipped_reason="no-symbols", adapter_name=adapter.name, notes=result.notes)

    return FileMap(path=rel, symbols=result.symbols, adapter_name=adapter.name, notes=result.notes)


# --------------------------------------------------------------------------
# 渲染输出（对齐 repomap 参考格式）
# --------------------------------------------------------------------------

def render_filemap(fm: FileMap) -> str:
    if fm.skipped_reason is not None:
        return ""  # 空文件/无符号文件/不支持的文件 一律跳过，不生成 block

    out_lines = [f"{fm.path}:", "⋮"]
    for sym in fm.symbols:
        indent = " " * (INDENT_WIDTH * sym.depth)
        # sym.name 通常是单行，但个别适配器（例如 C 系语言里"返回类型独占一行，
        # 函数名在下一行"的写法）会在 name 里嵌入换行来展示完整的多行签名。
        # 这里按换行拆开，确保每一行都有自己的 │ 前缀，不会破坏输出格式
        # （下游解析器，包括增量更新逻辑和外部的对比脚本，都假设每一行内容
        # 都以 │ 开头）。
        for name_line in sym.name.split("\n"):
            out_lines.append(f"│{indent}{name_line}")
        if sym.docstring is not None:
            out_lines.append(f'│{indent}    """{sym.docstring}"""')
    out_lines.append("⋮")
    return "\n".join(out_lines)


def render_repomap(filemaps: list[FileMap]) -> str:
    header = [
        SOURCE_MARKER,
        f"<!-- generated_at: {time.strftime('%Y-%m-%d %H:%M:%S')} -->",
        "",
    ]
    blocks = []
    for fm in filemaps:
        rendered = render_filemap(fm)
        if rendered:
            blocks.append(rendered)
    return "\n".join(header) + "\n\n".join(blocks) + ("\n" if blocks else "")


# --------------------------------------------------------------------------
# 增量更新：解析已有 REPOMAP.md，替换/插入单个文件的 block
# --------------------------------------------------------------------------

def parse_existing_blocks(content: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    body_lines = content.splitlines()
    start_idx = 0
    for idx, line in enumerate(body_lines):
        if not line.strip().startswith("<!--") and line.strip() != "":
            start_idx = idx
            break
    body = "\n".join(body_lines[start_idx:])

    import re

    raw_blocks = re.split(r"\n\n+", body.strip())
    for rb in raw_blocks:
        rb = rb.strip("\n")
        if not rb:
            continue
        first_line = rb.splitlines()[0]
        if first_line.endswith(":"):
            path = first_line[:-1]
            blocks[path] = rb
    return blocks


def update_single_file(repomap_path: Path, root: Path, update_file: Path, skip_generated: bool = True) -> str:
    if repomap_path.exists():
        existing = repomap_path.read_text(encoding="utf-8")
    else:
        existing = ""

    blocks = parse_existing_blocks(existing) if existing else {}

    fm = build_filemap(update_file, root, skip_generated=skip_generated)
    rel = fm.path
    rendered = render_filemap(fm)

    if rendered:
        blocks[rel] = rendered
    else:
        blocks.pop(rel, None)

    header = [
        SOURCE_MARKER,
        f"<!-- generated_at: {time.strftime('%Y-%m-%d %H:%M:%S')} (incremental update: {rel}) -->",
        "",
    ]
    ordered_paths = sorted(blocks.keys())
    body = "\n\n".join(blocks[p] for p in ordered_paths)
    return "\n".join(header) + (body + "\n" if body else "")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="repomap_lite",
        description="零外部依赖的多语言 REPOMAP 生成器（正则+适配器架构，非 tree-sitter 版）",
    )
    p.add_argument("-o", "--output", type=str, default=None,
                    help="输出文件路径，默认输出到 stdout")
    p.add_argument("--max-files", type=int, default=None,
                    help="限制处理的文件数量")
    p.add_argument("--include-vendor", action="store_true",
                    help="不排除 node_modules/vendor/dist/.git/bin/obj 等默认目录")
    p.add_argument("--no-gitignore", action="store_true",
                    help="不读取仓库内的 .gitignore 规则（默认会读取并排除匹配的文件/目录，"
                         "跟 --include-vendor 是两套独立的排除机制，可以分别关闭）")
    p.add_argument("--include-generated", action="store_true",
                    help="不跳过标注了\"Code generated ... DO NOT EDIT\"等自动生成标记的文件"
                         "（默认会跳过这类文件，见 references/known_limitations.md 里"
                         "自动生成文件检测那一节）")
    p.add_argument("--update-file", type=str, default=None,
                    help="只重新解析该单个文件并合并回已有 REPOMAP.md（增量更新模式）")
    p.add_argument("--root", type=str, default=".",
                    help="起始目录，默认为当前目录（会向上查找 git 仓库根目录）")
    p.add_argument("--list-adapters", action="store_true",
                    help="列出当前已注册的语言适配器及其覆盖的扩展名，然后退出")
    return p


def _print_adapter_list() -> None:
    from adapter_base import all_adapters

    print("已注册的语言适配器：")
    for a in all_adapters():
        print(f"  - {a.name}")


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.list_adapters:
        _print_adapter_list()
        return 0

    start_path = Path(args.root).resolve()
    repo_root = find_repo_root(start_path)
    if repo_root is None:
        print(
            "错误：未找到 .git 目录，本工具需要在 git 仓库内（或其子目录）运行。",
            file=sys.stderr,
        )
        return 1

    output_path = Path(args.output).resolve() if args.output else (repo_root / "REPOMAP.md")

    if args.update_file:
        update_target = Path(args.update_file)
        if not update_target.is_absolute():
            update_target = (Path.cwd() / update_target).resolve()
        if not update_target.exists():
            print(f"错误：--update-file 指定的文件不存在: {update_target}", file=sys.stderr)
            return 1
        if find_adapter_for(update_target) is None:
            print(f"警告：{update_target} 没有匹配的语言适配器，跳过", file=sys.stderr)
            return 1

        new_content = update_single_file(
            output_path, repo_root, update_target, skip_generated=not args.include_generated
        )
        output_path.write_text(new_content, encoding="utf-8")
        print(f"已增量更新: {output_path}", file=sys.stderr)
        return 0

    exclude_dirs = set() if args.include_vendor else DEFAULT_EXCLUDE_DIRS
    gitignore_matcher = None if args.no_gitignore else GitignoreMatcher(repo_root)
    filemaps = []
    for f in iter_source_files(repo_root, exclude_dirs, args.max_files, gitignore_matcher):
        filemaps.append(build_filemap(f, repo_root, skip_generated=not args.include_generated))

    content = render_repomap(filemaps)

    line_count = content.count("\n")
    if line_count > LARGE_OUTPUT_LINE_WARNING_THRESHOLD:
        byte_count = len(content.encode("utf-8"))
        print(
            f"警告：生成的地图较大（约 {line_count} 行，{byte_count / 1024 / 1024:.1f}MB），"
            f"可能会消耗较多下游 token。建议：",
            file=sys.stderr,
        )
        print(
            "  1. 用 --max-files 限制处理的文件数量做抽样；",
            file=sys.stderr,
        )
        print(
            "  2. 或对仓库按目录分批调用 --root 指定子目录，分别生成多份地图；",
            file=sys.stderr,
        )
        print(
            "  3. 如果确实需要完整地图，这个警告可以忽略，输出内容本身没有被截断。",
            file=sys.stderr,
        )

    if args.output:
        Path(args.output).write_text(content, encoding="utf-8")
        print(f"已写入: {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(content)

    return 0


if __name__ == "__main__":
    sys.exit(main())
