# skills-lab

Shared skill directory — single source of truth for agent skills used across multiple AI coding agents.

## Usage

Skills are real files here; agents discover them via symlinks:

```bash
# OpenCode
ln -s ~/oclab/skills-lab/<skill> ~/.config/opencode/skills/

# Claude Code
ln -s ~/oclab/skills-lab/<skill> ~/.claude/skills/
```

## Current Skills

| Skill | Purpose |
|-------|---------|
| `playwright-cdp` | Chrome CDP browser automation via Playwright |
| `vision-engine` | Unified vision analysis: multi-model routing, bounding box, structured JSON output |
| `skill-reviewer` | Systematic skill quality evaluation framework |
| `skill-maker` | Create, test, iterate, and optimize agent skills with eval toolchain |

## Skill Structure

```
<skill>/
  SKILL.md          # Entrypoint — single source of truth
  templates/        # Copy-paste-runnable code templates
  reference/        # Supplementary detail (troubleshooting, API docs)
  scripts/          # CLI tools
  agents/           # Subagent instructions (grader, analyzer, comparator)
  assets/           # Templates, static files
```

## Conventions

- SKILL.md frontmatter: `name` (gerund/verb-first) and `description` (starts with "Use when")
- Keep SKILL.md under 500 lines; move detail to `reference/`
- Content is bilingual (Chinese + English)
- Code snippets must be copy-paste-runnable — no `...` or `TODO` placeholders

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
