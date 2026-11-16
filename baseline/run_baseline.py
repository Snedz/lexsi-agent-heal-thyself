#!/usr/bin/env python3
"""Run the triage baseline over ../triage-tasks.jsonl and score it.

Usage (Monday GPU run, Colab):
    python run_baseline.py --backend transformers \
        --model CohereLabs/aya-expanse-8b --out results/aya-expanse-8b-base

Usage (T4 route — Colab free / Kaggle: FP16 needs ~17.9 GB and does not
fit a 16 GB T4, so load in 8-bit; the precision is fingerprinted):
    python run_baseline.py --backend transformers --load-in-8bit \
        --model CohereLabs/aya-expanse-8b --out results/aya-expanse-8b-base

Usage (plumbing smoke, anywhere, no GPU):
    python run_baseline.py --backend echo --out results/echo-smoke

Writes into --out:
    scores.json       aggregate metrics + per-item scores (fingerprinted)
    predictions.jsonl raw model output per item
    traces.jsonl      OpenAI-messages episode traces (AuditKIT-ready)
    summary.md        human-readable headline table

The fingerprint mirrors AuditKIT's RunResult idea (sha256 over model +
tasks + backend + load precision) so before/after runs are comparable by
construction — an 8-bit T4 run and an FP16 A100 run of the same checkpoint
must never share a fingerprint.
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scoring import aggregate, score_item          # noqa: E402
from triage_agent import EchoBackend, TransformersBackend, run_items  # noqa: E402


def load_tasks(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "triage-tasks.jsonl"))
    ap.add_argument("--backend", choices=["echo", "transformers"], default="echo")
    ap.add_argument("--model", default=None)
    ap.add_argument("--load-in-8bit", action="store_true",
                    help="load the checkpoint in 8-bit (bitsandbytes) — "
                         "REQUIRED on 16 GB T4 routes: aya-expanse-8b at "
                         "FP16 needs ~17.9 GB and does not fit")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    items = load_tasks(args.tasks)
    tasks_sha = hashlib.sha256(open(args.tasks, "rb").read()).hexdigest()

    if args.backend == "echo":
        backend = EchoBackend()
        model_id = "echo"
        watermark = "PLUMBING SMOKE — NOT BASELINE NUMBERS"
    else:
        backend = TransformersBackend(model_id=args.model,
                                      load_in_8bit=args.load_in_8bit) \
            if args.model else TransformersBackend(
                load_in_8bit=args.load_in_8bit)
        model_id = backend.model_id
        watermark = None
    load_precision = backend.load_precision

    predictions, traces = run_items(items, backend, limit=args.limit)
    by_id = {p["id"]: p for p in predictions}
    per_item = []
    for item in items[:args.limit] if args.limit else items:
        res, _calls = score_item(item, by_id[item["id"]]["raw_output"])
        per_item.append(res)

    agg = aggregate(per_item)
    fingerprint = hashlib.sha256(
        f"{model_id}|{args.backend}|{tasks_sha}|{load_precision}"
        .encode()).hexdigest()

    os.makedirs(args.out, exist_ok=True)
    payload = {
        "run": {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "backend": args.backend, "model": model_id,
            "load_precision": load_precision,
            "tasks_file_sha256": tasks_sha, "fingerprint": fingerprint,
            "watermark": watermark,
        },
        "metrics": agg,
        "per_item": per_item,
    }
    with open(os.path.join(args.out, "scores.json"), "w") as f:
        json.dump(payload, f, indent=2)
    with open(os.path.join(args.out, "predictions.jsonl"), "w") as f:
        for p in predictions:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    with open(os.path.join(args.out, "traces.jsonl"), "w") as f:
        for t in traces:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    m = agg
    lines = [f"# Baseline run — {model_id} via {args.backend}",
             f"\nFingerprint: `{fingerprint}`  ·  n = {m.get('n', 0)} items"
             f"  ·  load precision: {load_precision}"
             f"  ·  {payload['run']['timestamp_utc']}"]
    if watermark:
        lines.append(f"\n> **{watermark}**")
    lines += ["",
              "| Metric | Value |",
              "|---|---|",
              f"| Tool-call accuracy (exact sequence) | {m.get('tool_call_accuracy')} |",
              f"| Step accuracy | {m.get('step_accuracy')} |",
              f"| Tool-set match rate | {m.get('tool_set_match_rate')} |",
              f"| Argument schema-valid rate | {m.get('argument_schema_valid_rate')} |",
              f"| Argument value-valid rate | {m.get('argument_value_valid_rate')} |",
              f"| Ordering error rate | {m.get('ordering_error_rate')} |",
              f"| Steps/task (pred vs gold) | {m.get('steps_per_task_pred')} vs {m.get('steps_per_task_gold')} |",
              f"| Unsafe-action items | {m.get('unsafe_action_items')} |",
              "",
              "Failure classes: " + json.dumps(m.get("failure_class_distribution", {})),
              "",
              "Per-lane accuracy: " + json.dumps(m.get("by_lane_accuracy", {}))]
    with open(os.path.join(args.out, "summary.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {args.out}/scores.json (fingerprint {fingerprint[:12]}…)")
    print(json.dumps(agg, indent=2))


if __name__ == "__main__":
    main()
