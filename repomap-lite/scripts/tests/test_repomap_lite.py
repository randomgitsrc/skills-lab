"""repomap_lite 核心逻辑测试：预算降级、索引上限、增量更新保留 index_only。

覆盖三类行为（对应 SKILL.md「超大仓库」一节）：
1. render_repomap 的字节预算降级：超预算后不再展开完整 block，但文件仍出现在索引里；
2. render_index 的索引条目上限与聚合提示；
3. --update-file 增量更新在降级地图上必须保留 index_only 条目（历史 bug 回归）。

测试隔离：全部走 tmp_path，不触碰真实仓库；通过 main() 端到端验证为主，
便于在任意语言/风格下保持可运行。
"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import repomap_lite as rl  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def make_repo(tmp_path: Path, file_count: int = 150, symbols_per_file: int = 30) -> Path:
    """构造一个 git 仓库，内含 file_count 个 Python 文件，每个若干符号。

    每个文件带一个较长 docstring，让字节数可控地累积，便于触发预算降级。
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    for i in range(file_count):
        lines = [f"# file {i}", "import os", ""]
        for j in range(symbols_per_file):
            lines.append(f"def func_{i}_{j}(a, b, c, d):")
            lines.append(f'    """function {i}-{j} with fairly long docstring to add bytes here"""')
            lines.append(f"    return a + b + c + d + {i} + {j}")
            lines.append("")
        (repo / f"mod_{i:03d}.py").write_text("\n".join(lines), encoding="utf-8")
    return repo


def index_entries(content: str) -> list[tuple[str, int]]:
    """从渲染文本里提取顶部索引条目 (path, count)。"""
    entries = []
    for line in content.splitlines():
        m = rl.re.match(r"^\s*(\d+)\s{2,}(\S.*)$", line)
        if m and not line.startswith("<!--"):
            entries.append((m.group(2), int(m.group(1))))
    return entries


def full_block_count(content: str) -> int:
    """统计展开的完整 block 数。

    每个完整展开的 block 恰好带一行 `<!-- symbols: N -->`（见 render_filemap），
    用它计数最稳——不能数 'path:' 行，因为 `│def ...:` 这类符号行也会以
    ':' 结尾，会误计。
    """
    return content.count("<!-- symbols:")


# ---------------------------------------------------------------------------
# render_index：排序与索引上限
# ---------------------------------------------------------------------------

class TestRenderIndex:
    def test_sorts_desc_by_symbol_count(self):
        entries = [("b.py", 3), ("a.py", 10), ("c.py", 7)]
        out = rl.render_index(entries)
        lines = [l for l in out.splitlines() if not l.startswith("<!--")]
        assert lines[0].startswith(" 10  a.py")
        assert lines[1].startswith("  7  c.py")
        assert lines[2].startswith("  3  b.py")

    def test_max_entries_truncates_with_aggregate_note(self):
        entries = [(f"f{i:02d}.py", i) for i in range(1, 11)]  # 10 个文件
        out = rl.render_index(entries, max_entries=3)
        listed = [l for l in out.splitlines() if not l.startswith("<!--") and "  f" in l]
        assert len(listed) == 3
        # 聚合提示交代剩余文件与符号总数
        assert "还有 7 个文件" in out
        assert "共 28 个符号" in out  # 4+5+6+7 被截掉的是 1+2+3+4+5+6+7=28

    def test_none_max_entries_means_unlimited(self):
        entries = [(f"f{i:02d}.py", i) for i in range(1, 6)]
        out = rl.render_index(entries, max_entries=None)
        listed = [l for l in out.splitlines() if not l.startswith("<!--") and "  f" in l]
        assert len(listed) == 5
        assert "未在索引里逐条列出" not in out


# ---------------------------------------------------------------------------
# render_repomap：字节预算降级
# ---------------------------------------------------------------------------

