#!/usr/bin/env python3
"""
Aggregate individual run results into benchmark summary statistics.

Usage:
    python aggregate_benchmark.py <benchmark_dir> [--skill-name NAME] [--skill-path PATH]

Reads grading.json from run directories, produces benchmark.json + benchmark.md.
"""

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path


def calculate_stats(values: list[float]) -> dict:
    if not values:
        return {"mean": 0.0, "stddev": 0.0, "min": 0.0, "max": 0.0}
    n = len(values)
    mean = sum(values) / n
    stddev = math.sqrt(sum((x - mean) ** 2 for x in values) / (n - 1)) if n > 1 else 0.0
    return {"mean": round(mean, 4), "stddev": round(stddev, 4), "min": round(min(values), 4), "max": round(max(values), 4)}


def load_run_results(benchmark_dir: Path) -> dict:
    runs_dir = benchmark_dir / "runs"
    if runs_dir.exists():
        search_dir = runs_dir
    elif list(benchmark_dir.glob("eval-*")):
        search_dir = benchmark_dir
    else:
        print(f"No eval directories found in {benchmark_dir}")
        return {}

    results: dict[str, list] = {}
    for eval_idx, eval_dir in enumerate(sorted(search_dir.glob("eval-*"))):
        metadata_path = eval_dir / "eval_metadata.json"
        if metadata_path.exists():
            try:
                eval_id = json.load(open(metadata_path)).get("eval_id", eval_idx)
            except (json.JSONDecodeError, OSError):
                eval_id = eval_idx
        else:
            try:
                eval_id = int(eval_dir.name.split("-")[1])
            except ValueError:
                eval_id = eval_idx

        for config_dir in sorted(eval_dir.iterdir()):
            if not config_dir.is_dir():
                continue
            if not list(config_dir.glob("run-*")) and not (config_dir / "grading.json").exists():
                continue
            config = config_dir.name
            if config not in results:
                results[config] = []

            run_dirs = sorted(config_dir.glob("run-*"))
            if not run_dirs:
                run_dirs = [config_dir]

            for run_dir in run_dirs:
                grading_file = run_dir / "grading.json"
                if not grading_file.exists():
                    print(f"Warning: grading.json not found in {run_dir}")
                    continue
                try:
                    grading = json.load(open(grading_file))
                except json.JSONDecodeError as e:
                    print(f"Warning: Invalid JSON in {grading_file}: {e}")
                    continue

                result = {
                    "eval_id": eval_id,
                    "pass_rate": grading.get("summary", {}).get("pass_rate", 0.0),
                    "passed": grading.get("summary", {}).get("passed", 0),
                    "failed": grading.get("summary", {}).get("failed", 0),
                    "total": grading.get("summary", {}).get("total", 0),
                }

                timing = grading.get("timing", {})
                result["time_seconds"] = timing.get("total_duration_seconds", 0.0)
                timing_file = run_dir / "timing.json"
                if result["time_seconds"] == 0.0 and timing_file.exists():
                    try:
                        timing_data = json.load(open(timing_file))
                        result["time_seconds"] = timing_data.get("total_duration_seconds", 0.0)
                        result["tokens"] = timing_data.get("total_tokens", 0)
                    except json.JSONDecodeError:
                        pass

                result["tokens"] = result.get("tokens", 0) or grading.get("execution_metrics", {}).get("output_chars", 0)
                result["expectations"] = grading.get("expectations", [])
                results[config].append(result)

    return results


def aggregate_results(results: dict) -> dict:
    run_summary = {}
    configs = list(results.keys())

    for config in configs:
        runs = results.get(config, [])
        if not runs:
            run_summary[config] = {"pass_rate": {"mean": 0.0, "stddev": 0.0, "min": 0.0, "max": 0.0}, "time_seconds": {"mean": 0.0, "stddev": 0.0, "min": 0.0, "max": 0.0}, "tokens": {"mean": 0, "stddev": 0, "min": 0, "max": 0}}
            continue
        run_summary[config] = {
            "pass_rate": calculate_stats([r["pass_rate"] for r in runs]),
            "time_seconds": calculate_stats([r["time_seconds"] for r in runs]),
            "tokens": calculate_stats([r.get("tokens", 0) for r in runs]),
        }

    if len(configs) >= 2:
        primary = run_summary.get(configs[0], {})
        baseline = run_summary.get(configs[1], {})
    else:
        primary = run_summary.get(configs[0], {}) if configs else {}
        baseline = {}

    delta_pr = primary.get("pass_rate", {}).get("mean", 0) - baseline.get("pass_rate", {}).get("mean", 0)
    delta_time = primary.get("time_seconds", {}).get("mean", 0) - baseline.get("time_seconds", {}).get("mean", 0)
    delta_tokens = primary.get("tokens", {}).get("mean", 0) - baseline.get("tokens", {}).get("mean", 0)

    run_summary["delta"] = {"pass_rate": f"{delta_pr:+.2f}", "time_seconds": f"{delta_time:+.1f}", "tokens": f"{delta_tokens:+.0f}"}
    return run_summary


