# Style Rules — Detailed Reference

Detailed rules with do/don't examples. Maps each rule to linter equivalents.

## 1. Heading Rules

### 1.1 ATX Style Only

    ✅ Do:
    # Document Title
    ## Section 1
    ### Subsection 1.1

    ❌ Don't:
    Document Title
    =============
    Section 1
    ---------

**Linter:** markdownlint MD003 (heading-style: atx)

### 1.2 Numbered Headings

Use Arabic numerals. The number is part of the heading text, not the `#` prefix.

    ✅ Do:
    # 1. Overview
    ## 1.1 Background
    ## 1.2 Goals
    # 2. Design
    ## 2.1 Architecture

    ❌ Don't:
    # 一、概述
    # 二、设计
    # Overview (no number)

**Linter:** No built-in rule. Enforced by convention.

### 1.3 No Skipped Levels

    ✅ Do:
    # Title
    ## Section
    ### Subsection

    ❌ Don't:
    # Title
    #### Subsection  (skipped ## and ###)

**Linter:** markdownlint MD001 (heading-increment)

### 1.4 One H1 Per Document

    ✅ Do:
    # Document Title
    (content with ## and ### only)

    ❌ Don't:
    # Chapter 1
    # Chapter 2

**Linter:** markdownlint MD025 (single-h1)

## 2. List Rules

### 2.1 Unordered: Hyphen Only

    ✅ Do:
    - Item A
    - Item B
    - Item C

    ❌ Don't:
    * Item A
    + Item B
    - Item C

**Linter:** markdownlint MD004 (ul-style: dash)

### 2.2 Ordered: All `1.`

    ✅ Do:
    1. First
    1. Second
    1. Third

    ❌ Don't:
    1. First
    2. Second
    3. Third

Rationale: Markdown renderers auto-number. Using all `1.` makes reordering error-free.

**Linter:** markdownlint MD029 (ol-prefix: one)

### 2.3 Task Lists

    ✅ Do:
    - [ ] Pending task
    - [x] Completed task

    ❌ Don't:
    - [ ] Not a task item (use regular `-` for non-task items)
    - [x] Done (but this is just a list item, not a task)

Use task lists only for actionable items with binary status.

### 2.4 Nesting: 2-Space Indent

    ✅ Do:
    - Level 1
      - Level 2
        - Level 3

    ❌ Don't:
    - Level 1
        - Level 2  (4-space indent)
     - Level 2    (1-space indent)

**Linter:** markdownlint MD007 (ul-indent: 2)

## 3. Code Rules

### 3.1 Always Specify Language

    ✅ Do:
    ```python
    def hello():
        print("hello")
    ```

    ❌ Don't:
    ```
    def hello():
        print("hello")
    ```

**Linter:** markdownlint MD040 (fenced-code-language)

### 3.2 Inline Code for Technical Terms

    ✅ Do:
    Use `npm install` to add dependencies.
    The `Array.prototype.map()` method transforms elements.

    ❌ Don't:
    Use **npm install** to add dependencies.
    The Array.prototype.map() method transforms elements.

## 4. Diagram Rules

### 4.1 Mermaid (Default)

Use for: flowcharts, sequence diagrams, class diagrams, state diagrams, Gantt charts.

    ```mermaid
    sequenceDiagram
        participant Client
        participant Server
        Client->>Server: Request
        Server-->>Client: Response
    ```

### 4.2 PlantUML (Professional UML)

Use for: detailed class diagrams, component diagrams, use case diagrams, activity diagrams with swimlanes.

    ```plantuml
    @startuml
    class User {
      +name: string
      +email: string
    }
    @enduml
    ```

Note: PlantUML requires a render server. Use `http://www.plantuml.com/plantuml` or self-hosted.

### 4.3 SVG (Pixel-Perfect Custom)

Use for: custom illustrations, branded graphics, complex layouts that Mermaid/PlantUML can't express.

    ![Architecture](./assets/architecture.svg)

### 4.4 ASCII (Fallback / Explicit Request)

Use for: environments without diagram rendering, terminal-only contexts, when user explicitly asks.

    +----------+     +----------+
    |  Client  |---->|  Server  |
    +----------+     +----------+

## 5. Table Rules

### 5.1 Always Have Header Row

    ✅ Do:
    | Name | Type | Description |
    |------|------|-------------|
    | id   | int  | Primary key |

    ❌ Don't:
    | id   | int  | Primary key |
    |------|------|-------------|

**Linter:** No direct rule. Convention.

### 5.2 Align Pipes Vertically

    ✅ Do:
    | Name     | Type     | Description      |
    |----------|----------|------------------|
    | id       | int      | Primary key      |
    | username | varchar  | User login name  |

    ❌ Don't:
    |Name|Type|Description|
    |---|---|---|
    |id|int|Primary key|
    |username|varchar|User login name|

Rationale: Easier to read and edit in source form.

### 5.3 Column Alignment Markers

    | Left     | Center     | Right    |
    |:---------|:----------:|---------:|
    | Default  | Centered   | Numbers  |

## 6. Image Rules

### 6.1 Alt Text Required

    ✅ Do:
    ![System architecture overview](./assets/arch.png)
    ![Login form with error state](./images/login-error.png)

    ❌ Don't:
    ![](./assets/arch.png)
    ![image](./images/login-error.png)
    ![screenshot](./images/login-error.png)

**Linter:** markdownlint MD045 (no-alt-text), GitHub accessibility rules

### 6.2 Relative Paths

    ✅ Do:
    ![Diagram](./assets/diagram.png)
    ![Figure 1](../images/fig1.png)

    ❌ Don't:
    ![Diagram](/home/user/project/assets/diagram.png)
    ![Diagram](https://example.com/diagram.png)

Exception: external images that are intentionally remote.

### 6.3 Image Directory Convention

    project/
    ├── docs/
    │   ├── guide.md
    │   └── assets/       ← images for docs
    │       ├── fig1.png
    │       └── fig2.png
    ├── README.md
    └── assets/           ← images for root-level docs
        └── logo.png

## 7. Link & Reference Rules

### 7.1 Inline Links (Default)

    ✅ Do:
    See the [React documentation](https://react.dev) for details.

    ❌ Don't:
    See the React documentation (https://react.dev) for details.

### 7.2 Footnote-Style References (Long Docs)

For documents with 5+ external references:

    According to the spec[^1], the behavior is defined as...

    [^1]: https://spec.example.com/section-3

Footnotes render at the bottom of the document. Use `[^label]` syntax — the label can be a number or a descriptive word.

### 7.3 Cross-References Within Repo

    ✅ Do:
    See [API Reference](./api.md) and [Contributing](../CONTRIBUTING.md).

    ❌ Don't:
    See API Reference (absolute path or URL for internal files).

**Linter:** markdownlint MD011 (no-reversed-links), MD034 (no-bare-urls)

## 8. Chinese Typography Rules

Based on [chinese-copywriting-guidelines](https://github.com/sparanoid/chinese-copywriting-guidelines).

### 8.1 Space Between CJK and Latin/Number

    ✅ Do:
    使用 React 开发前端应用
    版本 3.2 修复了 12 个问题
    在 Linux 系统上运行

    ❌ Don't:
    使用React开发前端应用
    版本3.2修复了12个问题
    在Linux系统上运行

**Linter:** lint-md L003, AutoCorrect

### 8.2 Full-Width Punctuation in Chinese

    ✅ Do:
    这是一个示例，包含中文标点。
    请问：你确定吗？
    是的！

    ❌ Don't:
    这是一个示例,包含中文标点.
    请问:你确定吗?
    是的!

**Linter:** lint-md L004, AutoCorrect

### 8.3 Numbers and Units

    ✅ Do:
    10 GB、100 Mbps、30 分钟
    2024 年 1 月 1 日

    ❌ Don't:
    10GB、100Mbps、30分钟
    2024年1月1日

### 8.4 Ellipsis and Em Dash

    ✅ Do:
    他想了想……最终决定了。
    这是一条——重要的——规则。

    ❌ Don't:
    他想了想...最终决定了。
    这是一条--重要的--规则。

### 8.5 English Rules Inside English

    ✅ Do:
    使用 JavaScript 的 Array.prototype.map() 方法

    ❌ Don't:
    使用 javascript 的 array.prototype.map() 方法

## 9. Spacing Rules

### 9.1 Blank Line Requirements

Every structural element needs breathing room:

    ✅ Do:

    # Title

    Paragraph text.

    ## Section

    - Item 1
    - Item 2

    ```code
    block
    ```

    More text.

    ❌ Don't:
    # Title
    Paragraph text.
    ## Section
    - Item 1
    - Item 2
    ```code
    block
    ```
    More text.

**Linter:** markdownlint MD012 (no-multiple-blanks), MD022 (blanks-around-headings), MD031 (blanks-around-fences)

### 9.2 Trailing Newline

Every file must end with exactly one newline character.

**Linter:** markdownlint MD047 (single-trailing-newline)

### 9.3 No Multiple Consecutive Blank Lines

    ✅ Do:
    Paragraph 1.

    Paragraph 2.

    ❌ Don't:
    Paragraph 1.


    Paragraph 2.

**Linter:** markdownlint MD012 (no-multiple-blanks)

## 10. Miscellaneous

### 10.1 No HTML Unless Necessary

    ✅ Do:
    | Col1 | Col2 |
    |------|------|
    | A    | B    |

    ❌ Don't:
    <table><tr><td>A</td><td>B</td></tr></table>

Exception: `<details>/<summary>` for collapsible content, `<br>` where markdown can't express the layout.

**Linter:** markdownlint MD033 (no-inline-html)

### 10.2 Emphasis

    ✅ Do:
    **bold** for strong emphasis
    *italic* for light emphasis

    ❌ Don't:
    __bold__ (underscore style)
    _italic_ (may render differently in some parsers)

**Linter:** markdownlint MD049 (emphasis-style: asterisk), MD050 (strong-style: asterisk)

### 10.3 YAML Frontmatter

When a document needs metadata, use YAML frontmatter at the very top:

    ---
    title: Document Title
    date: 2024-01-01
    tags: [guide, reference]
    ---

Keep frontmatter minimal. Only include fields that downstream tools actually consume.

## Linter Quick Setup

### markdownlint

    # Install
    npm install -g markdownlint-cli

    # Check single file
    markdownlint path/to/file.md

    # Check with config
    markdownlint -c .markdownlint.json path/to/file.md

    # Auto-fix (some rules)
    markdownlint --fix path/to/file.md

Recommended `.markdownlint.json`:

    {
      "default": true,
      "MD003": { "style": "atx" },
      "MD004": { "style": "dash" },
      "MD007": { "indent": 2 },
      "MD029": { "style": "one" },
      "MD040": true,
      "MD045": true,
      "MD047": true,
      "MD049": { "style": "asterisk" },
      "MD050": { "style": "asterisk" }
    }

### lint-md (Chinese-specific)

    # Install
    npm install -g lint-md

    # Check
    lint-md path/to/file.md

    # Auto-fix
    lint-md path/to/file.md --fix

Key rules: L003 (CJK-Latin space), L004 (full-width punctuation), L009 (image alt text).

### AutoCorrect (CJK Typography)

    # Install
    npm install -g autocorrect

    # Check
    autocorrect path/to/file.md

    # Auto-fix
    autocorrect path/to/file.md --fix

Handles: CJK-Latin spacing, full/half-width punctuation, number-unit spacing.
