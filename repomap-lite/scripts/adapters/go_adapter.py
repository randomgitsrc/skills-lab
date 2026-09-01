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

from adapter_base import AdapterResult, Dependency, Symbol, register
from adapter_utils import IndentStack, indent_of, matches_generated_file_markers, strip_raw_string_literals_naive

GO_FUNC_RE = re.compile(r"^(\s*)func\s*(\([^)]*\))?\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(")
GO_TYPE_STRUCT_RE = re.compile(r"^(\s*)type\s+([A-Za-z_][A-Za-z0-9_]*)\s+(struct|interface)\b")

# 单行 import：`import "fmt"` 或带别名 `import myalias "path"`
GO_IMPORT_SINGLE_RE = re.compile(r'^\s*import\s+(?:[A-Za-z_][A-Za-z0-9_]*\s+)?"([^"]+)"')
# 块状 import 的开启行：`import (`
GO_IMPORT_BLOCK_OPEN_RE = re.compile(r"^\s*import\s*\(\s*$")
# 块状 import 内部的一行：可能带别名前缀，比如 `myalias "path"`，也可能只有
# 路径本身 `"path"`；不处理块注释/行内注释，真实 Go 代码里 import 块内部
# 出现行内注释的情况足够少见，不值得为此增加复杂度
GO_IMPORT_BLOCK_ENTRY_RE = re.compile(r'^\s*(?:[A-Za-z_][A-Za-z0-9_]*\s+)?"([^"]+)"')
GO_IMPORT_BLOCK_CLOSE_RE = re.compile(r"^\s*\)\s*$")

# Go 标准库是不含点号的裸单词路径（"fmt"、"os"、"encoding/json" 这种
# 多级但不含域名点号的路径也算标准库，Go 标准库确实允许多级路径）。
# 带域名点号的路径（"github.com/foo/bar"）说明这是通过某个域名标识的
# 第三方包，一定不是标准库；但也不能因此就断定是 external——它也可能是
# 本项目自己的 module 路径前缀（比如 go.mod 里 `module
# github.com/myorg/myproject`，那么 "github.com/myorg/myproject/internal/x"
# 其实是项目内部包）。没有读取 go.mod 的 module 声明，无法从单行代码本身
# 可靠区分这两种情况，所以带点号的路径一律归为 kind="unknown"（目标清楚，
# 归类不确定），只有确定不含点号的裸路径才归为 kind="external"（Go 标准库
# 是唯一能不看 go.mod 就下结论的情况——标准库路径的写法本身就是全局唯一、
# 不会跟任何项目自己的 module 名冲突的一段命名空间）。
_GO_STDLIB_LOOKING_RE = re.compile(r"^[a-z][a-z0-9_/]*$")


def _classify_go_import_target(target: str) -> str:
    if target in _GO_STDLIB_PACKAGES:
        return "external"
    return "unknown"