def generate_markdown(benchmark: dict) -> str:
    metadata = benchmark["metadata"]
    run_summary = benchmark["run_summary"]
    configs = [k for k in run_summary if k != "delta"]
    config_a = configs[0] if len(configs) >= 1 else "config_a"
    config_b = configs[1] if len(configs) >= 2 else "config_b"
    label_a = config_a.replace("_", " ").title()
    label_b = config_b.replace("_", " ").title()

    lines = [
        f"# Skill Benchmark: {metadata['skill_name']}", "",
        f"**Model**: {metadata['executor_model']}", f"**Date**: {metadata['timestamp']}", "",
        "## Summary", "",
        f"| Metric | {label_a} | {label_b} | Delta |", "|--------|------------|---------------|-------|",
    ]

    a = run_summary.get(config_a, {})
    b = run_summary.get(config_b, {})
    delta = run_summary.get("delta", {})

    a_pr = a.get("pass_rate", {})
    b_pr = b.get("pass_rate", {})
    lines.append(f"| Pass Rate | {a_pr.get('mean',0)*100:.0f}% ± {a_pr.get('stddev',0)*100:.0f}% | {b_pr.get('mean',0)*100:.0f}% ± {b_pr.get('stddev',0)*100:.0f}% | {delta.get('pass_rate', '—')} |")

    a_time = a.get("time_seconds", {})
    b_time = b.get("time_seconds", {})
    lines.append(f"| Time | {a_time.get('mean',0):.1f}s ± {a_time.get('stddev',0):.1f}s | {b_time.get('mean',0):.1f}s ± {b_time.get('stddev',0):.1f}s | {delta.get('time_seconds', '—')}s |")

    a_tok = a.get("tokens", {})
    b_tok = b.get("tokens", {})
    lines.append(f"| Tokens | {a_tok.get('mean',0):.0f} ± {a_tok.get('stddev',0):.0f} | {b_tok.get('mean',0):.0f} ± {b_tok.get('stddev',0):.0f} | {delta.get('tokens', '—')} |")

    if benchmark.get("notes"):
        lines.extend(["", "## Notes", ""])
        for note in benchmark["notes"]:
            lines.append(f"- {note}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Aggregate benchmark run results")
    parser.add_argument("benchmark_dir", type=Path)
    parser.add_argument("--skill-name", default="")
    parser.add_argument("--skill-path", default="")
    parser.add_argument("--output", "-o", type=Path)
    args = parser.parse_args()

    if not args.benchmark_dir.exists():
        print(f"Directory not found: {args.benchmark_dir}")
        sys.exit(1)

    results = load_run_results(args.benchmark_dir)
    run_summary = aggregate_results(results)

    runs = []
    for config in results:
        for result in results[config]:
            runs.append({"eval_id": result["eval_id"], "configuration": config, "result": {"pass_rate": result["pass_rate"], "passed": result["passed"], "failed": result["failed"], "total": result["total"], "time_seconds": result["time_seconds"], "tokens": result.get("tokens", 0)}, "expectations": result.get("expectations", [])})

    eval_ids = sorted(set(r["eval_id"] for config in results.values() for r in config))
    benchmark = {"metadata": {"skill_name": args.skill_name or "<skill-name>", "skill_path": args.skill_path or "<path>", "executor_model": "<model>", "analyzer_model": "<model>", "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "evals_run": eval_ids, "runs_per_configuration": 3}, "runs": runs, "run_summary": run_summary, "notes": []}

    output_json = args.output or (args.benchmark_dir / "benchmark.json")
    output_md = output_json.with_suffix(".md")

    with open(output_json, "w") as f:
        json.dump(benchmark, f, indent=2)
    print(f"Generated: {output_json}")

    markdown = generate_markdown(benchmark)
    with open(output_md, "w") as f:
        f.write(markdown)
    print(f"Generated: {output_md}")

    for config in [k for k in run_summary if k != "delta"]:
        pr = run_summary[config]["pass_rate"]["mean"]
        print(f"  {config.replace('_', ' ').title()}: {pr*100:.1f}% pass rate")
    print(f"  Delta: {run_summary.get('delta', {}).get('pass_rate', '—')}")


if __name__ == "__main__":
    main()
