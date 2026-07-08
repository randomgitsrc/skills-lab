#!/usr/bin/env python3
"""Generate an HTML review page for skill eval results."""

import argparse
import json
import sys
from pathlib import Path


VIEWER_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Skill Eval Review: {skill_name}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #1a1a2e; color: #e0e0e0; }}
  .container {{ max-width: 1200px; margin: 0 auto; }}
  h1 {{ color: #7c3aed; }}
  h2 {{ color: #a78bfa; border-bottom: 1px solid #333; padding-bottom: 8px; }}
  .tabs {{ display: flex; gap: 0; margin-bottom: 20px; }}
  .tab {{ padding: 10px 24px; cursor: pointer; background: #16213e; border: 1px solid #333; border-bottom: none; border-radius: 8px 8px 0 0; color: #aaa; }}
  .tab.active {{ background: #1a1a2e; color: #7c3aed; border-bottom: 2px solid #7c3aed; }}
  .tab-content {{ display: none; }}
  .tab-content.active {{ display: block; }}
  .eval-card {{ background: #16213e; border: 1px solid #333; border-radius: 8px; padding: 16px; margin-bottom: 16px; }}
  .eval-header {{ display: flex; justify-content: space-between; align-items: center; }}
  .eval-name {{ font-weight: bold; font-size: 1.1em; color: #c4b5fd; }}
  .badge {{ padding: 4px 10px; border-radius: 12px; font-size: 0.85em; }}
  .badge.pass {{ background: #065f46; color: #6ee7b7; }}
  .badge.fail {{ background: #7f1d1d; color: #fca5a5; }}
  .output {{ background: #0f172a; border: 1px solid #333; border-radius: 4px; padding: 12px; margin-top: 12px; white-space: pre-wrap; font-family: 'Fira Code', monospace; font-size: 0.9em; max-height: 400px; overflow-y: auto; }}
  .feedback {{ width: 100%; min-height: 80px; background: #0f172a; border: 1px solid #444; border-radius: 4px; color: #e0e0e0; padding: 8px; margin-top: 8px; font-family: inherit; resize: vertical; }}
  .nav {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }}
  .nav button {{ padding: 8px 16px; background: #7c3aed; color: white; border: none; border-radius: 4px; cursor: pointer; }}
  .nav button:hover {{ background: #6d28d9; }}
  .nav span {{ color: #888; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #333; }}
  th {{ color: #a78bfa; }}
  .submit-btn {{ padding: 12px 32px; background: #7c3aed; color: white; border: none; border-radius: 8px; cursor: pointer; font-size: 1.1em; margin-top: 20px; }}
  .submit-btn:hover {{ background: #6d28d9; }}
  .prev-output {{ margin-top: 8px; padding: 8px; background: #1e1e3f; border: 1px dashed #555; border-radius: 4px; }}
  .prev-label {{ color: #888; font-size: 0.85em; margin-bottom: 4px; }}
  .expectations {{ margin-top: 8px; }}
  .exp-item {{ display: flex; gap: 8px; align-items: baseline; padding: 4px 0; }}
  .exp-pass {{ color: #6ee7b7; }}
  .exp-fail {{ color: #fca5a5; }}
  .prev-feedback {{ color: #888; font-size: 0.9em; margin-top: 4px; font-style: italic; }}
</style>
</head>
<body>
<div class="container">
<h1>Skill Eval Review: {skill_name}</h1>
<div class="tabs">
  <div class="tab active" onclick="showTab('outputs')">Outputs</div>
  <div class="tab" onclick="showTab('benchmark')">Benchmark</div>
</div>
<div id="outputs-tab" class="tab-content active">
  <div id="eval-container"></div>
  <div class="nav">
    <button onclick="prevEval()">&#8592; Previous</button>
    <span id="eval-counter"></span>
    <button onclick="nextEval()">Next &#8594;</button>
  </div>
  <button class="submit-btn" onclick="submitReviews()">Submit All Reviews</button>
</div>
<div id="benchmark-tab" class="tab-content">
  <div id="benchmark-content"></div>
</div>
</div>
<script>
const evals = {eval_data};
const benchmark = {benchmark_data};
let currentEval = 0;
const feedback = {};

function showTab(name) {{
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById(name + '-tab').classList.add('active');
  event.target.classList.add('active');
}}

function renderEval(idx) {{
  const e = evals[idx];
  if (!e) return;
  const container = document.getElementById('eval-container');
  const pr = e.pass_rate !== undefined ? (e.pass_rate * 100).toFixed(0) + '%' : 'N/A';
  const badgeClass = e.pass_rate >= 0.8 ? 'pass' : 'fail';
  let html = '<div class="eval-card">';
  html += '<div class="eval-header"><span class="eval-name">' + e.name + '</span><span class="badge ' + badgeClass + '">' + pr + '</span></div>';
  html += '<div style="margin-top:8px;color:#888;">Prompt: ' + e.prompt + '</div>';
  if (e.output) html += '<div class="output">' + e.output + '</div>';
  if (e.previous_output) html += '<div class="prev-output"><div class="prev-label">Previous output:</div><div class="output">' + e.previous_output + '</div></div>';
  if (e.expectations && e.expectations.length) {{
    html += '<div class="expectations"><strong>Grades:</strong>';
    e.expectations.forEach(exp => {{
      const cls = exp.passed ? 'exp-pass' : 'exp-fail';
      const icon = exp.passed ? '&#10003;' : '&#10007;';
      html += '<div class="exp-item"><span class="' + cls + '">' + icon + '</span><span>' + exp.text + '</span></div>';
    }});
    html += '</div>';
  }}
  if (e.previous_feedback) html += '<div class="prev-feedback">Previous feedback: ' + e.previous_feedback + '</div>';
  html += '<textarea class="feedback" id="fb-' + idx + '" placeholder="Your feedback..." oninput="feedback[' + idx + ']=this.value">' + (feedback[idx]||'') + '</textarea>';
  html += '</div>';
  container.innerHTML = html;
  document.getElementById('eval-counter').textContent = (idx+1) + ' / ' + evals.length;
}}

function prevEval() {{ if (currentEval > 0) {{ currentEval--; renderEval(currentEval); }} }}
function nextEval() {{ if (currentEval < evals.length - 1) {{ currentEval++; renderEval(currentEval); }} }}

document.addEventListener('keydown', e => {{
  if (e.key === 'ArrowLeft') prevEval();
  if (e.key === 'ArrowRight') nextEval();
}});

function submitReviews() {{
  const reviews = evals.map((e, i) => ({{
    run_id: e.run_id || e.name,
    feedback: feedback[i] || '',
  }}));
  const blob = new Blob([JSON.stringify({{reviews, status: 'complete'}}, null, 2)], {{type: 'application/json'}});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'feedback.json'; a.click();
  URL.revokeObjectURL(url);
}}

function renderBenchmark() {{
  if (!benchmark || !benchmark.run_summary) {{
    document.getElementById('benchmark-content').innerHTML = '<p>No benchmark data available.</p>';
    return;
  }}
  const summary = benchmark.run_summary;
  const configs = Object.keys(summary).filter(k => k !== 'delta');
  let html = '<h2>Benchmark Summary</h2><table><tr><th>Metric</th>';
  configs.forEach(c => html += '<th>' + c.replace(/_/g,' ').replace(/\\b\\w/g,l=>l.toUpperCase()) + '</th>');
  html += '<th>Delta</th></tr>';
  const metrics = ['pass_rate', 'time_seconds', 'tokens'];
  const labels = ['Pass Rate', 'Time', 'Tokens'];
  metrics.forEach((m, i) => {{
    html += '<tr><td>' + labels[i] + '</td>';
    configs.forEach(c => {{
      const s = summary[c][m] || {{}};
      const mean = (s.mean || 0);
      const stddev = (s.stddev || 0);
      if (m === 'pass_rate') html += '<td>' + (mean*100).toFixed(0) + '% ± ' + (stddev*100).toFixed(0) + '%</td>';
      else if (m === 'time_seconds') html += '<td>' + mean.toFixed(1) + 's ± ' + stddev.toFixed(1) + 's</td>';
      else html += '<td>' + mean.toFixed(0) + ' ± ' + stddev.toFixed(0) + '</td>';
    }});
    html += '<td>' + (summary.delta ? summary.delta[m] || '—' : '—') + '</td></tr>';
  }});
  html += '</table>';
  if (benchmark.notes && benchmark.notes.length) {{
    html += '<h3>Notes</h3><ul>';
    benchmark.notes.forEach(n => html += '<li>' + n + '</li>');
    html += '</ul>';
  }}
  document.getElementById('benchmark-content').innerHTML = html;
}}

renderEval(0);
renderBenchmark();
</script>
</body>
</html>"""


def load_eval_data(workspace_dir: Path, iteration: int = None) -> list[dict]:
    if iteration:
        search_dir = workspace_dir / f"iteration-{iteration}"
    else:
        iterations = sorted(workspace_dir.glob("iteration-*"))
        search_dir = iterations[-1] if iterations else workspace_dir

    evals = []
    for eval_dir in sorted(search_dir.glob("eval-*")):
        meta_path = eval_dir / "eval_metadata.json"
        meta = {}
        if meta_path.exists():
            try:
                meta = json.load(open(meta_path))
            except json.JSONDecodeError:
                pass

        eval_item = {
            "name": meta.get("eval_name", eval_dir.name),
            "prompt": meta.get("prompt", ""),
            "run_id": eval_dir.name,
        }

        for config in ["with_skill", "without_skill", "old_skill"]:
            config_dir = eval_dir / config
            if config_dir.exists():
                grading_path = config_dir / "grading.json"
                if grading_path.exists():
                    try:
                        grading = json.load(open(grading_path))
                        eval_item["pass_rate"] = grading.get("summary", {}).get("pass_rate", 0.0)
                        eval_item["expectations"] = grading.get("expectations", [])
                    except json.JSONDecodeError:
                        pass

                outputs_dir = config_dir / "outputs"
                if outputs_dir.exists():
                    output_files = list(outputs_dir.glob("*"))
                    if output_files:
                        try:
                            eval_item["output"] = output_files[0].read_text()[:5000]
                        except:
                            eval_item["output"] = f"[{output_files[0].name}]"

        if meta.get("prompt"):
            evals.append(eval_item)

    return evals


def main():
    parser = argparse.ArgumentParser(description="Generate eval review HTML")
    parser.add_argument("workspace_dir", type=Path)
    parser.add_argument("--skill-name", default="skill")
    parser.add_argument("--benchmark", type=Path, default=None)
    parser.add_argument("--previous-workspace", type=Path, default=None)
    parser.add_argument("--static", type=Path, default=None, help="Write static HTML file instead of starting server")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    eval_data = load_eval_data(args.workspace_dir)

    if args.previous_workspace:
        prev_evals = load_eval_data(args.previous_workspace)
        for i, curr in enumerate(eval_data):
            for prev in prev_evals:
                if prev.get("name") == curr.get("name"):
                    if prev.get("output"):
                        curr["previous_output"] = prev["output"]
                    break

    benchmark_data = {}
    if args.benchmark and args.benchmark.exists():
        try:
            benchmark_data = json.load(open(args.benchmark))
        except json.JSONDecodeError:
            pass

    html = VIEWER_TEMPLATE.format(
        skill_name=args.skill_name,
        eval_data=json.dumps(eval_data),
        benchmark_data=json.dumps(benchmark_data),
    )

    if args.static:
        args.static.write_text(html)
        print(f"Static HTML written to: {args.static}")
    else:
        import http.server
        import webbrowser

        class Handler(http.server.SimpleHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode())

        with http.server.HTTPServer(("", args.port), Handler) as httpd:
            url = f"http://localhost:{args.port}"
            print(f"Eval viewer running at {url}")
            webbrowser.open(url)
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\nViewer stopped.")


if __name__ == "__main__":
    main()
