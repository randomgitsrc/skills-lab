#!/usr/bin/env python3
"""Improve a skill description based on eval results.

Takes failing eval results and proposes an improved description.
Requires: claude CLI (Claude Code) for LLM-powered improvement.
"""

import argparse
import json
import sys
from pathlib import Path

from scripts.utils import parse_skill_md


IMPROVE_PROMPT = """You are a skill description optimizer. Given the current description and eval results, propose an improved description.

Rules:
1. Start with "Use when..." — focus on triggering conditions
2. Third person — no "I can" / "You can"
3. Include specific triggers: error messages, tool names, scenarios
4. NEVER summarize the skill's workflow in the description
5. Keep under 500 characters if possible; max 1024
6. No angle brackets (< or >)

Current description:
{current_description}

Eval results:
{eval_results}

Failed queries (should have triggered but didn't):
{failed_triggers}

Failed queries (should NOT have triggered but did):
{false_triggers}

Propose an improved description that fixes these failures. Output ONLY the new description text, nothing else."""


def improve_description(
    skill_path: Path,
    current_description: str,
    eval_results: dict,
    model: str | None = None,
) -> str:
    failed_triggers = []
    false_triggers = []
    for r in eval_results.get("results", []):
        if not r["pass"]:
            if r["should_trigger"]:
                failed_triggers.append(f"  Query: {r['query'][:100]} (triggered {r['triggers']}/{r['runs']} times)")
            else:
                false_triggers.append(f"  Query: {r['query'][:100]} (triggered {r['triggers']}/{r['runs']} times)")

    if not failed_triggers and not false_triggers:
        return current_description

    prompt = IMPROVE_PROMPT.format(
        current_description=current_description,
        eval_results=json.dumps(eval_results["summary"], indent=2),
        failed_triggers="\n".join(failed_triggers) or "  None",
        false_triggers="\n".join(false_triggers) or "  None",
    )

    try:
        import subprocess
        cmd = ["claude", "-p", prompt, "--output-format", "text"]
        if model:
            cmd.extend(["--model", model])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        new_description = result.stdout.strip()
        if new_description and len(new_description) <= 1024:
            return new_description
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return current_description


def main():
    parser = argparse.ArgumentParser(description="Improve skill description based on eval results")
    parser.add_argument("--skill-path", required=True, type=Path)
    parser.add_argument("--eval-results", required=True, type=Path)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    name, description, content = parse_skill_md(args.skill_path)
    eval_results = json.load(open(args.eval_results))

    improved = improve_description(args.skill_path, description, eval_results, args.model)
    print(improved)


if __name__ == "__main__":
    main()
