# JSON Schemas

## evals.json

```json
{
  "skill_name": "string",
  "evals": [
    {
      "id": "integer",
      "prompt": "string — realistic user task prompt",
      "expected_output": "string — description of expected result",
      "files": ["string — paths to input files, if any"]
    }
  ]
}
```

## eval_metadata.json

Per-eval directory:

```json
{
  "eval_id": "integer",
  "eval_name": "string — descriptive name, not just 'eval-0'",
  "prompt": "string",
  "assertions": [
    {
      "name": "string — descriptive assertion name",
      "type": "string — 'contains' | 'not_contains' | 'regex' | 'file_exists' | 'custom'",
      "expected": "string — what to check for",
      "weight": "float — 1.0 default"
    }
  ]
}
```

## grading.json

Per run directory:

```json
{
  "eval_id": "integer",
  "run_number": "integer",
  "configuration": "string — 'with_skill' | 'without_skill' | 'old_skill'",
  "summary": {
    "pass_rate": "float — 0.0 to 1.0",
    "passed": "integer",
    "failed": "integer",
    "total": "integer"
  },
  "expectations": [
    {
      "text": "string — what was checked",
      "passed": "boolean",
      "evidence": "string — why it passed or failed"
    }
  ],
  "timing": {
    "total_duration_seconds": "float",
    "total_tokens": "integer"
  }
}
```

## timing.json

Per run directory, captured from subagent task notification:

```json
{
  "total_tokens": "integer",
  "duration_ms": "integer",
  "total_duration_seconds": "float"
}
```

## benchmark.json

Aggregated from all runs in an iteration:

```json
{
  "metadata": {
    "skill_name": "string",
    "skill_path": "string",
    "executor_model": "string",
    "analyzer_model": "string",
    "timestamp": "ISO 8601",
    "evals_run": ["integer"],
    "runs_per_configuration": "integer"
  },
  "runs": [
    {
      "eval_id": "integer",
      "configuration": "string",
      "run_number": "integer",
      "result": {
        "pass_rate": "float",
        "passed": "integer",
        "failed": "integer",
        "total": "integer",
        "time_seconds": "float",
        "tokens": "integer"
      },
      "expectations": [{"text": "string", "passed": "boolean", "evidence": "string"}]
    }
  ],
  "run_summary": {
    "with_skill": {
      "pass_rate": {"mean": "float", "stddev": "float", "min": "float", "max": "float"},
      "time_seconds": {"mean": "float", "stddev": "float", "min": "float", "max": "float"},
      "tokens": {"mean": "float", "stddev": "float", "min": "float", "max": "float"}
    },
    "without_skill": { "...same structure..." },
    "delta": {
      "pass_rate": "string — e.g. '+0.15'",
      "time_seconds": "string — e.g. '+3.2'",
      "tokens": "string — e.g. '+500'"
    }
  },
  "notes": ["string"]
}
```

## feedback.json

From eval viewer user review:

```json
{
  "reviews": [
    {
      "run_id": "string — e.g. 'eval-0-with_skill'",
      "feedback": "string — user's feedback, empty = fine",
      "timestamp": "ISO 8601"
    }
  ],
  "status": "string — 'complete'"
}
```

## trigger_eval.json

For description optimization:

```json
[
  {"query": "string — realistic user prompt", "should_trigger": "boolean"}
]
```

## Workspace Directory Layout

```
<skill-name>-workspace/
├── skill-snapshot/          # Snapshot of original skill (for improvements)
├── iteration-1/
│   ├── benchmark.json
│   ├── benchmark.md
│   ├── feedback.json
│   └── eval-0/
│       ├── eval_metadata.json
│       ├── with_skill/
│       │   ├── timing.json
│       │   ├── grading.json
│       │   └── outputs/
│       └── without_skill/
│           ├── timing.json
│           ├── grading.json
│           └── outputs/
└── iteration-2/
    └── ...
```