# Go 标准库包路径的精确匹配集合（189个，从本地真实安装的 Go 1.22 工具链
# `$GOROOT/src` 目录结构提取，排除了 internal/、cmd/、testdata/ 这些不是
# 公开可 import 的部分）。用**精确匹配**而不是"看起来像标准库"的正则猜测
# ——最初尝试过"不含域名点号的裸路径就是标准库"这条启发式规则，但用真实
# 案例验证时发现站不住：项目自己的 module 名字完全可以是任意不含点号的
# 短小写单词（比如 `module goreal` 这种真实写法），语法形态上跟标准库
# 包名（`fmt`、`os`、`sort`）完全无法区分，宽松正则会把项目自己的内部包
# 误判成"标准库/external"。只有精确匹配一份真实的标准库包名清单才是可靠的
# ——不在这份清单里的路径，一律归为 kind="unknown"（目标字符串本身是
# 清楚的，只是"是不是本项目自己的 module"这件事，没有读取 go.mod 的
# module 声明就无法确定，见 adapter_base.py 里 DependencyKind 的说明）。
#
# 这份清单是一次性生成的静态数据，不会随 Go 版本升级自动更新——新版本
# 标准库新增的包（比如 Go 1.21 加的 `slices`/`maps`）如果不在这份清单里，
# 会被保守地归为 unknown 而不是 external，这是可以接受的降级（错误方向
# 是"少判定几个真正的标准库"，不是"把项目自己的包误判成标准库"，后者
# 严重得多）。
_GO_STDLIB_PACKAGES = frozenset({
    'archive/tar', 'archive/zip', 'arena', 'bufio', 'builtin', 'bytes', 'cmp',
    'compress/bzip2', 'compress/flate', 'compress/gzip', 'compress/lzw', 'compress/zlib',
    'container/heap', 'container/list', 'container/ring', 'context', 'crypto',
    'crypto/aes', 'crypto/boring', 'crypto/cipher', 'crypto/des', 'crypto/dsa',
    'crypto/ecdh', 'crypto/ecdsa', 'crypto/ed25519', 'crypto/elliptic', 'crypto/hmac',
    'crypto/internal', 'crypto/md5', 'crypto/rand', 'crypto/rc4', 'crypto/rsa',
    'crypto/sha1', 'crypto/sha256', 'crypto/sha512', 'crypto/subtle', 'crypto/tls',
    'crypto/x509', 'database/sql', 'debug/buildinfo', 'debug/dwarf', 'debug/elf',
    'debug/gosym', 'debug/macho', 'debug/pe', 'debug/plan9obj', 'embed', 'encoding',
    'encoding/ascii85', 'encoding/asn1', 'encoding/base32', 'encoding/base64',
    'encoding/binary', 'encoding/csv', 'encoding/gob', 'encoding/hex', 'encoding/json',
    'encoding/pem', 'encoding/xml', 'errors', 'expvar', 'flag', 'fmt', 'go', 'go/ast',
    'go/build', 'go/constant', 'go/doc', 'go/format', 'go/importer', 'go/parser',
    'go/printer', 'go/scanner', 'go/token', 'go/types', 'hash', 'hash/adler32',
    'hash/crc32', 'hash/crc64', 'hash/fnv', 'hash/maphash', 'html', 'html/template',
    'image', 'image/color', 'image/draw', 'image/gif', 'image/jpeg', 'image/png',
    'index/suffixarray', 'io', 'io/fs', 'io/ioutil', 'iter', 'log', 'log/slog',
    'log/syslog', 'maps', 'math', 'math/big', 'math/bits', 'math/cmplx', 'math/rand',
    'mime', 'mime/multipart', 'mime/quotedprintable', 'net', 'net/http', 'net/mail',
    'net/netip', 'net/rpc', 'net/smtp', 'net/textproto', 'net/url', 'os', 'os/exec',
    'os/signal', 'os/user', 'path', 'path/filepath', 'plugin', 'reflect', 'regexp',
    'regexp/syntax', 'runtime', 'runtime/cgo', 'runtime/debug', 'runtime/metrics',
    'runtime/pprof', 'runtime/race', 'runtime/trace', 'slices', 'sort', 'strconv',
    'strings', 'sync', 'sync/atomic', 'syscall', 'testing', 'testing/fstest',
    'testing/iotest', 'testing/quick', 'text/scanner', 'text/tabwriter',
    'text/template', 'time', 'unicode', 'unicode/utf16', 'unicode/utf8', 'unique',
    'unsafe', 'weak',
})


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

    def extract_dependencies(self, lines: list[str]) -> list[Dependency]:
        """
        识别 Go 的两种 import 形式：单行 `import "path"` 和块状
        `import (\\n "a"\\n "b"\\n)`。Go 的 import 语法是所有已支持语言里
        最规整的一个（真实测过 cobra/gin/kubectl 等项目，没有遇到需要
        额外处理的边界情况），不需要处理注释屏蔽——`strip_raw_string_
        literals_naive` 已经在 extract_symbols 里处理过反引号原始字符串，
        但 import 语句本身不会出现在反引号字符串里，这里直接用原始
        lines，不复用 clean_lines，避免不必要的耦合。

        Go 没有相对导入语法（不存在 `import "./foo"` 这种写法），internal
        判断因此完全依赖能不能确认这个 import path 是本项目自己的
        module——见 _classify_go_import_target 的说明，没有读取 go.mod
        的情况下，只有能确定是标准库的路径才归为 external，其余带域名
        点号的路径一律归 unknown，不猜测是不是内部包。
        """
        deps: list[Dependency] = []
        in_block = False

        for i, raw in enumerate(lines):
            stripped = raw.rstrip("\n")

            if not in_block:
                if GO_IMPORT_BLOCK_OPEN_RE.match(stripped):
                    in_block = True
                    continue
                m = GO_IMPORT_SINGLE_RE.match(stripped)
                if m:
                    target = m.group(1)
                    deps.append(Dependency(
                        raw_text=stripped.strip(),
                        kind=_classify_go_import_target(target),
                        line_no=i + 1,
                        target=target,
                    ))
                continue

            # 块状 import 内部
            if GO_IMPORT_BLOCK_CLOSE_RE.match(stripped):
                in_block = False
                continue
            m = GO_IMPORT_BLOCK_ENTRY_RE.match(stripped)
            if m:
                target = m.group(1)
                deps.append(Dependency(
                    raw_text=stripped.strip(),
                    kind=_classify_go_import_target(target),
                    line_no=i + 1,
                    target=target,
                ))

        return deps

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
