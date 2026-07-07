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

## Current skills

| Skill | Description |
|-------|-------------|
| `playwright-cdp` | Chrome CDP browser automation (screenshots, navigation, WebGL) |
| `vision-analyzer` | Image analysis via vision helper agent |
