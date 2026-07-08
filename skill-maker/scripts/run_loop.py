#!/usr/bin/env python3
"""Run the eval + improve loop for description optimization.

Combines run_eval.py and improve_description.py in a loop.
Requires: claude CLI (Claude Code) installed and authenticated.
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path

from scripts.generate_review import generate_html, VIEWER_TEMPLATE
from scripts.improve_description import improve_description
from scripts.run_eval import find_project_root, run_eval
from scripts.utils import parse_skill_md


def split_eval_set(eval_set: list[dict], holdout: float, seed: int = 42) -> tuple[list[dict], list[dict]]:
    random.seed(seed)
    trigger = [e for e in eval_set if e["should_trigger"]]
    no_trigger = [e for e in eval_set if not e["should_trigger"]]
    random.shuffle(trigger)
    random.shuffle(no_trigger)
    n_t = max(1, int(len(trigger) * holdout))
    n_nt = max(1, int(len(no_trigger) * holdout))
    return trigger[n_t:] + no_trigger[n_nt:], trigger[:n_t] + no_trigger[:n_nt]


def run_loop(
    eval_set: list[dict],
    skill_path: Path,
    description_override: str | None,
    num_workers: int,
    timeout: int,
    max_iterations: int,
    runs_per_query: int,
    trigger_threshold: float,
    holdout: float,
    model: str,
    verbose: bool,
) -> dict:
    project_root = find_project_root()
    name, original_description, content = parse_skill_md(skill_path)
    current_description = description_override or original_description

    if holdout > 0:
        train_set, test_set = split_eval_set(eval_set, holdout)
        if verbose:
            print(f"Split: {len(train_set)} train, {len(test_set)} test", file=sys.stderr)
    else:
        train_set = eval_set
        test_set = []

    history = []
    best_description = current_description
    best_test_score = -1.0

    for iteration in range(1, max_iterations + 1):
        if verbose:
            print(f"\n{'='*60}\nIteration {iteration}/{max_iterations}\nDescription: {current_description}\n{'='*60}", file=sys.stderr)

        all_queries = train_set + test_set
        t0 = time.time()
        all_results = run_eval(eval_set=all_queries, skill_name=name, description=current_description, num_workers=num_workers, timeout=timeout, project_root=project_root, runs_per_query=runs_per_query, trigger_threshold=trigger_threshold, model=model)
        eval_elapsed = time.time() - t0

        train_queries = {q["query"] for q in train_set}
        train_results = [r for r in all_results["results"] if r["query"] in train_queries]
        test_results = [r for r in all_results["results"] if r["query"] not in train_queries]

        train_score = sum(1 for r in train_results if r["pass"]) / len(train_results) if train_results else 0
        test_score = sum(1 for r in test_results if r["pass"]) / len(test_results) if test_results else train_score

        if verbose:
            print(f"Train: {train_score:.1%} | Test: {test_score:.1%} | Time: {eval_elapsed:.0f}s", file=sys.stderr)

        history.append({
            "iteration": iteration,
            "description": current_description,
            "train_score": train_score,
            "test_score": test_score,
            "elapsed_seconds": eval_elapsed,
        })

        if test_score > best_test_score:
            best_test_score = test_score
            best_description = current_description

        if train_score >= 1.0 and (not test_set or test_score >= 1.0):
            if verbose:
                print("All passing — stopping early.", file=sys.stderr)
            break

        improved = improve_description(skill_path, current_description, all_results, model)
        if improved == current_description:
            if verbose:
                print("No improvement proposed — stopping.", file=sys.stderr)
            break
        current_description = improved

    return {
        "best_description": best_description,
        "best_test_score": best_test_score,
        "history": history,
        "total_iterations": len(history),
    }


def main():
    parser = argparse.ArgumentParser(description="Run description optimization loop")
    parser.add_argument("--eval-set", required=True, type=Path)
    parser.add_argument("--skill-path", required=True, type=Path)
    parser.add_argument("--description", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-iterations", type=int, default=5)
    parser.add_argument("--num-workers", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--runs-per-query", type=int, default=3)
    parser.add_argument("--trigger-threshold", type=float, default=0.5)
    parser.add_argument("--holdout", type=float, default=0.4)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    eval_set = json.loads(args.eval_set.read_text())
    result = run_loop(
        eval_set=eval_set,
        skill_path=args.skill_path,
        description_override=args.description,
        num_workers=args.num_workers,
        timeout=args.timeout,
        max_iterations=args.max_iterations,
        runs_per_query=args.runs_per_query,
        trigger_threshold=args.trigger_threshold,
        holdout=args.holdout,
        model=args.model,
        verbose=args.verbose,
    )

    print(json.dumps(result, indent=2))

    if args.verbose:
        print(f"\nBest description (test score: {result['best_test_score']:.1%}):", file=sys.stderr)
        print(result["best_description"], file=sys.stderr)


if __name__ == "__main__":
    main()
