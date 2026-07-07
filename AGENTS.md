# AGENTS.md

## What This Repo Is

Shared skill directory for AI coding agents. Each subdirectory is a self-contained skill with a `SKILL.md` entrypoint. Skills are deployed via symlinks, not copied.

## Structure

- Each skill = one top-level directory with `SKILL.md` as the entrypoint
- Supporting files go in `templates/`, `reference/`, or `scripts/` under the skill directory
- No build step, no package.json, no CI — these are instruction files, not runnable packages

## Skill Deployment

Symlink from agent config dir; never copy:

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

## Conventions

- SKILL.md frontmatter: `name` (gerund/verb-first, e.g. `reviewing-skills`) and `description` (starts with "Use when")
- SKILL.md is the single source of truth; reference/template files are supplementary
- Content is bilingual (Chinese + English) — preserve both when editing
- playwright-cdp scripts run with `NODE_PATH=$(npm root -g) npx tsx script.ts` (Playwright is a global install, not local)
- vision-analyzer CLI requires `.env` with `VISION_API_KEY`, `VISION_API_BASE_URL`, `VISION_MODEL`, `VISION_API_FORMAT`

## When Editing Skills

- Follow the reviewing-skills evaluation dimensions (discoverability, executability, correctness, etc.)
- Keep SKILL.md under 500 lines; move detail to `reference/`
- Code snippets must be copy-paste-runnable, no `...` or `TODO` placeholders
- Test Playwright scripts against CDP at `127.0.0.1:18800` — never use `chromium.launch()`