class TestRenderRepomap:
    def test_budget_truncates_blocks_but_keeps_index(self, tmp_path):
        repo = make_repo(tmp_path)
        out_path = tmp_path / "REPOMAP.md"
        rc = rl.main(["--root", str(repo), "-o", str(out_path),
                      "--full-detail-budget-bytes", "100000"])
        assert rc == 0
        content = out_path.read_text(encoding="utf-8")
        entries = index_entries(content)
        # 索引保留全部 150 个文件
        assert len(entries) == 150
        # 完整 block 被截断（远小于 150）
        assert full_block_count(content) < 150
        # 有降级提示
        assert "未展开完整内容" in content

    def test_force_full_detail_expands_everything(self, tmp_path):
        repo = make_repo(tmp_path)
        out_path = tmp_path / "REPOMAP.md"
        rc = rl.main(["--root", str(repo), "-o", str(out_path),
                      "--force-full-detail"])
        assert rc == 0
        content = out_path.read_text(encoding="utf-8")
        assert full_block_count(content) == 150
        assert "未展开完整内容" not in content

    def test_large_budget_no_truncation(self, tmp_path):
        repo = make_repo(tmp_path, file_count=10, symbols_per_file=5)
        out_path = tmp_path / "REPOMAP.md"
        rc = rl.main(["--root", str(repo), "-o", str(out_path),
                      "--full-detail-budget-bytes", "10000000"])
        assert rc == 0
        content = out_path.read_text(encoding="utf-8")
        assert full_block_count(content) == 10
        assert "未展开完整内容" not in content


# ---------------------------------------------------------------------------
# --update-file：降级地图上的增量更新必须保留 index_only（历史 bug 回归）
# ---------------------------------------------------------------------------

