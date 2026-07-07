---
name: vision-analyzer
description: "Use when needing to analyze any image — screenshots, design mockups, photos, diagrams, charts, or visual content. Not bound to any specific vision API provider."
---

# Vision Analyzer

Analyze images via multimodal vision API. Supports any provider (Anthropic, OpenAI, MiniMax, etc.) — change `.env` to switch.

## When to Use / Not Use

**Use:** analyze screenshots, verify UI rendering, read diagrams/charts, describe photos, compare visual designs, inspect page elements from images, any task requiring visual understanding.

**Not use:** pure text analysis (→ Read/Grep), non-image files (PDF, video), tasks that don't need visual understanding.

## Two Paths

| Path | When | How |
|------|------|-----|
| **Sub-agent** (vision-helper) | Inside opencode conversation, interactive analysis | `Task(subagent_type="vision-helper", prompt="读取 /path/img.png 并分析")` |
| **CLI script** | In bash pipeline, need yaml output, automation workflow | `scripts/vision-analyze.py -i /path/img.png -p "描述"` |

**Sub-agent** is zero-config, uses opencode's built-in multimodal model. **CLI script** requires `.env` config but works in any bash context.

## Critical Rules

| Rule | Why |
|------|-----|
| Main agent (build/general/explore) must NOT use Read tool on images | Main model cannot process images; will error or return garbage. vision-helper can — its model is multimodal |
| Always dispatch vision-helper sub-agent for image analysis in conversation | vision-helper has multimodal model (MiniMax-M3) + Read permission; it CAN read images |
| CLI script requires `.env` with all 3 vars | Missing any = script exits with error |
| Image must be local file path | Both paths read from local filesystem |
| Supported formats: png, jpg, gif, webp | Other formats may fail |

## Red Flags — STOP and Fix

| Red Flag | Fix |
|----------|-----|
| Using Read tool on image file (from main agent) | Dispatch vision-helper sub-agent instead — it CAN Read images |
| Analyzing image without vision-helper or CLI | Must use one of the two paths |
| CLI fails with "VISION_API_KEY not found" | Create `.env` in project root or `~/.env` |
| CLI fails with "No module named httpx" | `pip install httpx` |

### Rationalization Table

| Excuse | Reality |
|--------|---------|
| "CLI script is overkill for a quick check" | Sub-agent path is zero-config. Use it for quick checks. CLI for pipelines. |
| "I'll describe the image from context" | Without seeing the image, descriptions are hallucinations. Use vision. |

## Quick Reference

| Task | Path | Command |
|------|------|---------|
| Analyze in conversation | Sub-agent | `Task(subagent_type="vision-helper", prompt="读取 /path/img.png 并分析")` |
| Analyze in bash | CLI | `scripts/vision-analyze.py -i /path/img.png -p "描述"` |
| YAML output | CLI | `scripts/vision-analyze.py -i img.png --format yaml` |
| Default description | CLI | `scripts/vision-analyze.py -i img.png` |

## Config (CLI only)

`.env` in project root or `~/.env`:

```bash
VISION_API_KEY=sk-xxx
VISION_API_BASE_URL=https://api.minimaxi.com/anthropic
VISION_MODEL=MiniMax-M3
VISION_API_FORMAT=anthropic    # or openai
```

Change provider = change `.env`. No code changes needed.

## CLI Usage

```bash
# Basic
scripts/vision-analyze.py -i /tmp/screenshot.png -p "描述这张截图"

# Default prompt (describe in detail)
scripts/vision-analyze.py -i /tmp/screenshot.png

# YAML output for pipeline
scripts/vision-analyze.py -i /tmp/screenshot.png -p "输出YAML" --format yaml
```

## Common Mistakes

| Mistake | Fix |
|---------|------|
| [环境] `.env` in wrong directory | Place in project root or `~/` |
| [环境] Missing httpx | `pip install httpx` |
| [路径] Image path is WSL path but script runs on Windows | Use consistent path scheme |
| [API] API timeout (60s default) | Large images may need longer; edit script timeout |
| [API] Image exceeds multimodal API size limit | Resize to ≤20MB before sending |
| [API] Rate limited by provider | Add delay between requests or switch provider |

## Integration with Other Skills

- **playwright-cdp** → screenshot → use vision-analyzer to analyze
- Any skill that produces an image file → use vision-analyzer to interpret
