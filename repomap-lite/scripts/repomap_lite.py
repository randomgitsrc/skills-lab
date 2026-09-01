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

from adapter_base import (  # noqa: E402
    AdapterResult,
    Dependency,
    Symbol,
    adapter_extract_dependencies,
    adapter_says_generated,
    find_adapter_for,
)
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

# 上面这条"只警告不拦截"的策略，在真正超大仓库（数万文件级）上不够用——
# 用户反馈的具体场景：agent 直接把 REPOMAP.md 整个读进上下文，如果这份
# 地图本身是几MB到几十MB（真实测过 TypeScript 编译器仓库，31000+文件，
# 输出到过 13.8MB/44万行这个量级），要么读取工具本身因为文件过大直接
# 失败/截断（agent 拿到一份不完整但自己不知道的内容，反而比"警告后仍然
# 给你完整东西"更危险——早期那条设计理由这里不再成立，因为原来假设的
# "完整但大"的风险，在这个规模下已经变成"读的时候直接读不完整/读不了"，
# 前提条件变了，结论也要跟着变），要么读取本身"成功"但吃掉了对话里几乎
# 全部可用上下文，只为了换来一份没人会真正逐条查看的超长符号列表——
# 花了巨大的 token 成本，边际价值却趋近于零（第29000个文件的符号列表，
# 不会比排在前面的重要文件更值得占用上下文）。
#
# 解决思路不是"更狠地截断"，而是**改变超大仓库场景下默认产出的形态**：
# 索引段（文件路径+符号数，见 render_index）本身开销很小、且不管仓库
# 多大都能完整生成（一个文件一行，纯计数，不含符号细节），真正占体积的
# 是每个文件的完整符号列表 block。累计输出字节数超过下面这个预算之后，
# 后续文件**仍然出现在索引里**（用户/agent 依然能看到"这个文件存在，
# 它有多少符号"这个事实，不会被静默漏掉），但不再展开完整的逐符号 block，
# 只在索引后追加一段简短提示，说明如何针对某个具体文件单独拿到完整细节
# （`--update-file`）或者按目录重新生成范围更小的地图（`--root`）。
#
# 这不是"更狠地截断"，是"把当前的产出形态从'一次性把所有细节都塞进一份
# 文件'换成'先给一份完整但轻量的地图坐标，细节按需单独取'"——前者在
# 仓库规模到达数万文件级时已经不再是一个安全默认值，后者对任何规模的
# 仓库都成立，只是对小仓库而言这个预算几乎不会触发，行为等同于不变。
#
# 具体阈值：以"完整展开后的输出字节数"作为预算基准，不是符号数或行数。
# 这是一次真实的自我纠正：最初这里用"符号总数"做预算单位，理由是"符号数
# 已经算好、更直接反映内容有多少细节"——但用真实 Redis 语料实测验证时
# 发现这个假设不成立：Redis 全量语料约17700个符号对应约1.08MB输出，
# 平均每个符号约62字节，但这个"字节/符号"比例因语言、代码风格差异很大
# （比如 Go 的单行 `func Foo()` 跟 C++ 带多个修饰符和模板参数的签名，
# 字节数可以差好几倍）。用符号数做预算，在验证阶段拿一个平均字节数偏高的
# 语料测试时，50000个符号的预算实际对应了超过3MB的输出——完全没有达到
# "把体积控制在一个安全范围"这个预算机制本来要解决的问题，因为衡量单位
# 从一开始就选错了：真正决定"agent 读这个文件会不会有问题"的是字节数
# 本身，不是符号数这个间接代理指标。改成直接以字节数为预算之后，不管
# 语言/代码风格如何变化，预算达到的效果都是直接、可预期的。
#
# 2MB 这个数字不是精确科学，是"明显超出正常单次对话上下文预算，值得
# 强制降级"的量级——对比：真实测过的 Redis 全量语料（含全部依赖库）约
# 1.1MB，TypeScript 编译器这种数万文件级仓库测出过 13.8MB，2MB 大致是
# 二者之间、能覆盖绝大多数正常大小仓库、又确实会在真正超大仓库上触发
# 降级的分界点。可以用 `--full-detail-budget-bytes` 调整，或者用
# `--force-full-detail` 完全关闭这个降级、强制展开全部内容（用户明确
# 知道自己想要什么、愿意承担相应风险时使用）。
DEFAULT_FULL_DETAIL_BYTE_BUDGET = 2 * 1024 * 1024

