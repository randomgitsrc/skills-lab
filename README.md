# skills-lab

Shared skill directory — single source of truth for agent skills used across multiple AI coding agents.

## Usage

Skills are real files here; agents discover them via symlinks:

```bash
# OpenCode
ln -s ~/oclab/skills-lab/<skill> ~/.config/opencode/skills/

# Claude Code
ln -s ~/oclab/skills-lab/<skill> ~/.claude/skills/

# DeepSeek Harness (DSH) — two discovery roots, both are scanned
ln -s ~/oclab/skills-lab/<skill> ~/.dsh/skills/
ln -s ~/oclab/skills-lab/<skill> ~/.agents/skills/
```

DSH scans both `~/.dsh/skills/` (user-dsh) and `~/.agents/skills/` (user-agents). Same-name skills are auto-deduplicated by priority rank — user-dsh wins over user-agents — with a harmless warning logged for the ignored copy. Linking each skill into one root is still recommended to keep it noise-free.

## Current Skills

| Skill           | Purpose                                                                        |
|-----------------|--------------------------------------------------------------------------------|
| `playwright-cdp`  | Chrome CDP browser automation via Playwright                                  |
| `vision-engine`   | Unified vision analysis: multi-model routing, bounding box, structured JSON output |
| `skill-reviewer`  | Systematic skill quality evaluation framework                                 |
| `skill-maker`     | Create, test, iterate, and optimize agent skills with eval toolchain          |
| `writing-markdown`| Consistent Markdown style: headings, fences, diagrams, tables, Chinese typography |

## Skill Structure

```text
<skill>/
  SKILL.md          # Entrypoint — single source of truth
  templates/        # Copy-paste-runnable code templates
  reference/        # Supplementary detail (troubleshooting, API docs)
  scripts/          # CLI tools
  agents/           # Subagent instructions (grader, analyzer, comparator)
  assets/           # Templates, static files
  evals/            # Eval cases for skill quality testing
```

## Conventions

- SKILL.md frontmatter: `name` (gerund/verb-first) and `description` (starts with "Use when")
- Keep SKILL.md under 500 lines; move detail to `reference/`
- Content is bilingual (Chinese + English)
- Code snippets must be copy-paste-runnable — no `...` or `TODO` placeholders
- Docs in this repo (README.md, AGENTS.md) follow the `writing-markdown` skill style

## Running Playwright Scripts

```bash
NODE_PATH=$(npm root -g) npx tsx script.ts
```

Playwright is a global install. Connect via CDP at `127.0.0.1:18800` — never `chromium.launch()`.

## Vision Engine CLI

```bash
pip install -r vision-engine/scripts/requirements.txt
python vision-engine/scripts/vision-analyze.py -i /tmp/screenshot.png -p "描述"
```

Config in `vision-engine/config/vision-config.json`. Supports multiple vision providers with automatic fallback.
