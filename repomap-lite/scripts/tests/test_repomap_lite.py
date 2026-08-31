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
