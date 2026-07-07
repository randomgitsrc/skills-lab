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
| `vision-analyzer` | Image analysis via vision-helper sub-agent or CLI script |
| `reviewing-skills` | Systematic skill quality evaluation framework |

## Skill Structure

```
<skill>/
  SKILL.md          # Entrypoint — single source of truth
  templates/        # Copy-paste-runnable code templates
  reference/        # Supplementary detail (troubleshooting, API docs)
  scripts/          # CLI tools
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

## Vision Analyzer CLI

Requires `.env` with:

```
VISION_API_KEY=sk-xxx
VISION_API_BASE_URL=https://api.example.com
VISION_MODEL=model-name
VISION_API_FORMAT=anthropic
```