# 索引段本身的展示上限（按文件数，不是符号数）。理由见 render_index 的
# 文档字符串——真实测过一万文件规模的仓库，索引段本身能长到约23万字符，
# 即使已经不展开逐符号细节，这个规模也不再是"扫一眼建立心智地图"该有的
# 开销。500 这个数字不是精确科学，是"一次性看完仍然可行、又能覆盖绝大多数
# 中大型仓库真实文件数"的量级；超过这个规模的仓库本身也是
# full_detail_byte_budget 更可能触发的场景，两个机制天然配合。
DEFAULT_INDEX_MAX_ENTRIES = 500


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------

class FileMap:
    __slots__ = ("path", "symbols", "skipped_reason", "adapter_name", "notes", "dependencies")

    def __init__(
        self, path: str, symbols=None, skipped_reason=None, adapter_name=None, notes=None,
        dependencies=None,
    ):
        self.path = path
        self.symbols: list[Symbol] = symbols or []
        self.skipped_reason = skipped_reason
        self.adapter_name = adapter_name
        self.notes: list[str] = notes or []
        self.dependencies: list[Dependency] = dependencies or []


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
    聚合仓库内全部 .gitignore（以及可选的 .repomapignore，见下）文件（含
    嵌套）的规则，提供一个 `is_ignored(path_relative_to_repo_root, is_dir)`
    判断接口。

    每条规则记录它所在的忽略文件相对仓库根目录的目录前缀，判断时只应用
    "规则文件所在目录 == 待判断路径的前缀目录"的规则，这是 git 嵌套
    .gitignore 的核心语义（子目录的规则只影响子树），`.repomapignore`
    复用同一套语义。

    `.repomapignore` 是什么、为什么需要一个独立于 `.gitignore` 的文件：
    `.gitignore` 回答的是"这个路径要不要被 git 追踪"，`.repomapignore`
    回答的是一个不同的问题——"这个路径要不要出现在结构地图里"。两者不总是
    一致：
    - 项目里**确实提交到 git**的第三方代码快照/vendored 依赖、大批量的
      测试 fixture、示例代码、迁移脚本历史记录——这些内容真实存在、
      需要被版本控制，但对"这个项目实际是怎么写的"这个问题没有信息量，
      不应该出现在给 agent 冷启动用的结构地图里。`.gitignore` 对这些
      内容无能为力，因为它们本来就要被 git 追踪。
    - 反过来，如果为了让地图排除它们就把这些路径也写进 `.gitignore`，
      会把"版本控制关心什么"和"地图关心什么"这两个完全不同的意图混在
      同一个文件里，后续任何人看 `.gitignore` 都要多想一层"这条规则是
      为了不追踪，还是只是为了不出现在地图里"，增加维护负担。

    什么时候该用 `.repomapignore`（而不是试图塞进 `.gitignore`）：
    - 内容**已经**被 git 追踪，但不想出现在地图里（上面提到的场景）
    - 内容不满足任何已支持语言的"自动生成文件"内容标记检测
      （见 references/known_limitations.md 的相关章节），但项目组自己
      知道这是生成物/不需要理解的内容
    - 需要针对"这个地图给 agent 用"这个场景单独调整排除范围，而不想
      影响其他工具（IDE、CI、部署脚本）对同一批文件的处理方式

    是否需要自动维护/更新：不需要，也不应该。这是一份跟 `.gitignore`
    地位相同的、项目组主动维护的静态配置——工具不会自动往里面加东西，
    原因是"自动判断某个路径不值得放进地图"本质上是一个需要人类/项目
    意图介入的判断，自动化的话风险是新增的真实代码被意外归类为"不值得
    展示"而悄悄从地图里消失，且没人会注意到，这比"忘记排除某个生成目录、
    地图里多了点噪音"要严重得多。
    """

    IGNORE_FILENAMES = (".gitignore", ".repomapignore")

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        # 每条规则: (适用的目录前缀(相对根目录, '' 表示根目录本身),
        #            正则, 是否否定模式, 是否仅目录)
        self._rules: list[tuple[str, re.Pattern, bool, bool]] = []
        self._load_all_ignore_files()

    def _load_all_ignore_files(self) -> None:
        for dirpath, dirnames, filenames in os.walk(self.repo_root):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            prefix = str(Path(dirpath).relative_to(self.repo_root))
            if prefix == ".":
                prefix = ""
            # 按固定顺序读取（先 .gitignore 再 .repomapignore），保证同一
            # 目录内 .repomapignore 的规则排在 .gitignore 之后——如果两者
            # 都对同一路径有否定模式，后加载的生效，这跟 git 对同一个
            # .gitignore 文件内"后面的规则覆盖前面"的顺序语义保持一致，
            # 只是把这个顺序关系扩展到两个文件之间。
            for ignore_filename in self.IGNORE_FILENAMES:
                if ignore_filename not in filenames:
                    continue
                ignore_path = Path(dirpath) / ignore_filename
                try:
                    content = ignore_path.read_text(encoding="utf-8", errors="ignore")
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
    scan_root: Path,
    repo_root: Path,
    exclude_dirs: set[str],
    max_files: Optional[int] = None,
    gitignore: Optional["GitignoreMatcher"] = None,
):
    """
    遍历 scan_root 子树下的源文件。scan_root 和 repo_root 是两个不同的
    概念，刻意分开：

    - repo_root：`.git` 所在的仓库根目录，`.gitignore`/`.repomapignore`
      的规则本身是相对这个目录写的（git 的真实语义如此），输出里展示的
      文件路径也是相对这个目录的相对路径。
    - scan_root：实际要遍历的起点，可以等于 repo_root（默认情况），也
      可以是仓库内的任意子目录——这是给 monorepo 场景用的："只想看
      packages/some-package/ 这个子项目的结构地图，不想扫全仓库"。

    早期版本没有这个区分，`--root` 参数虽然存在，但 `find_repo_root()`
    总是从给定路径向上找到 `.git` 所在处并把这个结果同时当作"遍历起点"
    和"路径基准"，导致在 `--root packages/pkg-a` 这种子目录场景下，
    实际行为退化成"遍历整个仓库"，`--root` 参数名字暗示的"限定扫描范围"
    完全没有生效——用真实的 monorepo 场景复现确认过这个 bug：从子包
    目录下运行工具，本以为只会得到该子包自己的符号，实际拿到的是整个
    仓库的地图，混入了其他子包的内容。

    修复后，`.gitignore` 判断和输出路径展示继续以 repo_root 为基准
    （保持跟 git 语义一致，也保持 REPOMAP.md 里路径的可读性——即使只扫
    一个子包，路径也应该是 `packages/pkg-a/src/foo.py` 这种完整仓库
    相对路径，而不是把子包自己的相对路径当成仓库根，那样反而更容易让人
    误解这是整个仓库的结构），只有"遍历从哪里开始"改成 scan_root。
    """
    count = 0
    for dirpath, dirnames, filenames in os.walk(scan_root):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs and not d.startswith(".git")]
        if gitignore is not None:
            kept_dirnames = []
            for d in dirnames:
                dir_rel = str((Path(dirpath) / d).relative_to(repo_root))
                if gitignore.is_ignored(dir_rel, is_dir=True):
                    continue
                kept_dirnames.append(d)
            dirnames[:] = kept_dirnames
        for fname in sorted(filenames):
            path = Path(dirpath) / fname
            if gitignore is not None:
                file_rel = str(path.relative_to(repo_root))
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

    # 依赖识别是一个独立于符号提取的可选能力，用同一份安全调用包装
    # （见 adapter_base.adapter_extract_dependencies 的说明），单独 try
    # 一次而不是让它跟符号提取共享同一个 try 块——即使某个适配器的依赖
    # 识别逻辑本身出了问题，也不该影响符号提取这个更核心的能力继续正常
    # 工作（虽然 adapter_extract_dependencies 内部已经吞掉了异常，这里
    # 再包一层纯粹是防御性的，避免未来有人在调用点之间插入代码时不小心
    # 引入新的异常路径）。
    try:
        dependencies = adapter_extract_dependencies(adapter, lines)
    except Exception:  # noqa: BLE001
        dependencies = []

    if not result.symbols and not dependencies:
        # 早期版本这里只看 result.symbols——一个文件如果一个符号都没有
        # 识别出来就整体跳过，不生成 block。这个判断标准现在不够用了：
        # 有些文件符号数量确实是0（比如整个文件只有几行 import/require、
        # 没有任何顶层函数/类定义，或者仅有的函数体是作为参数传入的匿名
        # 回调，本身不会被现有符号规则收录——用真实项目 express 的
        # examples/view-constructor/index.js 复现验证过这个场景真实存在），
        # 但依赖信息仍然是有价值的、不该被一起丢弃的内容。改成"符号和
        # 依赖都是空的才跳过"，只要两者之一有内容就生成 block。
        return FileMap(
            path=rel, skipped_reason="no-symbols", adapter_name=adapter.name, notes=result.notes,
        )

    return FileMap(
        path=rel, symbols=result.symbols, adapter_name=adapter.name, notes=result.notes,
        dependencies=dependencies,
    )


# --------------------------------------------------------------------------
# 渲染输出（对齐 repomap 参考格式）
# --------------------------------------------------------------------------

# internal 依赖逐条列出目标路径时的数量上限——超过这个数字就退化成只给
# 总数，不逐条列出。这是为了避免真的有文件 internal import 了几十个
# 项目内部模块时，这一行本身变得比展示价值应得的更长；这类极端情况本身
# 也是一个值得关注的信号（这个文件可能是某种"聚合入口"，import 很多东西
# 但没有自己的实现），只给数量依然能传达"这个文件跟很多别的文件有关联"
# 这个事实，只是不逐一列出。
DEPENDENCY_INTERNAL_DISPLAY_LIMIT = 8


def render_dependencies_line(dependencies: list[Dependency]) -> str:
    """
    把一个文件的 Dependency 列表压缩成单行展示文本，跟 `<!-- symbols: N -->`
    同一种注释风格。设计目标是"简单、可用、效率"（用户明确提出的三个词）：

    - **简单**：单行、跟已有的 symbols 注释视觉语言一致，agent 不需要
      学一套新格式。
    - **效率**：只有 internal 逐条列出目标路径（这是"文件之间关系"里
      真正有信息量的部分，也是源码之外查不到的增量信息）；external/
      unknown/dynamic 只给数量，不逐条展示 raw_text——用真实项目验证过
      这个必要性：Redis 的 src/server.c 有47条 #include，如果每条都展示
      完整原始文本，这一份"依赖清单"本身就会占用不小的篇幅，而其中大多数
      是标准库头文件，对"理解这个项目实际是怎么写的"这个目标价值很低。
    - **可用**：四种分类的区分度保留（数量分别展示，不合并成一个笼统
      的总数），点进具体文件源码就能看到 unknown/dynamic 到底是哪几行
      （Dependency.line_no 保留了这个信息，只是没有在这个压缩视图里
      展示，需要更细节时可以直接读源文件）。

    没有依赖时返回空字符串（不生成这一行），这是大多数文件的常见情况
    （不是所有文件都会 import 别的东西），不该为了"格式统一"强行展示
    一个空的依赖行。
    """
    if not dependencies:
        return ""

    internal = [d for d in dependencies if d.kind == "internal"]
    external_count = sum(1 for d in dependencies if d.kind == "external")
    unknown_count = sum(1 for d in dependencies if d.kind == "unknown")
    dynamic_count = sum(1 for d in dependencies if d.kind == "dynamic")

    parts = []
    if internal:
        if len(internal) <= DEPENDENCY_INTERNAL_DISPLAY_LIMIT:
            targets = ", ".join(d.target for d in internal if d.target)
            parts.append(f"internal={len(internal)}({targets})")
        else:
            parts.append(f"internal={len(internal)}")
    if external_count:
        parts.append(f"external={external_count}")
    if unknown_count:
        parts.append(f"unknown={unknown_count}")
    if dynamic_count:
        parts.append(f"dynamic={dynamic_count}")

    return f"<!-- deps: {' '.join(parts)} -->"


def render_filemap(fm: FileMap) -> str:
    if fm.skipped_reason is not None:
        return ""  # 空文件/无符号文件/不支持的文件 一律跳过，不生成 block

    out_lines = [f"{fm.path}:", f"<!-- symbols: {len(fm.symbols)} -->"]
    deps_line = render_dependencies_line(fm.dependencies)
    if deps_line:
        out_lines.append(deps_line)
    out_lines.append("⋮")
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


def render_index(entries: list[tuple[str, int]], max_entries: Optional[int] = None) -> str:
    """
    渲染顶部文件清单（索引段）：每个文件的符号数，从多到少排序。

    放在 REPOMAP.md 最顶部（来源标记之后、逐文件 block 之前），让 agent
    冷启动时先看到整个仓库摊开在哪、哪些文件是重点，再按需下钻到具体
    block。符号数是"该文件提取到的符号（函数/类/结构体等）数量"。

    max_entries 是索引本身**展示给人看**的文件数上限（None 表示不设上限，
    展示全部）。这是补上"符号总数预算能防止逐符号 block 无限膨胀，但索引
    本身在文件数量极多的仓库（数万文件级）下依然可能长到几百KB甚至更多"
    这个问题——用真实数据验证过：一个一万文件的仓库，光是索引段就有约
    23万字符，虽然远小于展开全部符号细节的体积，但已经不是"扫一眼"级别
    的开销了。

    截断规则：按符号数从多到少排序后，只展示前 max_entries 个（这些通常
    是最值得关注的文件），其余的**不逐条展示给人看**，但一定要完整保留
    在一段机器可读、人类阅读时会忽略的存档注释里（见下方 _ARCHIVE_MARKER），
    不是彻底不写进文件——这是本文件修复过的一个真实数据丢失 bug 换来的
    设计要求：早期版本"其余的不逐条列出"确实做到了"给人看的部分不冗长"，
    但同时也意味着这些文件的路径+符号数**从磁盘上彻底消失**，后续
    `--update-file` 增量更新时解析已有 REPOMAP.md 完全无法恢复它们的身份
    ——用真实的一万文件测试仓库复现过：仅仅因为这些文件排在索引第500名
    之后（被展示截断，不是被"预算降级"截断——这是两种独立的截断机制，
    详见 parse_existing_map 的说明），一次简单的增量更新就会让它们从地图
    里彻底消失，不是退化成"仅索引条目"，而是连索引条目都没有了。

    "展示给人看的部分"和"机器可读的完整存档"分开维护，解决的正是这个
    问题：人类/agent 正常阅读时只看到简洁的前 max_entries 行 + 一行聚合
    提示，视觉上跟修复前完全一样；但完整的路径+符号数列表始终以机器可读
    格式存在于文件里（用真实数据验证过：一万文件时这部分存档约22万字符，
    这是"保证数据不丢失"必须付出的代价，跟前面 full_detail_byte_budget
    "可以不展开细节但不能让文件的存在消失"是完全同一个原则在索引这一层的
    体现——如果连这份存档都不写，index_max_entries 这个截断机制本身就是
    一个数据丢失的定时炸弹，只是触发条件比 full_detail_byte_budget 更隐蔽）。
    """
    lines = ["<!-- 索引：文件清单 · 符号数（从多到少），供快速定位重点文件 -->"]
    ordered = sorted(entries, key=lambda x: (-x[1], x[0]))
    shown = ordered if max_entries is None else ordered[:max_entries]
    for path, count in shown:
        lines.append(f"{count:3d}  {path}")
    remaining = ordered[len(shown):]
    if remaining:
        remaining_symbol_total = sum(c for _, c in remaining)
        lines.append(
            f"<!-- 还有 {len(remaining)} 个文件（共 {remaining_symbol_total} 个符号）未在索引里逐条列出，"
            f"用 --index-max-entries 调大索引展示上限可以看到更多 -->"
        )
        lines.append(render_index_archive(remaining))
    return "\n".join(lines)


_ARCHIVE_MARKER_OPEN = "<!-- INDEX-ARCHIVE-BEGIN (机器可读，完整保留展示截断之外的文件，人类阅读可忽略这一段)"
_ARCHIVE_MARKER_CLOSE = "INDEX-ARCHIVE-END -->"


def render_index_archive(entries: list[tuple[str, int]]) -> str:
    """
    把"展示截断"之外的 (path, count) 完整序列化进一段 HTML 注释，人类阅读
    REPOMAP.md 时会自然跳过（视觉上就是一段看不太懂的注释块），但
    parse_existing_map 能精确解析回完整的路径+符号数列表，不依赖"数一数
    索引里展示了多少行"这种脆弱的判断。

    格式：每行一条 `count\\tpath`（用 tab 分隔，因为路径本身可能包含空格，
    用固定的多空格分隔在正则解析时不如 tab 明确可靠），整体包在
    _ARCHIVE_MARKER_OPEN/_ARCHIVE_MARKER_CLOSE 之间。
    """
    lines = [_ARCHIVE_MARKER_OPEN]
    for path, count in entries:
        lines.append(f"{count}\t{path}")
    lines.append(_ARCHIVE_MARKER_CLOSE)
    return "\n".join(lines)


def parse_index_archive(content: str) -> list[tuple[str, int]]:
    """解析 render_index_archive 写入的存档段，返回 (path, count) 列表。
    找不到存档段（比如索引本身没有触发展示截断）时返回空列表。"""
    start = content.find(_ARCHIVE_MARKER_OPEN)
    if start == -1:
        return []
    end = content.find(_ARCHIVE_MARKER_CLOSE, start)
    if end == -1:
        return []
    block = content[start + len(_ARCHIVE_MARKER_OPEN) : end]
    result = []
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d+)\t(.+)$", line)
        if m:
            result.append((m.group(2), int(m.group(1))))
    return result


_SYMBOL_COUNT_RE = re.compile(r"^<!-- symbols: (\d+) -->", re.M)


def extract_symbol_count(block: str) -> int:
    """从渲染后的 block 文本提取该文件的符号数。

    优先读 block 内的 `<!-- symbols: N -->` 注释（render_filemap 生成）。
    兼容旧版（升级前生成的 REPOMAP.md 没有该注释）：退回粗略统计
    `│` 前缀行（docstring 行除外）。多行签名会多算几行，仅作索引用，
    精度要求不高；下一次全量生成即恢复精确计数。
    """
    m = _SYMBOL_COUNT_RE.search(block)
    if m:
        return int(m.group(1))
    n = 0
    for line in block.splitlines():
        if line.startswith("│") and not re.match(r"^│\s*(\"\"\"|\'\'\')", line):
            n += 1
    return n


def render_repomap(
    filemaps: list[FileMap],
    full_detail_byte_budget: Optional[int] = DEFAULT_FULL_DETAIL_BYTE_BUDGET,
    index_max_entries: Optional[int] = DEFAULT_INDEX_MAX_ENTRIES,
) -> str:
    """
    渲染完整的 REPOMAP.md 文本。

    full_detail_byte_budget 是"完整展开逐符号 block"的总预算（按渲染后
    的 UTF-8 字节数计），None 表示不设预算、始终展开全部内容（对应
    --force-full-detail）。超过预算之后，后续文件不再展开完整 block，
    但仍然出现在顶部索引里（带真实符号数），并在索引之后追加一段简短
    说明，指出还有多少文件/多少符号被这样处理，以及如何单独获取它们的
    完整细节——保证"这份地图在超大仓库场景下体积可控"的同时，不静默丢失
    任何文件的存在信息，这是跟"直接截断/直接丢弃"的关键区别（见上方
    DEFAULT_FULL_DETAIL_BYTE_BUDGET 的详细说明，包括"为什么用字节数而不是
    符号数"这个真实踩过的坑）。

    预算判断按文件在 filemaps 里出现的顺序累加（不是先排序再决定谁能进
    预算），这样最终决定"哪些文件展开、哪些只留在索引里"的是文件被扫描到
    的顺序（通常是目录遍历的自然顺序），不是"挑出全仓库里最重要的头部
    文件展开、其余全部收起来"这种重新排序——后者虽然听起来更"智能"，但会
    让"是否展开"这件事依赖一次额外的全局排序和阈值判断，语义上更复杂，
    也更难向用户解释"为什么这个文件展开了，那个没有"；按扫描顺序简单累加，
    规则简单直白，用户从命令行参数（--root、--max-files）就能直接控制
    实际效果，不需要额外理解一套排序规则。
    """
    header = [
        SOURCE_MARKER,
        f"<!-- generated_at: {time.strftime('%Y-%m-%d %H:%M:%S')} -->",
        "",
    ]
    blocks = []
    entries = []
    cumulative_bytes = 0
    truncated_file_count = 0
    truncated_symbol_count = 0
    budget_exceeded = False

    for fm in filemaps:
        if fm.skipped_reason is not None:
            continue
        symbol_count = len(fm.symbols)
        entries.append((fm.path, symbol_count))

        if full_detail_byte_budget is not None and cumulative_bytes >= full_detail_byte_budget:
            budget_exceeded = True
            truncated_file_count += 1
            truncated_symbol_count += symbol_count
            continue

        rendered = render_filemap(fm)
        if rendered:
            blocks.append(rendered)
            cumulative_bytes += len(rendered.encode("utf-8"))

    index = render_index(entries, max_entries=index_max_entries) if entries else ""
    body = "\n\n".join(blocks)
    out = "\n".join(header)
    if index:
        out += index + "\n\n"
    if budget_exceeded:
        out += (
            f"<!-- 已展开约 {cumulative_bytes / 1024:.0f}KB 内容（预算 {full_detail_byte_budget / 1024:.0f}KB），"
            f"剩余 {truncated_file_count} 个文件（共 {truncated_symbol_count} 个符号）"
            f"只出现在上方索引里，未展开完整内容。"
            f"针对具体文件单独获取完整细节：--update-file <path>；"
            f"或用 --root 缩小扫描范围重新生成；"
            f"或用 --full-detail-budget-bytes 调大预算；"
            f"或用 --force-full-detail 完全关闭这个限制。 -->\n\n"
        )
    out += body + ("\n" if body else "")
    return out


# --------------------------------------------------------------------------
# 增量更新：解析已有 REPOMAP.md，替换/插入单个文件的 block
# --------------------------------------------------------------------------

def parse_existing_map(content: str) -> tuple[dict[str, str], list[tuple[str, int]]]:
    """解析已有 REPOMAP.md，返回 (blocks, index_only)。

    - blocks: path -> 渲染后的 block 文本（完整展开的文件）
    - index_only: [(path, count), ...] —— 只出现在顶部索引里、没有完整 block
      的文件（预算降级时被截断的那部分）。增量更新重建时这些文件必须保留在
      索引里，否则它们会从地图里静默消失（历史 bug：预算降级 + --update-file
      组合导致被截断文件全部丢失）。

    index_only 的来源有两处，都要读，缺一个都会重新引入数据丢失：
    1. 索引里**展示出来**的那部分（'  NNN  path' 这种行）——对应
       full_detail_byte_budget 触发的"完整细节被截断，但索引里还看得到"
       的文件。
    2. 索引展示本身也被截断（超过 index_max_entries）时，剩余部分被
       完整保留在 render_index_archive 写入的机器可读存档段里——这是
       修复过的第二个真实数据丢失 bug：早期版本只解析第1种来源，导致
       "索引展示截断"和"预算截断"两种机制同时触发的大仓库场景下
       （用真实的一万文件测试仓库复现过），排在索引第 index_max_entries
       名之后的文件在增量更新时被完全遗漏——不是退化成"仅索引条目"，
       是连索引条目都没有，一次 --update-file 就能让数千个文件的存在
       彻底从地图上消失。两处来源分别解析后合并，才能保证"只要文件曾经
       出现过（不管是在可见索引里、还是在存档段里），它的路径+符号数
       就不会丢"。
    """
    blocks: dict[str, str] = {}
    index_only: list[tuple[str, int]] = []
    lines = content.splitlines()

    # 1) 解析顶部索引段里**展示出来**的部分：'<!-- 索引：' 标记之后的
    #    '  NNN  path' 行
    for i, line in enumerate(lines):
        if line.startswith("<!-- 索引："):
            j = i + 1
            while j < len(lines):
                lj = lines[j]
                if lj.startswith("<!--") or lj.strip() == "":
                    break
                m = re.match(r"^\s*(\d+)\s{2,}(\S.*)$", lj)
                if not m:
                    break
                index_only.append((m.group(2), int(m.group(1))))
                j += 1
            break

    # 1b) 解析索引展示截断之外、存档在机器可读注释段里的部分（见上方
    #     docstring 第2点）。这一步不依赖上面那个循环有没有提前 break，
    #     存档段独立查找，两处来源互不影响、可以同时存在。
    index_only.extend(parse_index_archive(content))

    # 2) 解析完整 block（原有逻辑：从第一个非注释非空行开始，按空行切块）
    start_idx = 0
    for idx, line in enumerate(lines):
        if not line.strip().startswith("<!--") and line.strip() != "":
            start_idx = idx
            break
    body = "\n".join(lines[start_idx:])

    raw_blocks = re.split(r"\n\n+", body.strip())
    for rb in raw_blocks:
        rb = rb.strip("\n")
        if not rb:
            continue
        first_line = rb.splitlines()[0]
        if first_line.endswith(":"):
            path = first_line[:-1]
            blocks[path] = rb

    # 3) index_only 去重（理论上可见索引行和存档段不会有重复路径，因为
    #    render_index 按顺序切分、互不重叠，但增量更新场景下解析的是
    #    "别人生成的文件"，防御性去重不吃亏——同一个路径出现多次时，
    #    保留第一次出现的计数），再排除掉已经有完整 block 的文件
    #    （有 block 的以 block 为准，block 里的内容更新、更权威）。
    seen_paths: set[str] = set()
    deduped_index_only: list[tuple[str, int]] = []
    for p, c in index_only:
        if p in seen_paths:
            continue
        seen_paths.add(p)
        deduped_index_only.append((p, c))
    index_only = [(p, c) for p, c in deduped_index_only if p not in blocks]
    return blocks, index_only


def update_single_file(repomap_path: Path, root: Path, update_file: Path, skip_generated: bool = True) -> str:
    """
    增量更新单个文件对应的 block，不应用 render_repomap 里的
    full_detail_byte_budget 截断逻辑——原因：增量更新操作的对象是
    "这一个文件"，不是重新决定"整个仓库里哪些文件该展开、哪些不该"，
    对单文件更新套用一个基于全仓库输出字节数的预算，语义上说不通（如果
    这次更新恰好让累计字节数跨过预算线，突然把这次更新的文件也收起来，
    这个行为对用户来说会很意外，也偏离了"我只是想更新这一个文件"的
    初衷）。全量重新生成（不带 --update-file）时预算逻辑才生效，见
    render_repomap 的说明。

    注意：预算降级后生成的地图里，被截断的文件只存在于顶部索引、没有
    完整 block。增量更新必须把它们从索引里保留下来（parse_existing_map
    返回的 index_only），否则这些文件会从地图里静默消失（历史 bug：
    预算降级 + --update-file 组合导致全部被截断文件丢失）。
    """
    if repomap_path.exists():
        existing = repomap_path.read_text(encoding="utf-8")
    else:
        existing = ""

    blocks, index_only = parse_existing_map(existing) if existing else ({}, [])

    fm = build_filemap(update_file, root, skip_generated=skip_generated)
    rel = fm.path
    rendered = render_filemap(fm)

    if rendered:
        blocks[rel] = rendered
    else:
        blocks.pop(rel, None)
    # rel 若是之前被截断（在 index_only 里），现在已单独展开或删除，
    # 不再以"仅索引"条目保留
    index_only = [(p, c) for p, c in index_only if p != rel]

    header = [
        SOURCE_MARKER,
        f"<!-- generated_at: {time.strftime('%Y-%m-%d %H:%M:%S')} (incremental update: {rel}) -->",
        "",
    ]
    ordered_paths = sorted(blocks.keys())
    entries = [(p, extract_symbol_count(blocks[p])) for p in ordered_paths] + index_only
    # 跟全量生成路径保持一致的默认展示上限，否则单次 --update-file 会让
    # 索引展示突然从"最多500行"变回"全部展示"，行为不可预期——不管走哪条
    # 代码路径，"索引给人看的部分不超过 DEFAULT_INDEX_MAX_ENTRIES 行"这个
    # 承诺都应该成立，超出部分交给 render_index 内置的存档机制处理，不会
    # 因为改用这个更简单的调用而丢数据（详见 render_index/parse_existing_map
    # 的说明）。
    index = render_index(entries, max_entries=DEFAULT_INDEX_MAX_ENTRIES) if entries else ""
    body = "\n\n".join(blocks[p] for p in ordered_paths)
    out = "\n".join(header)
    if index:
        out += index + "\n\n"
    if index_only:
        # 仍有被截断（仅索引）的文件，重新生成降级提示，保持地图诚实
        remaining_total = sum(c for _, c in index_only)
        out += (
            f"<!-- 增量更新后仍有 {len(index_only)} 个文件（共 {remaining_total} 个符号）"
            f"只出现在上方索引里，未展开完整内容。"
            f"针对具体文件单独获取完整细节：--update-file <path>；"
            f"或用 --root 缩小扫描范围重新生成。 -->\n\n"
        )
    out += body + ("\n" if body else "")
    return out


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
                    help="不读取仓库内的 .gitignore 和 .repomapignore 规则（默认会读取并排除"
                         "匹配的文件/目录，跟 --include-vendor 是两套独立的排除机制，"
                         "可以分别关闭；.repomapignore 语法跟 .gitignore 完全一致，用于排除"
                         "\"已被 git 追踪、但不想出现在地图里\"的内容，见 SKILL.md 的说明）")
    p.add_argument("--include-generated", action="store_true",
                    help="不跳过标注了\"Code generated ... DO NOT EDIT\"等自动生成标记的文件"
                         "（默认会跳过这类文件，见 references/known_limitations.md 里"
                         "自动生成文件检测那一节）")
    p.add_argument("--full-detail-budget-bytes", type=int, default=DEFAULT_FULL_DETAIL_BYTE_BUDGET,
                    help=f"完整展开逐符号 block 的总字节数预算，默认 {DEFAULT_FULL_DETAIL_BYTE_BUDGET}"
                         f"（约 {DEFAULT_FULL_DETAIL_BYTE_BUDGET // 1024 // 1024}MB）。"
                         "超出预算的文件仍会出现在顶部索引里（带真实符号数），但不再展开完整内容，"
                         "见 SKILL.md 里\"超大仓库\"一节的说明")
    p.add_argument("--force-full-detail", action="store_true",
                    help="完全关闭 --full-detail-budget-bytes 限制，不管仓库多大都展开全部文件的完整内容"
                         "（可能产出体积很大的文件，明确知道自己需要完整内容时使用）")
    p.add_argument("--index-max-entries", type=int, default=DEFAULT_INDEX_MAX_ENTRIES,
                    help=f"顶部索引最多展示的文件数，默认 {DEFAULT_INDEX_MAX_ENTRIES}（按符号数从多到少排序，"
                         "可见索引只展示前 N 行 + 一行聚合提示，超出部分仍完整存入机器可读存档段、"
                         "增量更新不丢文件；传 0 表示不设上限，展示全部文件）")
    p.add_argument("--update-file", type=str, default=None,
                    help="只重新解析该单个文件并合并回已有 REPOMAP.md（增量更新模式）")
    p.add_argument("--root", type=str, default=".",
                    help="扫描范围的起点，默认为当前目录。仍然会向上查找 .git 所在的仓库根目录"
                         "（.gitignore/.repomapignore 规则和输出里的文件路径始终相对仓库根目录，"
                         "保持跟 git 语义一致），但实际遍历只会限定在这个起点的子树内——用于"
                         "monorepo 场景，只想生成某个子包/子目录自己的结构地图时指定")
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

    scan_root = Path(args.root).resolve()
    repo_root = find_repo_root(scan_root)
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
        try:
            update_target.relative_to(repo_root)
        except ValueError:
            print(
                f"错误：--update-file 指定的文件不在仓库根目录内: {update_target}（仓库根: {repo_root}）。"
                f"增量更新只支持更新仓库内的文件。",
                file=sys.stderr,
            )
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
    for f in iter_source_files(scan_root, repo_root, exclude_dirs, args.max_files, gitignore_matcher):
        filemaps.append(build_filemap(f, repo_root, skip_generated=not args.include_generated))

    content = render_repomap(
        filemaps,
        full_detail_byte_budget=None if args.force_full_detail else args.full_detail_budget_bytes,
        index_max_entries=None if args.index_max_entries == 0 else args.index_max_entries,
    )

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