class TestIncrementalUpdate:
    def test_truncated_files_survive_incremental_update(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        out_path = tmp_path / "REPOMAP.md"
        rc = rl.main(["--root", str(repo), "-o", str(out_path),
                      "--full-detail-budget-bytes", "100000"])
        assert rc == 0
        before = out_path.read_text(encoding="utf-8")
        assert len(index_entries(before)) == 150
        assert full_block_count(before) < 150

        # 真实用法：从仓库根目录内运行 --update-file（repo_root 由 cwd 推导）
        monkeypatch.chdir(repo)
        rc = rl.main(["-o", str(out_path), "--update-file", "mod_099.py"])
        assert rc == 0
        after = out_path.read_text(encoding="utf-8")

        # 索引条目数不变：被截断文件未被静默丢弃
        assert len(index_entries(after)) == 150
        # mod_099 现在获得完整 block（原本仅索引）
        assert "mod_099.py:" in after
        # 其他被截断文件仍以索引条目存在
        assert any(p == "mod_140.py" for p, _ in index_entries(after))
        # 降级提示保留
        assert "未展开完整内容" in after

    def test_incremental_update_beyond_index_display_cap_does_not_lose_files(self, tmp_path, monkeypatch):
        """
        回归测试：修复此前一个真实的数据丢失 bug。

        render_index 本身有两层独立的截断机制：
        1. full_detail_byte_budget 控制"完整展开多少字节的逐符号内容"
        2. index_max_entries 控制"索引本身展示给人看多少行"（默认500）

        早期版本的 parse_existing_map 只解析索引里**展示出来**的那部分，
        当文件数超过 index_max_entries 时，排在展示截断之外的文件的
        (path, count) 从未被写进任何机器可读的位置——用真实的一万文件
        测试仓库复现过：一次简单的 --update-file 会让数千个文件（不是
        因为 full_detail_byte_budget 而没有完整 block 的那部分，而是
        因为排在索引第 index_max_entries 名之后而"仅索引"信息本身都不存在
        的那部分）从地图上彻底消失，不是退化成"仅索引条目"，是连索引
        条目都没有了。

        这里用一个刻意超过 DEFAULT_INDEX_MAX_ENTRIES（默认500）的文件数
        （600个）复现：确保即使触发了索引展示截断，增量更新前后
        parse_existing_map 解析出的 (blocks + index_only) 总数始终等于
        真实文件数，一个都不能少。
        """
        file_count = 600
        repo = make_repo(tmp_path, file_count=file_count)
        out_path = tmp_path / "REPOMAP.md"
        # 用一个足够小的字节预算，确保同时触发 full_detail_byte_budget
        # 截断（不是所有文件都能拿到完整 block）——这样能同时验证两层
        # 截断机制叠加时都不丢数据，而不只是单独验证索引展示截断这一层。
        rc = rl.main(["--root", str(repo), "-o", str(out_path),
                      "--full-detail-budget-bytes", "50000"])
        assert rc == 0
        before = out_path.read_text(encoding="utf-8")
        blocks_before, index_only_before = rl.parse_existing_map(before)
        total_before = len(blocks_before) + len(index_only_before)
        assert total_before == file_count, (
            f"生成后应该追踪到全部 {file_count} 个文件，实际 {total_before} 个"
        )

        monkeypatch.chdir(repo)
        rc = rl.main(["-o", str(out_path), "--update-file", "mod_599.py"])
        assert rc == 0
        after = out_path.read_text(encoding="utf-8")
        blocks_after, index_only_after = rl.parse_existing_map(after)
        total_after = len(blocks_after) + len(index_only_after)
        assert total_after == file_count, (
            f"增量更新后应该仍然追踪到全部 {file_count} 个文件，"
            f"实际只有 {total_after} 个——文件在更新过程中丢失了"
        )
        # 被更新的文件本身应该拿到完整 block（不管它更新前是哪种状态）
        assert "mod_599.py" in blocks_after

    def test_incremental_index_matches_full_regeneration(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        out_path = tmp_path / "REPOMAP.md"
        rc = rl.main(["--root", str(repo), "-o", str(out_path),
                      "--full-detail-budget-bytes", "100000"])
        assert rc == 0
        monkeypatch.chdir(repo)
        rc = rl.main(["-o", str(out_path), "--update-file", "mod_099.py"])
        assert rc == 0
        after = index_entries(out_path.read_text(encoding="utf-8"))

        # 全量重新生成，索引应逐条一致
        fresh_path = tmp_path / "fresh.md"
        rc = rl.main(["--root", str(repo), "-o", str(fresh_path),
                      "--full-detail-budget-bytes", "100000"])
        assert rc == 0
        fresh = index_entries(fresh_path.read_text(encoding="utf-8"))
        assert after == fresh

    def test_update_file_outside_repo_returns_friendly_error(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path, file_count=2, symbols_per_file=1)
        outside = tmp_path / "outside.py"
        outside.write_text("def x(): pass\n", encoding="utf-8")
        out_path = tmp_path / "REPOMAP.md"
        rc = rl.main(["--root", str(repo), "-o", str(out_path)])
        assert rc == 0
        # 仓库外文件：不应抛裸 ValueError，而是友好错误 + 退出码 1
        monkeypatch.chdir(repo)
        rc = rl.main(["-o", str(out_path), "--update-file", str(outside)])
        assert rc == 1

    def test_update_file_missing_returns_friendly_error(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path, file_count=1, symbols_per_file=1)
        out_path = tmp_path / "REPOMAP.md"
        rc = rl.main(["--root", str(repo), "-o", str(out_path)])
        assert rc == 0
        monkeypatch.chdir(repo)
        rc = rl.main(["-o", str(out_path), "--update-file", "missing.py"])
        assert rc == 1


# ---------------------------------------------------------------------------
# parse_existing_map：解析完整 block 与 index_only 条目
# ---------------------------------------------------------------------------

class TestParseExistingMap:
    def test_extracts_blocks_and_index_only(self, tmp_path):
        repo = make_repo(tmp_path)
        out_path = tmp_path / "REPOMAP.md"
        rc = rl.main(["--root", str(repo), "-o", str(out_path),
                      "--full-detail-budget-bytes", "100000"])
        assert rc == 0
        content = out_path.read_text(encoding="utf-8")
        blocks, index_only = rl.parse_existing_map(content)

        # 有完整 block 的文件进入 blocks
        assert blocks, "降级地图上至少应有一些完整 block"
        # index_only = 索引里出现但没有完整 block 的文件
        idx = dict(index_entries(content))
        assert len(index_only) > 0, "降级地图上应存在仅索引文件"
        for p, c in index_only:
            assert p in idx, f"{p} 应在索引里"
            assert p not in blocks, f"{p} 不应同时有完整 block"
            assert c == idx[p], f"{p} 的符号数应一致"

    def test_plain_map_no_index_only(self, tmp_path):
        repo = make_repo(tmp_path, file_count=3, symbols_per_file=1)
        out_path = tmp_path / "REPOMAP.md"
        rc = rl.main(["--root", str(repo), "-o", str(out_path)])
        assert rc == 0
        blocks, index_only = rl.parse_existing_map(out_path.read_text(encoding="utf-8"))
        assert len(blocks) == 3
        assert index_only == []


# ---------------------------------------------------------------------------
# 字符串字面量展示：屏蔽后的文本只能用于结构判断，不能用于展示
# ---------------------------------------------------------------------------
# 回归测试：修复一个真实的、独立评审报告点名的缺陷——被识别为符号的定义行
# 如果本身包含字符串字面量，早期版本会把屏蔽后（内容被替换成等宽空格）的
# 文本当作展示文本，导致引号完整保留、内容被静默抹掉，看起来格式完整、
# 实际信息已经丢失（例如 `path.resolve(HERE, '..')` 展示成
# `path.resolve('  ')`）。这类问题不会反映在符号名称层面的 precision/recall
# 指标上（被抹掉的字符串通常不影响符号名本身能否被正确匹配），只能通过
# 直接检查展示文本内容是否忠实于源码来发现，所以单独写测试而不是依赖
# 已有的统计类回归测试。

from adapters.c_family_adapter import CFamilyAdapter, DIALECTS  # noqa: E402
from adapters.js_ts_adapter import JsTsAdapter  # noqa: E402


class TestStringLiteralDisplay:
    def test_js_top_level_string_literal_shown_intact(self):
        code = (
            "import path from 'path';\n"
            "\n"
            "const ROOT_DIR = path.resolve('..');\n"
            "export const CONFIG_PATH = '../config/settings.json';\n"
        )
        adapter = JsTsAdapter()
        result = adapter.extract_symbols("test.mjs", code.splitlines(keepends=True))
        names = [s.name for s in result.symbols]
        assert any("path.resolve('..')" in n for n in names), (
            f"字符串内容应该原样展示，不应被抹成空白占位符；实际: {names}"
        )
        assert any("'../config/settings.json'" in n for n in names), (
            f"字符串内容应该原样展示，不应被抹成空白占位符；实际: {names}"
        )

    def test_js_type_alias_with_string_literals_shown_intact(self):
        code = 'export type Status = "pending" | "done" | "failed"\n'
        adapter = JsTsAdapter()
        result = adapter.extract_symbols("test.ts", code.splitlines(keepends=True))
        names = [s.name for s in result.symbols]
        assert any('"pending"' in n and '"done"' in n and '"failed"' in n for n in names), (
            f"type alias 里的字符串字面量应该原样展示；实际: {names}"
        )

    def test_c_family_default_param_string_shown_intact(self):
        code = (
            'void logMessage(const char *prefix = "DEFAULT_PREFIX") {\n'
            "}\n"
        )
        adapter = CFamilyAdapter(DIALECTS["cpp"])
        result = adapter.extract_symbols("test.cpp", code.splitlines(keepends=True))
        names = [s.name for s in result.symbols]
        assert any('"DEFAULT_PREFIX"' in n for n in names), (
            f"默认参数里的字符串字面量应该原样展示；实际: {names}"
        )

    def test_c_family_split_style_signature_string_shown_intact(self):
        """跨行拼接的展示文本（返回类型独占一行）同样要用未屏蔽的原始文本。"""
        code = (
            "static const char *\n"
            'getDefaultPath(const char *suffix, const char *base = "/usr/local")\n'
            "{\n"
            "    return base;\n"
            "}\n"
        )
        adapter = CFamilyAdapter(DIALECTS["cpp"])
        result = adapter.extract_symbols("test.c", code.splitlines(keepends=True))
        names = [s.name for s in result.symbols]
        assert any('"/usr/local"' in n for n in names), (
            f"跨行拼接的展示文本里，字符串字面量也应该原样展示；实际: {names}"
        )

    def test_masking_still_prevents_structural_corruption(self):
        """确认展示层的修复没有削弱屏蔽机制本身的结构性正确性——这是历史上
        真实出过 bug 的三个 adversarial 场景（撇号注释、*_/*_ 字符串、
        字符串里的花括号），修复展示逻辑后必须仍然全部正确处理。"""
        code = (
            "/* the caller's buffer */\n"
            "void safeFunction(void) {\n"
            "}\n"
            "\n"
            'void anotherFunction(const char *pattern = "*/*") {\n'
            "}\n"
            "\n"
            'const char *WITH_BRACES = "contains {curly} braces";\n'
            "\n"
            "void thirdFunction(void) {\n"
            "}\n"
        )
        adapter = CFamilyAdapter(DIALECTS["cpp"])
        result = adapter.extract_symbols("test.c", code.splitlines(keepends=True))
        names = [s.name for s in result.symbols]
        # 三个函数都应该被正确识别，没有因为撇号/星号斜杠/花括号误判而丢失
        # 或产生虚假的深度嵌套
        assert any("safeFunction" in n for n in names)
        assert any("anotherFunction" in n for n in names)
        assert any("thirdFunction" in n for n in names)
        # anotherFunction 的字符串内容也应该正确展示（既不被误判打断结构，
        # 也不该被抹成占位符）
        assert any('"*/*"' in n for n in names)


# ---------------------------------------------------------------------------
# 依赖识别（extract_dependencies）：每种语言的四分类判断，以及渲染集成
# ---------------------------------------------------------------------------
# Dependency.kind 的四分类（跨语言统一使用，见 adapter_base.py 的说明）：
#   internal — 目标明确，确定是项目内部文件
#   external — 目标明确，确定是第三方包/系统库
#   unknown  — 目标明确，但内部/外部归类信息不足，不能瞎猜
#   dynamic  — 目标本身是运行时变量，无法静态解析
#
# 每种语言的测试都覆盖了实现过程中真实验证过的关键场景，不是泛泛的
# happy path：Go 的"裸路径不能等同标准库"（曾经真实产生误判）、
# JS 的"require 不要求行首"（真实 lodash 案例）、C 的"条件编译分支
# 内的 include 需要标注"（真实 Redis 案例）等。

from adapters.go_adapter import GoAdapter  # noqa: E402
from adapters.python_adapter import PythonAdapter  # noqa: E402
from adapters.rust_adapter import RustAdapter  # noqa: E402
from adapters.ruby_adapter import RubyAdapter  # noqa: E402


def _kinds_by_target(deps, target):
    return [d.kind for d in deps if d.target == target]


class TestDependencyExtractionGo:
    def test_stdlib_external_and_unknown_and_third_party(self):
        code = (
            'package main\n'
            '\n'
            'import "fmt"\n'
            '\n'
            'import (\n'
            '\t"os"\n'
            '\t"encoding/json"\n'
            '\n'
            '\tmyalias "github.com/foo/bar"\n'
            '\t"goreal/internal/config"\n'
            ')\n'
        )
        adapter = GoAdapter()
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert _kinds_by_target(deps, "fmt") == ["external"]
        assert _kinds_by_target(deps, "os") == ["external"]
        assert _kinds_by_target(deps, "encoding/json") == ["external"]
        assert _kinds_by_target(deps, "github.com/foo/bar") == ["unknown"]
        # 回归测试：早期实现用"不含域名点号的裸路径=标准库"这条正则
        # 启发式判断，会把项目自己的 module 名（这里假设是 goreal）
        # 误判成标准库，因为语法形态上跟真标准库包名（fmt/os）完全无法
        # 区分。改用精确匹配一份真实标准库清单后，这种项目自己的内部
        # 包路径必须正确归为 unknown，不能归 external。
        assert _kinds_by_target(deps, "goreal/internal/config") == ["unknown"]

    def test_new_stdlib_package_recognized(self):
        """确认较新的标准库包（Go 1.21+ 加入）也在清单里。"""
        code = 'import "slices"\n'
        adapter = GoAdapter()
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert _kinds_by_target(deps, "slices") == ["external"]


class TestDependencyExtractionPython:
    def test_relative_import_is_internal(self):
        code = "from . import helper\nfrom ..utils import thing\n"
        adapter = PythonAdapter()
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert _kinds_by_target(deps, ".") == ["internal"]
        assert _kinds_by_target(deps, "..utils") == ["internal"]

    def test_stdlib_external(self):
        code = "import os\nimport sys\nfrom pathlib import Path\n"
        adapter = PythonAdapter()
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert _kinds_by_target(deps, "os") == ["external"]
        assert _kinds_by_target(deps, "pathlib") == ["external"]

    def test_absolute_third_party_is_unknown(self):
        code = "from mypackage.core import Something\n"
        adapter = PythonAdapter()
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert _kinds_by_target(deps, "mypackage.core") == ["unknown"]

    def test_dynamic_import_with_variable_is_dynamic(self):
        code = "mod = importlib.import_module(dynamic_name)\n"
        adapter = PythonAdapter()
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert len(deps) == 1
        assert deps[0].kind == "dynamic"
        assert deps[0].target is None

    def test_dynamic_import_with_literal_resolves_target(self):
        code = "mod = importlib.import_module('foo.bar')\n"
        adapter = PythonAdapter()
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert len(deps) == 1
        assert deps[0].target == "foo.bar"
        assert deps[0].kind != "dynamic"

    def test_triple_quoted_docstring_decoy_ignored(self):
        """三引号文档字符串里提到的 import 不应被误判为真实依赖
        （回归测试：这是本项目一贯强调的"注释/字符串边界判断"教训
        在依赖识别这个新功能上的应用）。"""
        code = (
            "def foo():\n"
            "    '''\n"
            "    Example: import something_fake\n"
            "    '''\n"
            "    pass\n"
        )
        adapter = PythonAdapter()
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert deps == []


class TestDependencyExtractionJsTs:
    def test_esm_import_relative_is_internal(self):
        code = "import { helper } from '../utils';\n"
        adapter = JsTsAdapter()
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert _kinds_by_target(deps, "../utils") == ["internal"]

    def test_esm_import_bare_package_is_external(self):
        code = "import React from 'react';\nimport { z } from '@babel/core';\n"
        adapter = JsTsAdapter()
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert _kinds_by_target(deps, "react") == ["external"]
        assert _kinds_by_target(deps, "@babel/core") == ["external"]

    def test_require_not_at_line_start_is_still_found(self):
        """回归测试：真实案例（lodash）里 require 调用嵌在一长串条件
        判断表达式里，不在行首，必须用搜索而不是行首匹配才能找到。"""
        code = "const freeModule = mod && mod.require && mod.require('util').types;\n"
        adapter = JsTsAdapter()
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert _kinds_by_target(deps, "util") == ["external"]

    def test_comment_decoy_not_treated_as_dependency(self):
        """回归测试：注释里提到的 import/require 文字不应被误判为真实
        依赖——跟上一轮修复的字符串展示 bug 同一条原则的应用。"""
        code = "// This used to import from '../old-utils' before the refactor\n"
        adapter = JsTsAdapter()
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert deps == []

    def test_dynamic_require_with_variable_is_dynamic(self):
        code = "const dyn = require(moduleNameVar);\n"
        adapter = JsTsAdapter()
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert len(deps) == 1
        assert deps[0].kind == "dynamic"
        assert deps[0].target is None


class TestDependencyExtractionCFamily:
    def test_cpp_local_vs_system_include(self):
        code = '#include "myheader.h"\n#include <stdio.h>\n'
        adapter = CFamilyAdapter(DIALECTS["cpp"])
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert _kinds_by_target(deps, "myheader.h") == ["internal"]
        assert _kinds_by_target(deps, "stdio.h") == ["external"]

    def test_cpp_conditional_include_is_annotated(self):
        """回归测试：真实 Redis 案例（src/ae.c）——多个操作系统互斥的
        #ifdef 分支各自 include 不同实现，这些依赖取决于编译目标，
        不是这个文件的确定依赖，必须标注，不能跟顶层无条件 include
        混在一起平铺展示。"""
        code = (
            '#include "always.h"\n'
            '#ifdef HAVE_EVPORT\n'
            '#include "ae_evport.c"\n'
            '#endif\n'
        )
        adapter = CFamilyAdapter(DIALECTS["cpp"])
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        always_dep = next(d for d in deps if d.target == "always.h")
        conditional_dep = next(d for d in deps if d.target == "ae_evport.c")
        assert "条件编译" not in always_dep.raw_text
        assert "条件编译" in conditional_dep.raw_text

    def test_cpp_include_inside_block_comment_is_not_a_dependency(self):
        """回归测试：块注释里列 0 位置的 `#include` 示例文字不应被误判为
        真实依赖——早期实现直接逐行匹配原始文本，不感知注释边界，导致
        文档注释里举例的 include（如 `/* #include <stdio.h> */`）被当成
        真实的 external 依赖收录。这跟 JS/TS、Ruby、Python 的依赖识别
        复用注释屏蔽是同一套原则，C 系遗漏了，属真实缺陷（构造案例复现
        确认过）。"""
        code = (
            "/*\n"
            " * 示例：下面这行在块注释里，不是真实代码\n"
            "#include <stdio.h>\n"
            " */\n"
            "#include <stdlib.h>\n"
        )
        adapter = CFamilyAdapter(DIALECTS["cpp"])
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert all(d.target != "stdio.h" for d in deps), (
            f"块注释里的 #include 不应被误判为依赖；实际: {[d.target for d in deps]}"
        )
        assert _kinds_by_target(deps, "stdlib.h") == ["external"]

    def test_cpp_ifdef_inside_comment_does_not_corrupt_depth_tracking(self):
        """回归测试：块注释里的 `#ifdef` 不应让条件编译深度计数 +1——
        如果被误计入，会让后续真实的 include 被错误标注为"[条件编译分支内]"
        （因为 ifdef_depth 计数也必须基于屏蔽后的文本，跟 include 判断
        同一条原则）。"""
        code = (
            "/* #ifdef FAKE\n"
            "#include \"fake_branch.h\"\n"
            "#endif */\n"
            "#include \"always_on.h\"\n"
        )
        adapter = CFamilyAdapter(DIALECTS["cpp"])
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert all(d.target != "fake_branch.h" for d in deps), (
            f"块注释里的假 #ifdef 分支不应被误判为依赖；实际: {[d.target for d in deps]}"
        )
        always_dep = next(d for d in deps if d.target == "always_on.h")
        assert "条件编译" not in always_dep.raw_text, (
            f"真实的无条件 include 不应因注释里的假 #ifdef 而被误标条件编译；实际: {always_dep.raw_text!r}"
        )

    def test_java_stdlib_vs_unknown(self):
        code = (
            "import java.util.List;\n"
            "import com.thirdparty.Library;\n"
        )
        adapter = CFamilyAdapter(DIALECTS["java"])
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert _kinds_by_target(deps, "java.util.List") == ["external"]
        assert _kinds_by_target(deps, "com.thirdparty.Library") == ["unknown"]

    def test_cs_using_alias_form_excluded(self):
        """C# 的别名 using（`using X = Y;`）不当作依赖收录——语义上是
        起别名，不是引入新依赖。"""
        code = (
            "using System;\n"
            "using MyAlias = System.Text.StringBuilder;\n"
        )
        adapter = CFamilyAdapter(DIALECTS["c_sharp"])
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert _kinds_by_target(deps, "System") == ["external"]
        assert all(d.target != "MyAlias" for d in deps)


class TestDependencyExtractionRust:
    def test_crate_self_super_are_internal(self):
        code = (
            "use crate::utils::helper;\n"
            "use self::submodule::Thing;\n"
            "use super::parent_thing;\n"
        )
        adapter = RustAdapter()
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert all(d.kind == "internal" for d in deps)

    def test_stdlib_external_and_third_party_unknown(self):
        code = "use std::collections::HashMap;\nuse serde::Serialize;\n"
        adapter = RustAdapter()
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert _kinds_by_target(deps, "std::collections::HashMap") == ["external"]
        assert _kinds_by_target(deps, "serde::Serialize") == ["unknown"]

    def test_mod_declaration_is_internal_file_dependency(self):
        """mod foo; 声明一个对应磁盘上 foo.rs 的子模块文件，这是 Rust
        里唯一真正回答"这个文件依赖哪个别的文件"的语句，必须归 internal，
        跟同名但语义不同的内联 mod foo { ... } 容器定义区分开。"""
        code = "mod utils;\nmod internal_stuff;\n"
        adapter = RustAdapter()
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert len(deps) == 2
        assert all(d.kind == "internal" for d in deps)


class TestDependencyExtractionRuby:
    def test_require_relative_is_internal(self):
        code = "require_relative './helper'\n"
        adapter = RubyAdapter()
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert _kinds_by_target(deps, "./helper") == ["internal"]

    def test_require_stdlib_external_vs_unknown(self):
        code = "require 'json'\nrequire 'my_project_module'\n"
        adapter = RubyAdapter()
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert _kinds_by_target(deps, "json") == ["external"]
        assert _kinds_by_target(deps, "my_project_module") == ["unknown"]

    def test_comment_decoy_not_treated_as_dependency(self):
        code = "# This used to require 'old_lib' before the refactor\n"
        adapter = RubyAdapter()
        deps = adapter.extract_dependencies(code.splitlines(keepends=True))
        assert deps == []


class TestDependencyRenderingIntegration:
    """依赖识别接入渲染层之后的端到端行为：压缩展示格式、
    零符号但有依赖的文件不被跳过、预算截断时不泄漏依赖详情。"""

    def test_deps_line_compact_format(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        (repo / "src").mkdir(parents=True)
        (repo / ".git").mkdir()
        (repo / "src" / "main.py").write_text(
            "import os\n"
            "from . import helper\n"
            "from ..utils import thing\n"
            "\n"
            "def main():\n"
            "    pass\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(repo)
        out_path = repo / "REPOMAP.md"
        rc = rl.main(["-o", str(out_path)])
        assert rc == 0
        content = out_path.read_text(encoding="utf-8")
        assert "<!-- deps: internal=2(., ..utils) external=1 -->" in content

    def test_zero_symbols_but_has_dependencies_not_skipped(self, tmp_path, monkeypatch):
        """回归测试：早期版本"没有符号就整体跳过文件"的判断标准会连带
        丢弃依赖信息——用真实 express 项目的纯 re-export 文件场景复现
        确认过这个问题真实存在。"""
        repo = tmp_path / "repo"
        (repo / "src").mkdir(parents=True)
        (repo / ".git").mkdir()
        (repo / "src" / "reexport_only.py").write_text(
            "from .core import Something\nfrom .utils import helper\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(repo)
        out_path = repo / "REPOMAP.md"
        rc = rl.main(["-o", str(out_path)])
        assert rc == 0
        content = out_path.read_text(encoding="utf-8")
        assert "src/reexport_only.py:" in content
        assert "<!-- deps:" in content

    def test_no_dependencies_no_deps_line(self, tmp_path, monkeypatch):
        """没有任何依赖的文件不应该展示一行空的 deps 注释。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / "plain.py").write_text("def foo():\n    pass\n", encoding="utf-8")
        monkeypatch.chdir(repo)
        out_path = repo / "REPOMAP.md"
        rc = rl.main(["-o", str(out_path)])
        assert rc == 0
        content = out_path.read_text(encoding="utf-8")
        assert "<!-- deps:" not in content

    def test_truncated_file_does_not_leak_dependency_detail(self, tmp_path, monkeypatch):
        """预算截断时，被截断的文件不应该展示依赖详情——跟符号一样，
        应该只留在索引里，不能泄漏出完整的 deps 行。

        注意：预算检查发生在处理每个文件*之前*，累计字节数为0时永远
        不会达到任何正数预算，所以扫描到的第一个文件总会被完整渲染一次
        （这是合理的设计，避免"一个文件都不展示"的极端情况）。要真正
        触发截断，必须让至少两个文件参与扫描，用第一个文件消耗掉预算，
        第二个文件才会被截断——最初写这个测试时只放了一个文件、预算
        设成1字节，结果这个文件依然被完整渲染，测试因此产生了误导性的
        失败（不是功能代码有 bug，是测试构造本身没有真正触发要验证的
        场景），修正后按扫描顺序（sorted，按文件名字典序）放两个文件，
        让第二个文件确实落在预算之外。
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / "a_first.py").write_text(
            "import os\nfrom . import helper\n\ndef foo():\n    pass\n",
            encoding="utf-8",
        )
        (repo / "b_second.py").write_text(
            "import sys\nfrom . import other\n\ndef bar():\n    pass\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(repo)
        out_path = repo / "REPOMAP.md"
        # 预算刚好只够第一个文件的完整 block（含 deps 行），不够两个都展开
        first_size = len(rl.render_filemap(rl.build_filemap(repo / "a_first.py", repo)).encode("utf-8"))
        rc = rl.main(["-o", str(out_path), "--full-detail-budget-bytes", str(first_size)])
        assert rc == 0
        content = out_path.read_text(encoding="utf-8")
        assert "a_first.py:" in content
        assert "b_second.py:" not in content  # 第二个文件被截断，不展开完整 block
        # 索引里仍然能看到 b_second.py 的存在（只是没有 deps 详情）
        assert "b_second.py" in content
        # 关键断言：第二个文件的依赖详情不应该以任何形式泄漏出来——
        # 只应该有一行 deps（属于 a_first.py），不应该有第二行
        assert content.count("<!-- deps:") == 1

    def test_incremental_update_preserves_deps_line(self, tmp_path, monkeypatch):
        """增量更新不应该破坏依赖行的展示，也不应该干扰符号计数解析
        （extract_symbol_count 用固定正则匹配 symbols: 标记，不应受
        新增的 deps: 行影响）。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        f = repo / "main.py"
        f.write_text("import os\n\ndef foo():\n    pass\n", encoding="utf-8")
        monkeypatch.chdir(repo)
        out_path = repo / "REPOMAP.md"
        rc = rl.main(["-o", str(out_path)])
        assert rc == 0

        f.write_text("import os\n\ndef foo():\n    pass\n\ndef bar():\n    pass\n", encoding="utf-8")
        rc = rl.main(["-o", str(out_path), "--update-file", "main.py"])
        assert rc == 0
        content = out_path.read_text(encoding="utf-8")
        assert "<!-- symbols: 2 -->" in content
        assert "<!-- deps: external=1 -->" in content
