# AGENTS.md

## What This Repo Is

Shared skill directory for AI coding agents. Each subdirectory is a self-contained skill with a `SKILL.md` entrypoint. Skills are deployed via symlinks, not copied.

## Structure

- Each skill = one top-level directory with `SKILL.md` as the entrypoint
- Supporting files go in `templates/`, `reference/`, `scripts/`, `agents/`, `assets/`, or `evals/` under the skill directory
- No build step, no root package.json, no CI — skills are instruction files; per-skill `scripts/` may still be runnable CLIs with their own dependencies

## Skill Deployment

Symlink from agent config dir; never copy:

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
| `repomap-lite`    | Zero-dependency multi-language codebase map (REPOMAP.md)                     |
| `skill-reviewer`  | Systematic skill quality evaluation framework                                 |
| `skill-maker`     | Create, test, iterate, and optimize agent skills with eval toolchain          |
| `writing-markdown`| Consistent Markdown style: headings, fences, diagrams, tables, Chinese typography |

## Conventions

- SKILL.md frontmatter: `name` (gerund/verb-first, e.g. `skill-maker`) and `description` (starts with "Use when")
- SKILL.md is the single source of truth; reference/template files are supplementary
- Content is bilingual (Chinese + English) — preserve both when editing
- Docs in this repo (README.md, AGENTS.md) follow the `writing-markdown` skill style
- playwright-cdp scripts run with `NODE_PATH=$(npm root -g) npx tsx script.ts` (Playwright is a global install, not local)
- vision-engine CLI requires `pip install -r vision-engine/scripts/requirements.txt`; config in `vision-engine/config/vision-config.json`

## When Editing Skills

- Follow the skill-reviewer evaluation dimensions (discoverability, executability, correctness, etc.)
- Keep SKILL.md under 500 lines; move detail to `reference/`
- Code snippets must be copy-paste-runnable, no `...` or `TODO` placeholders
- Test Playwright scripts against CDP at `127.0.0.1:18800` — never use `chromium.launch()`
- skill-maker scripts require Python 3.10+ and `pip install pyyaml`; description optimization also requires `claude` CLI

## Reference Docs

`01-reference/` contains design docs and background materials, not skills.
