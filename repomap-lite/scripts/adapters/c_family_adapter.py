#!/usr/bin/env python3
"""
c_family_adapter.py — C# / Java / C++ / C 适配器（含 Qt 框架的 C++ 代码）。

这四种语言的定义语法高度相似（访问修饰符 + 类型 + 名字 + 花括号），
所以用同一套核心逻辑，通过一个小的"方言配置"（Dialect）区分差异，
而不是给每种语言各写一份几乎重复的代码。这是"适配器内部再做一层参数化"，
不是为每种语言单开一个完全独立的适配器类——降低了四种语言的维护成本，
新增一个花括号语言方言（比如 Kotlin、Swift）大概率也能复用这个适配器，
只需要加一个新的 Dialect 配置，见文件底部的注册代码。

Qt 特殊处理：Qt 的 `Q_OBJECT` / `signals:` / `slots:` / `Q_PROPERTY(...)` 等宏
是 Qt 特有的类声明扩展语法。本适配器把 `signals:`/`public slots:` 这类当作
可见性修饰符处理（不生成独立符号，只是让后续的成员定义不被拦截），
`Q_OBJECT`/`Q_PROPERTY` 宏本身当作噪音跳过（不生成符号，也不报错）。
这是用真实 Qt 项目 cutelyst 验证过的最小可用方案，不追求完整支持 Qt 元对象系统。

已知与真实 tree-sitter 基准的行为差异（如实声明，见 references/known_limitations.md）：
- 只识别方法/字段签名所在的那一行，不追踪多行参数列表的完整内容
- 模板/泛型语法（C++ template<...>, Java/C# 泛型 <T>）只做尽力而为的匹配，
  复杂嵌套泛型（尖括号里再套尖括号+逗号）可能造成误判，未做完整括号平衡解析
- 宏展开（C++ 预处理器宏定义的类/函数）不会被识别，因为本适配器不做预处理
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from adapter_base import AdapterResult, Symbol, register
from adapter_utils import (
    BraceDepthTracker,
    line_is_brace_balanced,
    mask_c_family_comments_and_strings,
    matches_generated_file_markers,
)

# 访问修饰符和其他不影响"这是不是一个定义"的前缀关键字，按方言各自列出
_CS_MODIFIERS = r"(?:public|private|protected|internal|static|abstract|sealed|partial|virtual|override|readonly|async|unsafe|extern|new)"
_JAVA_MODIFIERS = r"(?:public|private|protected|static|abstract|final|synchronized|native|strictfp|default)"
_CPP_MODIFIERS = r"(?:public|private|protected|static|virtual|inline|explicit|friend|constexpr|extern|mutable)"


@dataclass(frozen=True)
class Dialect:
    """一种"花括号语言"的方言配置：这门语言允许哪些修饰符前缀、
    容器关键字有哪些、扩展名是什么。"""

    key: str
    extensions: tuple[str, ...]
    modifiers_pattern: str
    container_keywords: tuple[str, ...]  # class/struct/interface/enum 等
    # Qt 特有的可见性段关键字（仅 C++ 方言需要，其他语言传空元组）
    qt_visibility_sections: tuple[str, ...] = ()
    # 该方言的"自动生成文件"标记正则，每种语言的约定不同，没有就传空元组
    # （比如 C++ 目前没有一个跨编译器统一遵循的生成文件标记约定，传空元组，
    # 表示这个方言暂不支持 is_generated 检测——这是如实反映"目前没有验证过
    # 这个语言的约定"，不是硬凑一个不确定的规则）。
    generated_markers: tuple["re.Pattern", ...] = ()


# Java 的标准生成文件标记：`javax.annotation.processing.Generated` /
# `jakarta.annotation.Generated` 注解（JSR 269 规范定义），常见于 Dagger、
# AutoValue、MapStruct 等会写出中间 .java 源文件的注解处理器。
#
# 如实说明验证程度：这条规则依据的是 JSR 269 规范文档和主流注解处理器的
# 公开文档约定，**不是**像 Go/Python 那两条规则一样用真实工具跑出来的
# 输出直接验证过的（本次没有可用的网络访问权限拉取 Dagger/AutoValue/
# MapStruct 的 jar 包，Lombok 虽然装了但它是编译期字节码改写，不产出
# 中间 .java 源文件，验证不了这条规则）。如果这条规则在真实项目上出现
# 误判或漏判，优先怀疑是这里，而不是默认它跟 Go/Python 那两条一样可靠。
_JAVA_GENERATED_MARKERS = (
    re.compile(r"^\s*@(?:javax\.annotation\.processing\.|jakarta\.annotation\.)?Generated\("),
)


DIALECTS: dict[str, Dialect] = {
    "c_sharp": Dialect(
        key="c_sharp",
        extensions=(".cs",),
        modifiers_pattern=_CS_MODIFIERS,
        container_keywords=("class", "interface", "struct", "enum", "record"),
    ),
    "java": Dialect(
        key="java",
        extensions=(".java",),
        modifiers_pattern=_JAVA_MODIFIERS,
        container_keywords=("class", "interface", "enum", "record"),
        generated_markers=_JAVA_GENERATED_MARKERS,
    ),
    "cpp": Dialect(
        key="cpp",
        extensions=(".c", ".cpp", ".cc", ".cxx", ".hpp", ".hxx", ".hh", ".h"),
        modifiers_pattern=_CPP_MODIFIERS,
        container_keywords=("class", "struct", "enum", "namespace", "union"),
        qt_visibility_sections=("signals", "public slots", "private slots", "protected slots", "slots"),
        # C++ 没有跨编译器/跨工具链统一的生成文件标记约定（不同代码生成器
        # 各自为政，没有类似 Go 官方规范那样的共识），暂不提供
        # generated_markers，也就是这个方言的 is_generated 恒为 False。
    ),
}


def _build_container_re(dialect: Dialect) -> re.Pattern:
    kw = "|".join(dialect.container_keywords)
    # 允许模板前缀 `template<...>` 出现在同一行或被忽略（简化：只匹配容器关键字本身）
    return re.compile(
        rf"^(\s*)(?:{dialect.modifiers_pattern}\s+)*"
        rf"(?:{kw})\s+([A-Za-z_][A-Za-z0-9_]*)"
    )


# C 语言的 typedef 声明，仅 cpp 方言使用（C#/Java 没有这个关键字）。
# 覆盖三种形式：
#   1. 简单别名：`typedef <类型> <名字>;`（可选 struct/enum/union 前缀），
#      例如 `typedef long long mstime_t;`、`typedef struct redisObject robj;`
#   2. 函数指针别名：`typedef <返回类型> (*<名字>)(<参数...>);`，
#      例如 `typedef void (*moduleTypeFreeFunc)(void *value);`——名字被包在
#      `(*...)` 里，不是一个直接跟在类型后面的裸标识符，需要单独一条规则。
#   3. 匿名结构体/枚举 typedef（跨行）：
#        typedef struct {
#            ...成员...
#        } Name;
#      名字出现在闭合花括号之后的独立一行（`} Name;`），需要跨行状态才能
#      正确关联回开启这个块的 `typedef struct {`/`typedef enum {` 那一行。
# 用真实项目 Redis 的头文件实测确认：这三种形式全部都有实际意义上不小的
# 出现频率——单个 src/server.h 就有111处 typedef，其中20处是函数指针形式；
# deps/xxhash/xxhash.h 里绝大多数 typedef 是匿名结构体/枚举跨行形式
# （早期版本明确把这种形式列为"已知局限，不修复"，但实测发现它是所有
# typedef 形式里出现频率最高、影响面最大的一种，值得投入实现，而不是
# 简单归为"复杂度不成比例"就跳过）。
TYPEDEF_RE = re.compile(
    r"^(\s*)typedef\s+.*?\b([A-Za-z_][A-Za-z0-9_]*)\s*;\s*$"
)
TYPEDEF_FUNC_PTR_RE = re.compile(
    r"^(\s*)typedef\s+.*?\(\s*\*\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*\([^;]*\)\s*;\s*$"
)
# 匹配"开启一个 struct/enum/union typedef 块"的那一行。分两种情况：
#   - 有 tag 名：`typedef struct redisDb {` —— tag 名本身就足够展示，
#     直接在这一行生成符号，不需要等收尾（跟真实 tree-sitter 基准的行为
#     一致：用真实项目 Redis 验证过，基准直接展示 `typedef struct redisDb {`
#     这一行本身，不会额外去找后面的 `} Alias;`）。
#   - 无 tag 名（纯匿名）：`typedef struct {` / `typedef enum {` —— 这时
#     开启行本身没有任何名字可展示，必须等到收尾的 `} Name;` 才知道
#     这个类型的名字是什么，需要跨行状态记录"正在等待收尾"
#     （见 extract_symbols 里的 pending_anonymous_typedef 状态）。
TYPEDEF_BLOCK_OPEN_WITH_TAG_RE = re.compile(
    r"^(\s*)typedef\s+(?:struct|enum|union)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{\s*$"
)
TYPEDEF_BLOCK_OPEN_ANONYMOUS_RE = re.compile(
    r"^(\s*)typedef\s+(?:struct|enum|union)\s*\{\s*$"
)
# 匹配 typedef 块的收尾行：`} Name;`（closing brace 后面紧跟别名标识符和分号），
# 只用于匿名开启的情况（有 tag 的情况已经在开启行生成符号，收尾行不再重复）。
TYPEDEF_BLOCK_CLOSE_RE = re.compile(
    r"^(\s*)\}\s*([A-Za-z_][A-Za-z0-9_]*)\s*;\s*$"
)


def _build_method_re(dialect: Dialect) -> re.Pattern:
    # 形如: [修饰符]* 返回类型(可含泛型/指针/引用/命名空间::) 方法名 ( 参数... )
    #       [const] [throws 异常类型列表(仅Java)] [收尾]
    # "收尾"部分要覆盖三种情况：
    #   - 纯声明，以 `;` 结束（头文件里常见：`void Foo();`）
    #   - 多行方法体，本行只到左花括号 `{` 为止（花括号独占下一行或本行结尾）
    #   - 单行方法体，花括号在本行内就配平，例如 `void Foo() { }` 或 `int Bar() { return 1; }`
    # 早期版本只处理了前两种，第三种（单行方法体）完全匹配不上——用真实的
    # C# 嵌套类测试案例复现过这个 bug：`public void OuterMethod() { }` 因为
    # 末尾多出的 `}` 破坏了 `$` 锚点而完全无法匹配，导致该方法被整个漏掉。
    #
    # `throws` 子句是用真实项目 gson 测出的另一个真实缺口：Java 方法签名里
    # `throws IOException {` 这种写法（受检异常声明），因为参数列表和左花括号
    # 之间多了一段 `throws ...` 内容，早期正则完全无法匹配，在测试代码里
    # （频繁标注 `throws IOException`）造成大量漏报（单个文件150处）。
    # 虽然只有 Java 用得到这个子句，但让 C#/C++ 方言也接受这个可选片段
    # 是无害的（它们的代码里不会出现 `throws` 关键字，不会误伤其他匹配）。
    #
    # 这是本适配器里最容易误报/漏报的一条规则，已知局限里明确写了这一点。
    return re.compile(
        rf"^(\s*)(?:{dialect.modifiers_pattern}\s+)*"
        r"(?:[A-Za-z_][A-Za-z0-9_:<>,\*&\s]*[\s\*&])"
        r"([A-Za-z_~][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*(?:const\s*)?"
        r"(?:throws\s+[A-Za-z_][A-Za-z0-9_.<>,\s]*)?"
        r"(?:;|\{\s*[^{}]*\s*\}\s*;?|\{)?\s*$"
    )


def _build_out_of_class_method_re() -> re.Pattern:
    # C++ 特有写法：方法在 .cpp 里以 `ClassName::MethodName(...)` 形式实现，
    # 声明和定义分离，方法名前面紧跟 `::` 而不是空格，前面这条通用规则匹配不上，
    # 需要单独一条规则。构造/析构函数（ClassName::ClassName / ClassName::~ClassName）
    # 也走这条规则。收尾部分同样要覆盖单行方法体的情况（见 _build_method_re 的注释）。
    return re.compile(
        r"^(\s*)(?:[A-Za-z_][A-Za-z0-9_<>,\*&\s]*[\s\*&])?"
        r"([A-Za-z_][A-Za-z0-9_]*)::(~?[A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*(?:const\s*)?"
        r"(?:\{\s*[^{}]*\s*\}\s*;?|\{)?\s*$"
    )


def _build_in_class_ctor_dtor_re() -> re.Pattern:
    """
    类/结构体内部的构造函数/析构函数，没有返回类型前缀（这是它们与普通方法
    在语法上的关键区别，例如 `Container() : size_(0) {}` 或 `~Container() {}`，
    可能带初始化列表 `: member(value), ...`）。因为没有返回类型可以匹配，
    _build_method_re() 那条通用规则天生覆盖不到这种写法，需要单独一条规则，
    并且调用方必须额外确认"括号前的名字等于当前所在类的名字"才能使用这条规则，
    否则任何裸函数调用语句 `Foo(x);` 都会被误判为构造函数（详见调用点）。
    """
    return re.compile(
        r"^(\s*)(explicit\s+)?(~?)([A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*"
        r"(?::[^{;]+)?"  # 可选的成员初始化列表 `: a(1), b(2)`
        r"(?:\{\s*[^{}]*\s*\}\s*;?|\{|;)?\s*$"
    )


_QT_MACRO_RE = re.compile(r"^\s*Q_(OBJECT|PROPERTY|INVOKABLE|SIGNAL|SLOT|ENUM|FLAG|DISABLE_COPY)\b")


# 匹配"只有一个类型名，没有括号、没有分号、没有花括号"的独立一行，
# 例如 `static void`、`int`、`static inline uint64_t`，以及项目自定义的
# 宏前缀（如 jemalloc 的 `JEMALLOC_ALWAYS_INLINE void`）。不再局限于固定的
# 修饰符关键字列表——真实项目里这类宏前缀的取名千差万别（EXPORT/API/
# WINAPI/JEMALLOC_ALWAYS_INLINE/XXH_PUBLIC_API等），穷举关键字列表天然
# 覆盖不全。改成允许任意数量的"标识符token序列"，只要求最后以一个类型名
# （可带指针星号）结尾——用真实项目 Redis 内嵌的 jemalloc 依赖复现确认：
# `JEMALLOC_ALWAYS_INLINE void` 这一行因为固定关键字列表不认识
# `JEMALLOC_ALWAYS_INLINE` 这个自定义宏，导致后续 `ph_new(ph_t *ph) {`
# 这种分行写法的函数完全无法被识别为"返回类型独占一行"的模式。
_TYPE_ONLY_LINE_RE = re.compile(
    r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*\s+)*"
    r"[A-Za-z_][A-Za-z0-9_]*\s*\**\s*$"
)
# 下一行必须是"标识符 + 左括号"开头，且不能是控制流关键字
# （if/for/while/switch 后面也会跟括号，但那些不是函数定义）
_IDENTIFIER_PAREN_START_RE = re.compile(r"^\s*[A-Za-z_~][A-Za-z0-9_]*\s*\(")
_CONTROL_FLOW_KEYWORDS = {"if", "for", "while", "switch", "return", "sizeof"}


# 匹配"这一行开启了一段函数签名，但参数列表在本行内没有配平"的情况：
# 形如 "[修饰符]* 返回类型 函数名(部分参数..."，本行内 `(` 数量比 `)` 多，
# 说明参数列表还要继续到下一行。用真实项目 Redis 复现确认：
#   static redisContext *getRedisContext(const char *ip, int port,
#                                        const char *hostsocket)
#   {
# 第一行本身"返回类型+函数名"都在同一行（不是"返回类型独占一行"的
# split-style 写法），但参数列表跨行，_method_re 要求 `(...)` 在同一行内
# 闭合，天然匹配不上。这条规则专门补这个缺口：只要求"类型样式前缀 +
# 标识符 + 左括号"出现在行首，不要求本行内配平，交由
# _find_statement_terminator 做跨行查找来确认真正的收尾字符。
_UNCLOSED_PAREN_SIGNATURE_RE = re.compile(
    r"^(\s*)(?:[A-Za-z_][A-Za-z0-9_:<>,\*&\s]*[\s\*&])"
    r"([A-Za-z_~][A-Za-z0-9_]*)\s*\("
)


def _paren_balance(line: str) -> int:
    """统计一行内 `(` 比 `)` 多多少个（可以为负）。用于判断一行是否
    在参数列表中间结束（正数=还有未闭合的左括号，说明参数列表跨行）。"""
    return line.count("(") - line.count(")")


def _find_statement_terminator(clean_lines: list[str], start_idx: int) -> str | None:
    """
    从 start_idx 这一行开始，向后扫描直到找到这个函数签名/声明的真正收尾
    字符——要么是配对完整后紧跟的 `;`（纯声明），要么是配对完整后紧跟的
    `{`（有函数体）。用于正确判断"多行参数列表"这种情况：仅看当前行是否
    以 `;` 结尾是不够的，参数列表本身可能还要再跨多行才结束。

    用真实项目 Redis 内嵌的 jemalloc 依赖复现确认过这个 bug：
    `static void\\narena_maybe_do_deferred_work(tsdn_t *tsdn, arena_t *arena,
    decay_t *decay,\\n    size_t npages_new);` 是一个 3 行的纯声明
    （返回类型独占第1行，参数列表横跨第2、3行），早期版本只检查"当前行
    （第2行）是否以 `;` 结尾"来决定要不要压入一个等待函数体的帧——第2行
    显然不以 `;` 结尾（它在参数列表中间），于是被错误地当作"即将有函数体"
    处理，压入一个永远等不到花括号的帧，导致该帧永久卡在栈顶，把文件从
    这里往后的全部内容都误判为"仍在某个函数体内部"。

    实现：从 start_idx 行的第一个 `(` 开始做括号计数，直到找到与之配对的
    `)`；再跳过 `)` 之后的空白，看紧跟的第一个非空白字符是 `;` 还是 `{`。
    这个扫描不需要跨越太多行（函数签名的参数列表很少超过几十行），设置一个
    合理的行数上限（20行）防止极端情况下的性能问题或误判导致的无限扫描。
    """
    MAX_LOOKAHEAD_LINES = 20
    paren_depth = 0
    found_open_paren = False

    for line_offset in range(min(MAX_LOOKAHEAD_LINES, len(clean_lines) - start_idx)):
        line = clean_lines[start_idx + line_offset]
        j = 0
        n = len(line)
        while j < n:
            ch = line[j]
            if ch == "(":
                paren_depth += 1
                found_open_paren = True
            elif ch == ")":
                paren_depth -= 1
                if found_open_paren and paren_depth == 0:
                    # 找到配对的收尾括号，继续在同一行剩余部分或后续行里
                    # 找第一个非空白字符
                    k = j + 1
                    while True:
                        while k < len(line):
                            if not line[k].isspace():
                                return line[k]
                            k += 1
                        # 本行剩余部分全是空白，换到下一行继续找
                        next_offset = line_offset + 1
                        if next_offset >= min(MAX_LOOKAHEAD_LINES, len(clean_lines) - start_idx):
                            return None
                        line = clean_lines[start_idx + next_offset]
                        k = 0
                        line_offset = next_offset
                        n = len(line)
            j += 1

    return None


def _is_split_style_function_start(prev_line: str, cur_line: str) -> bool:
    """
    判断 prev_line + cur_line 这两行合在一起是否构成"返回类型独占一行，
    函数名和参数列表在下一行"这种 C 语言常见写法（例如 Linux kernel、
    jemalloc 等大量真实代码库的主流风格）：

        static void
        pthread_create_wrapper_init(void) {

    用真实项目 Redis（内嵌的 jemalloc 依赖）实测确认：这种写法在单个文件里
    出现上千次，早期版本因为要求返回类型和函数名在同一行，完全无法识别这类
    函数，是一个真实且高优先级的覆盖缺口，不是边界情况。

    这是一个真正的"跨行判断"，调用方需要同时看当前行和上一行；不通过合并
    文本喂给现有的单行正则（那样会打乱行号语义），而是单独判断+复用现有的
    函数名提取逻辑，只在确认是这个模式时才生效，避免误伤其他"独立一行的
    标识符"场景（比如宏定义、标签等）。

    _TYPE_ONLY_LINE_RE 为了兼容项目自定义宏前缀（如 jemalloc 的
    `JEMALLOC_ALWAYS_INLINE void`）放宽成了"任意标识符token序列"，这意味着
    像孤立的 `return`（后面紧跟换行，参数换行书写这种罕见但合法的写法）
    也会匹配上"类型独占一行"的形状。必须额外排除上一行本身就是控制流/
    语句关键字的情况，否则 `return\\n    some_call(x);` 这种写法会被
    误判为"返回类型独占一行的函数定义"。
    """
    prev_first_word_match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", prev_line.strip())
    if prev_first_word_match and prev_first_word_match.group(0) in _CONTROL_FLOW_KEYWORDS:
        return False
    if not _TYPE_ONLY_LINE_RE.match(prev_line):
        return False
    cur_stripped = cur_line.strip()
    m = _IDENTIFIER_PAREN_START_RE.match(cur_stripped)
    if not m:
        return False
    first_word = re.match(r"[A-Za-z_~][A-Za-z0-9_]*", cur_stripped)
    if first_word and first_word.group(0) in _CONTROL_FLOW_KEYWORDS:
        return False
    return True


class CFamilyAdapter:
    """
    构造时传入一个 Dialect，得到一个针对该方言的适配器实例。
    三个方言（c_sharp / java / cpp）分别在文件底部实例化并注册。
    """

    def __init__(self, dialect: Dialect):
        self.dialect = dialect
        self.name = dialect.key
        self._container_re = _build_container_re(dialect)
        self._method_re = _build_method_re(dialect)
        self._out_of_class_method_re = _build_out_of_class_method_re()
        self._ctor_dtor_re = _build_in_class_ctor_dtor_re()
        self._qt_visibility_re = None
        if dialect.qt_visibility_sections:
            sections = "|".join(re.escape(s) for s in dialect.qt_visibility_sections)
            self._qt_visibility_re = re.compile(rf"^\s*(?:{sections})\s*:\s*$")

    def match(self, filepath) -> bool:
        return Path(filepath).suffix in self.dialect.extensions

    def is_generated(self, lines: list[str]) -> bool:
        if not self.dialect.generated_markers:
            return False
        # Java 的 @Generated 注解出现在类声明正上方，前面通常还有 package
        # 声明和一段 import 列表，不像 Go/Python 的标记那样保证在文件最
        # 开头几行——用更宽的扫描窗口（50行）覆盖这种情况，代价是全文
        # 扫描风险区间变大，但 @Generated 本身是一个足够具体的注解语法
        # （不是"generated"这种可能出现在普通注释里的自然语言词），
        # 出现在扫描窗口内基本就能确认这是生成文件，误判空间不大。
        return matches_generated_file_markers(
            lines, self.dialect.generated_markers, check_lines=50
        )

    def extract_symbols(self, filepath, lines: list[str]) -> AdapterResult:
        # 用单次线性扫描同时处理注释（//、/* */）和字符串/字符字面量
        # （"..."、C#的@"..."、'...'），而不是分两个独立阶段各自处理。
        # 分阶段处理天然有相互误伤的问题：先屏蔽字符串会让英文注释里的
        # 撇号（如 "the caller's buffer"）被误判为字符字面量开始，
        # 吞掉后续内容直到下一个引号——如果中间恰好越过了该注释自己的
        # 结束符 `*/`，会导致文件从这里往后全部被误判为仍在注释里，
        # 全部清空；反过来先剥注释也有对称的失败模式。已用真实项目
        # Redis 的 src/cluster.c 复现确认过这个连锁失败的具体路径
        # （见 adapter_utils.mask_c_family_comments_and_strings 的文档字符串）。
        clean_lines = mask_c_family_comments_and_strings(lines)
        symbols: list[Symbol] = []
        notes: list[str] = []

        # 统一的帧栈：每一帧是 (base_depth, kind, container_depth, entered)。
        #   base_depth   — 这一帧对应的定义行被处理时的花括号深度（tracker.depth_before_line()）
        #   kind         — "container"（class/struct/namespace等）或 "body"（函数/方法体）
        #   container_depth — 仅 kind=="container" 时有意义：这个容器自身的展示深度(depth)，
        #                      用于计算它内部直接成员的 depth = container_depth + 1
        #   entered      — 是否已经真正看到花括号深度超过 base_depth（即已经进入这一帧内部）。
        #                  在 entered 变为 True 之前，不能因为深度回落到 base_depth 就弹出——
        #                  因为定义行本身和它后面独占一行的 `{` 具有相同的 depth_before
        #                  （左花括号要等 tracker.update 处理完才会计入深度），
        #                  如果不做这个区分，帧会在刚入栈的下一行就被误判为"已经离开"而弹出。
        # 这一个统一的栈替代了早期版本里 container_stack 和 body_stack 两个平行的栈，
        # 避免同一个"入栈行与其左花括号共享 depth_before"的问题在两处分别修一次、
        # 修出两套不完全一致的逻辑。
        frames: list[dict] = []
        tracker = BraceDepthTracker()
        qt_macro_seen = False
        prev_stripped_line = ""  # 用于跨行判断"返回类型独占一行"的写法
        # 记录当前是否正在等待一个匿名 typedef struct/enum 块的收尾行
        # （`typedef struct {` 这种没有 tag 名的情况，真正的名字要等
        # `} Name;` 才知道，见 TYPEDEF_BLOCK_OPEN_ANONYMOUS_RE 的说明）。
        # 值为 None 表示当前不在等待；否则是该 typedef 块开启时所在的
        # 花括号深度（base_depth），用于配合帧栈的深度匹配确认"这个收尾行
        # 确实是在关闭我们记录的这个匿名块，不是关闭了中途新开启的其他容器"。
        pending_anonymous_typedef_base_depth: int | None = None
        pending_anonymous_typedef_line_no: int = 0

        def pop_exited_frames(depth_before: int) -> None:
            while frames and frames[-1]["entered"] and depth_before <= frames[-1]["base_depth"]:
                frames.pop()
            if frames and not frames[-1]["entered"] and depth_before > frames[-1]["base_depth"]:
                frames[-1]["entered"] = True

        def innermost_container_depth() -> int:
            """从栈顶往下找最近的 container 帧的 container_depth；没有则说明在顶层，返回 -1
            （调用方会用 -1+1=0 作为顶层符号的 depth）。跳过中间的 body 帧
            （理论上 body 帧内部不应该再看到符号，因为函数体内部整体被跳过，
            这个函数只在"确认不在任何 body 内"的前提下才会被调用到）。
            """
            for f in reversed(frames):
                if f["kind"] == "container":
                    return f["container_depth"]
            return -1

        for i, raw in enumerate(clean_lines):
            # clean_lines 已经由 mask_c_family_comments_and_strings 处理过，
            # // 行注释和字符串内容都已经屏蔽完毕，这里不需要再调用
            # strip_line_comment_naive 一次（调用了也是无害的空操作，
            # 但保留变量名 no_line_comment/stripped 不变，减少下面大段
            # 逻辑里到处改名字的风险）。
            no_line_comment = raw.rstrip("\n")
            stripped = no_line_comment

            if not stripped.strip():
                tracker.update(no_line_comment)
                continue

            if _QT_MACRO_RE.match(stripped):
                qt_macro_seen = True
                tracker.update(no_line_comment)
                prev_stripped_line = stripped
                continue

            if self._qt_visibility_re and self._qt_visibility_re.match(stripped):
                tracker.update(no_line_comment)
                prev_stripped_line = stripped
                continue

            depth_before = tracker.depth_before_line()

            # 在弹出任何帧之前，先检查这一行是不是我们正在等待的匿名 typedef
            # 块的收尾行。必须在 pop_exited_frames 之前检查——那个函数会根据
            # 深度回落弹出帧，如果先弹出再检查，用来核对"确实是同一个块"的
            # base_depth 信息就没了。
            #
            # 深度判断用 `pending + 1` 而不是 `pending` 本身：收尾行自己的
            # `}` 还没被 tracker 计入，所以此刻的 depth_before 仍然反映
            # "身处块内部"的深度（即 base_depth + 1），要等这一行处理完、
            # tracker.update() 跑过之后，深度才会真正回落到 base_depth。
            # 这是本文件里第三次踩到同一类"深度要等这一行处理完才生效"的
            # 计时问题（此前在 body 帧的配平检测、container 帧的前向声明
            # 判断里也出现过），已用真实的 xxhash.h 结构复现确认过。
            if (
                pending_anonymous_typedef_base_depth is not None
                and depth_before == pending_anonymous_typedef_base_depth + 1
            ):
                close_match = TYPEDEF_BLOCK_CLOSE_RE.match(stripped)
                if close_match:
                    alias_name = close_match.group(2)
                    symbols.append(Symbol(
                        name=f"typedef ... {{ ... }} {alias_name};",
                        depth=0,
                        line_no=pending_anonymous_typedef_line_no,
                    ))
                    pending_anonymous_typedef_base_depth = None

            pop_exited_frames(depth_before)

            in_function_body = bool(frames) and frames[-1]["kind"] == "body" and frames[-1]["entered"]
            if in_function_body:
                tracker.update(no_line_comment)
                prev_stripped_line = stripped
                continue

            # 只在栈顶是（或不存在）container 帧的情况下才谈得上"顶层"或"容器直接子层"，
            # 如果栈顶是一个尚未 entered 的 body 帧（刚匹配到方法定义、还没看到左花括号），
            # 后续这一行仍然被视为该方法签名的延续，不应被当作新的容器/方法起点。
            top_is_pending_body = bool(frames) and frames[-1]["kind"] == "body" and not frames[-1]["entered"]
            if top_is_pending_body:
                tracker.update(no_line_comment)
                prev_stripped_line = stripped
                continue

            container_frames = [f for f in frames if f["kind"] == "container"]
            is_top_level = len(container_frames) == 0
            nearest_container_depth = innermost_container_depth()
            # "容器直接子层"指当前深度恰好比最近的 container 帧深一级，
            # 且从该 container 帧到栈顶之间没有其他 container 帧插入
            # （即最近的一帧本身就是 container，而不是隔着别的容器）。
            in_container_direct_child = (
                frames
                and frames[-1]["kind"] == "container"
                and depth_before == frames[-1]["base_depth"] + 1
            )

            m = self._container_re.match(stripped)
            if m and (is_top_level or in_container_direct_child):
                cur_depth = nearest_container_depth + 1
                container_name = m.groups()[-1]
                symbols.append(Symbol(name=stripped.strip(), depth=cur_depth, line_no=i + 1))
                # 只有当这一行既不是"当场配平"（同行内 { } 都出现），也不是
                # 纯前向声明（以 `;` 结尾、完全没有花括号，例如 C 头文件里
                # 极常见的 `struct redisObject;` 前向声明）时，才需要压入一个
                # 容器帧继续等待后续的花括号体。前向声明没有任何花括号体，
                # 如果误压入帧，这个帧的 entered 永远不会变成 True（因为深度
                # 永远不会超过压入时的基线），会永久卡在栈顶，把文件从这里
                # 往后的所有内容都误判为"仍在某个容器内部"，导致后续本该是
                # 顶层的声明（包括这里最初想要修复的函数指针 typedef）全部
                # 被跳过。这是用真实项目 Redis 的 src/server.h 复现确认过的
                # bug：该文件开头有多个 `struct XXX;` 前向声明，全部误触发压帧，
                # 导致文件后半部分的 typedef/struct 全部无法被正确识别为顶层。
                ends_as_forward_decl = stripped.rstrip().endswith(";") and "{" not in stripped
                if not line_is_brace_balanced(stripped) and not ends_as_forward_decl:
                    frames.append({
                        "base_depth": depth_before,
                        "kind": "container",
                        "container_depth": cur_depth,
                        "container_name": container_name,
                        "entered": False,
                    })
                tracker.update(no_line_comment)
                prev_stripped_line = stripped
                continue

            if self.dialect.key == "cpp" and is_top_level:
                tagged_open = TYPEDEF_BLOCK_OPEN_WITH_TAG_RE.match(stripped)
                if tagged_open:
                    # 有 tag 名的 typedef struct/enum/union 块：跟真实基准行为一致，
                    # 直接在开启行生成符号（tag 名本身就是有意义的信息），
                    # 同时把这个块当作普通容器压帧，允许内部成员被正确嵌套展示。
                    cur_depth = nearest_container_depth + 1
                    container_name = tagged_open.group(2)
                    symbols.append(Symbol(name=stripped.strip(), depth=cur_depth, line_no=i + 1))
                    frames.append({
                        "base_depth": depth_before,
                        "kind": "container",
                        "container_depth": cur_depth,
                        "container_name": container_name,
                        "entered": False,
                    })
                    tracker.update(no_line_comment)
                    prev_stripped_line = stripped
                    continue

                anon_open = TYPEDEF_BLOCK_OPEN_ANONYMOUS_RE.match(stripped)
                if anon_open:
                    # 无 tag 名：这一行本身不生成符号，记录"正在等待收尾"状态，
                    # 同时仍然要压入一个容器帧（哪怕暂时没有名字），保证深度
                    # 追踪和"是否在容器内部一层"的判断对块内成员依然正确——
                    # 等真正看到 `} Name;` 时再用 pending 状态回填符号。
                    pending_anonymous_typedef_base_depth = depth_before
                    pending_anonymous_typedef_line_no = i + 1
                    cur_depth = nearest_container_depth + 1
                    frames.append({
                        "base_depth": depth_before,
                        "kind": "container",
                        "container_depth": cur_depth,
                        "container_name": None,
                        "entered": False,
                    })
                    tracker.update(no_line_comment)
                    prev_stripped_line = stripped
                    continue

                m = TYPEDEF_FUNC_PTR_RE.match(stripped) or TYPEDEF_RE.match(stripped)
                if m:
                    symbols.append(Symbol(name=stripped.strip(), depth=0, line_no=i + 1))
                    tracker.update(no_line_comment)
                    prev_stripped_line = stripped
                    continue

            if in_container_direct_child or is_top_level:
                # 先尝试构造/析构函数专属规则，但只在"当前確实处于某个类的直接子层"
                # 且括号前的名字跟这个类同名（或前面带 `~`）时才采信，否则任何裸函数
                # 调用语句 `Foo(x);` 都会被误判为构造函数 —— 这条规则天生比通用方法
                # 规则更容易误报，必须用类名核对收紧。
                enclosing_class_name = (
                    frames[-1].get("container_name") if in_container_direct_child else None
                )
                m = None
                if enclosing_class_name:
                    ctor_match = self._ctor_dtor_re.match(stripped)
                    if ctor_match and ctor_match.group(4) == enclosing_class_name:
                        m = ctor_match
                if m is None:
                    m = self._method_re.match(stripped) or self._out_of_class_method_re.match(stripped)

                # 通用规则都没匹配上时，检查是不是"返回类型独占上一行"这种写法
                # （见 _is_split_style_function_start 的详细说明；用真实项目 Redis
                # 内嵌的 jemalloc 依赖实测确认，单个文件里这种写法出现上千次，
                # 是比 Class::method 更常见的一种真实缺口，仅在 C++ 方言下生效，
                # 因为这是 C 代码的传统风格，C#/Java 代码几乎不会这样写）。
                display_name = stripped.strip()
                if m is None and self.dialect.key == "cpp" and _is_split_style_function_start(
                    prev_stripped_line, stripped
                ):
                    # 用一个宽松的"标识符+括号"匹配确认这确实是函数定义而不是
                    # if/for/while 等控制流语句（已在 _is_split_style_function_start
                    # 里排除了常见控制流关键字，这里只需要拿到匹配对象本身）。
                    fallback_match = _IDENTIFIER_PAREN_START_RE.match(stripped.strip())
                    if fallback_match:
                        m = fallback_match
                        # 展示时把上一行的返回类型也带上，跟真实 tree-sitter 基准的
                        # 多行签名展示方式一致，避免只显示函数名丢失类型信息。
                        display_name = f"{prev_stripped_line.strip()}\n{stripped.strip()}"

                # 通用规则和"返回类型独占一行"都没匹配上时，检查是不是
                # "返回类型和函数名同一行，但参数列表跨行"这种写法（见
                # _UNCLOSED_PAREN_SIGNATURE_RE 的说明）。要求本行确实有
                # 未闭合的左括号（_paren_balance > 0），否则任何普通语句
                # 只要形状凑巧像"类型 标识符("就会被误判——真正的未闭合
                # 参数列表是这条规则唯一应该生效的场景。
                if m is None and self.dialect.key == "cpp" and _paren_balance(stripped) > 0:
                    unclosed_match = _UNCLOSED_PAREN_SIGNATURE_RE.match(stripped)
                    if unclosed_match:
                        name_candidate = unclosed_match.group(2)
                        if name_candidate not in _CONTROL_FLOW_KEYWORDS:
                            terminator = _find_statement_terminator(clean_lines, i)
                            if terminator in ("{", ";"):
                                m = unclosed_match
                                display_name = stripped.strip()

                if m:
                    cur_depth = nearest_container_depth + 1
                    symbols.append(Symbol(name=display_name, depth=cur_depth, line_no=i + 1))
                    # 判断要不要压入一个等待函数体的帧：不能只看"这一行是否
                    # 以 `;` 结尾"，因为参数列表本身可能还要跨多行才结束
                    # （见 _find_statement_terminator 的详细说明）。正确做法
                    # 是找到跟本行第一个 `(` 配对的 `)`，再看紧跟其后的
                    # 第一个非空白字符是 `;`（纯声明，不压帧）还是 `{`
                    # （有函数体，需要压帧）还是其他/找不到（保守起见按
                    # "同一行内已配平"的旧逻辑退化处理，不确定时不压帧，
                    # 因为压错帧的后果远比不压帧更严重——不压帧最多漏掉
                    # 那一个符号内部的嵌套识别，压错帧会让整个文件后续内容
                    # 全部错位）。
                    terminator = _find_statement_terminator(clean_lines, i)
                    should_push_body = (
                        terminator == "{"
                        if terminator is not None
                        else (not stripped.rstrip().endswith(";") and not line_is_brace_balanced(stripped))
                    )
                    if should_push_body:
                        frames.append({
                            "base_depth": depth_before,
                            "kind": "body",
                            "container_depth": -1,
                            "container_name": None,
                            "entered": False,
                        })

            tracker.update(no_line_comment)
            prev_stripped_line = stripped

        if qt_macro_seen:
            notes.append("检测到 Qt 宏 (Q_OBJECT/Q_PROPERTY等)，已跳过宏本身，未展开元对象系统")

        return AdapterResult(symbols=symbols, notes=notes)


register(CFamilyAdapter(DIALECTS["c_sharp"]))
register(CFamilyAdapter(DIALECTS["java"]))
register(CFamilyAdapter(DIALECTS["cpp"]))
