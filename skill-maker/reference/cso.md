# Claude Search Optimization (CSO)

How to make skills discoverable at the right time.

## The Discovery Problem

Agents have 50-100+ skills available. At any moment, they scan skill metadata (name + description) to decide which to load. If your description doesn't match, your skill is invisible — even if it's perfect.

## Description = When to Use, NOT What It Does

The most common and costly mistake: summarizing the skill's workflow in the description.

**Why this matters:** Agents may follow the description instead of reading the full skill. A description saying "code review between tasks" caused an agent to do ONE review, even though the skill's flowchart showed TWO reviews. When the description was changed to just triggering conditions, the agent correctly read the full skill and followed the two-stage process.

The description is a **trigger**, not a **summary**.

## Description Rules

1. **Start with "Use when..."** — focuses on triggering conditions
2. **Include specific triggers:** error messages, tool names, technical keywords, scenarios
3. **Third person** — no "I can" / "You can"
4. **Under 500 characters** if possible; max 1024
5. **No workflow summary** — the description is NOT the skill

## Good vs Bad Examples

```yaml
# ❌ Summarizes workflow — agent may shortcut
description: Use for TDD - write test first, watch it fail, write minimal code, refactor

# ❌ Too abstract
description: For async testing

# ❌ First person
description: I can help you with async tests when they're flaky

# ✅ Triggering conditions only
description: Use when implementing any feature or bugfix, before writing implementation code

# ✅ Specific symptoms + technology
description: Use when using React Router and handling authentication redirects, or when login flows redirect incorrectly after authentication
```

## Keyword Coverage

Use words the agent would search for:
- **Error messages:** "Hook timed out", "ENOTEMPTY", "race condition"
- **Symptoms:** "flaky", "hanging", "zombie", "pollution"
- **Synonyms:** "timeout/hang/freeze", "cleanup/teardown/afterEach"
- **Tools:** actual commands, library names, file types

## Naming Conventions

- **Gerund / verb-first:** `creating-skills` not `skill-creation`
- **By what you DO or core insight:** `condition-based-waiting` not `async-test-helpers`
- **Letters, numbers, hyphens only:** no parentheses, special chars
- **Max 64 characters**

## Progressive Disclosure

Three levels of loading:

1. **Metadata** (name + description) — always in context (~100 words)
2. **SKILL.md body** — loaded when skill triggers (<500 lines ideal)
3. **Reference files** — loaded on demand (unlimited)

Every token in SKILL.md competes with conversation history. Keep it lean.

**Key patterns:**
- SKILL.md < 500 lines; approaching limit → add reference layer
- Reference files clearly linked from SKILL.md with guidance on when to read
- Reference files >300 lines → include table of contents
- One level deep only — no nested references

## Token Efficiency

- Move details to tool help (`--help`) rather than documenting all flags inline
- Use cross-references ("See reference/X.md") instead of repeating content
- Compress examples — one great example beats many mediocre ones
- Eliminate redundancy — same info should appear exactly once

## Description Optimization Loop

For systematic description improvement, use the automated optimization tool:

```bash
python scripts/run_loop.py \
  --eval-set trigger_eval.json \
  --skill-path /path/to/skill \
  --model <model-id> \
  --max-iterations 5 \
  --verbose
```

The tool: splits eval set 60/40 train/test, evaluates current description (3 runs per query), proposes improvements, re-evaluates, iterates. Returns `best_description` by test score to avoid overfitting.

Manual alternative: create trigger eval queries, test description against them, iterate based on which queries fail to trigger.
