#!/usr/bin/env python3
"""Formalize a baseline run into the AuditKIT audit record (concept #1).

Architecture (per auditkit_config.yaml): baseline/scoring.py is the primary
scorer; AuditKIT is the record/comparison layer. This script wraps a
completed run (scores.json + predictions.jsonl + traces.jsonl) into an
AuditKIT AgentEvalResult:

  - episodes: traces converted with agent_eval.episode_from_openai_messages,
    with the assistant's raw JSON output also attached as OpenAI `tool_calls`
    (AuditKIT's normalize_call wants {name|function.name, arguments}; our
    harness emits {tool, arguments} — the enrichment bridges the two).
  - cases: one AgentCase per item — allowed_tools = the harness vocabulary,
    reference_turns = gold sequence in {name, arguments} form, outcome = a
    CustomPredicate oracle that defers to scoring.score_item (exact match),
    metadata carries our failure_class.
  - scorers: tool_call_f1 + tool_call_validity (AuditKIT's own metrics,
    recorded alongside ours for the shared record).

Verified live 2026-11-13 against auditkit 1.1.11 (pip) on the echo-smoke
run: 104/104 cases completed, verdicts match scoring.py exactly.

Outputs into --out: auditkit-record.json (full result) + auditkit-summary.md.
The before/after comparison for the submission is ak.compare on two records
produced by this script (base vs fine-tuned run).

Usage:
  pip install auditkit   # 1.1.11 verified
  python3 formalize_auditkit.py --run results/aya8b-base \
      --tasks ../triage-tasks.jsonl --out results/aya8b-base/auditkit
"""
import argparse
import dataclasses
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scoring import parse_prediction, score_item      # noqa: E402
from toolspec import TOOL_NAMES                        # noqa: E402

TASKS_BY_ID = {}


def enrich(messages):
    out = []
    for msg in messages:
        msg = dict(msg)
        if msg.get("role") == "assistant":
            calls, _err = parse_prediction(msg.get("content", ""))
            if calls:
                msg["tool_calls"] = [
                    {"function": {"name": c["tool"],
                                   "arguments": json.dumps(c["arguments"],
                                                           ensure_ascii=False)}}
                    for c in calls]
        out.append(msg)
    return out


def verdict(ep, case):
    import auditkit.agent_eval as m
    item = TASKS_BY_ID[case.id]
    res, _calls = score_item(item, ep.final_answer or "")
    return m.SUCCESS if res["exact_match"] else m.FAILURE


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--tasks", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "triage-tasks.jsonl"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    import auditkit.agent_eval as m

    with open(args.tasks) as f:
        TASKS_BY_ID.update({json.loads(l)["id"]: json.loads(l)
                            for l in f if l.strip()})
    with open(os.path.join(args.run, "scores.json")) as f:
        scores = json.load(f)
    per_item = {r["id"]: r for r in scores["per_item"]}
    with open(os.path.join(args.run, "traces.jsonl")) as f:
        traces = [json.loads(l) for l in f if l.strip()]

    episodes, cases = [], []
    for t in traces:
        item = TASKS_BY_ID[t["id"]]
        ep = m.episode_from_openai_messages(
            enrich(t["messages"]), case_id=t["id"], source_id=t["id"])
        episodes.append(ep)
        ref = [{"name": c["tool"], "arguments": c["arguments"]}
               for c in item["gold"]["tool_sequence"]]
        cases.append(m.AgentCase(
            id=t["id"], task=t["messages"][1]["content"],
            category=item["lane"], allowed_tools=list(TOOL_NAMES),
            reference_turns=ref,
            outcome=m.CustomPredicate(verdict, version="1",
                                      name="triage-exact-match"),
            metadata={"failure_class": per_item[t["id"]]["failure_class"],
                      "harness_fingerprint": scores["run"].get("fingerprint")}))

    name = f"lexsi-triage-{scores['run'].get('model', 'run')}"
    spec = m.AgentEvalSpec(cases=cases, episodes=episodes, mode="recorded",
                           name=name,
                           scorers=["tool_call_f1", "tool_call_validity"])
    res = m.AgentEvalRunner().run(spec)

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "auditkit-record.json"), "w") as f:
        json.dump(dataclasses.asdict(res), f, indent=2, default=str)

    verdicts, f1s, valids = [], [], []
    for r in res.rows:
        oc = r.outcome if isinstance(r.outcome, dict) else {}
        verdicts.append(oc.get("verdict", r.status))
        diag = r.diagnostics if isinstance(r.diagnostics, dict) else {}
        if "tool_call_f1" in diag:
            f1s.append(diag["tool_call_f1"].get("value"))
        if "tool_call_validity" in diag:
            valids.append(diag["tool_call_validity"].get("value"))
    agg_verdict, _why = m.aggregate_verdict([str(v) for v in verdicts])
    mean = lambda xs: round(sum(x for x in xs if x is not None) / len(xs), 4) if xs else None  # noqa: E731
    lines = [f"# AuditKIT record — {name}",
             f"\nSource run fingerprint: `{scores['run'].get('fingerprint', '?')}`"
             f" · model `{scores['run'].get('model')}` · cases: {len(res.rows)}",
             f"\nAggregate verdict: **{agg_verdict}** · verdict counts: "
             f"{dict(Counter(str(v) for v in verdicts))}",
             f"\nAuditKIT metrics (mean per case): tool_call_f1 = {mean(f1s)}"
             f" · tool_call_validity = {mean(valids)}",
             f"\nHarness metrics (scoring.py, the scorer of record): "
             f"tool_call_accuracy = {scores['metrics'].get('tool_call_accuracy')}"
             f" · failure classes: "
             f"{json.dumps(scores['metrics'].get('failure_class_distribution', {}))}"]
    with open(os.path.join(args.out, "auditkit-summary.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {args.out}/auditkit-record.json + auditkit-summary.md")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
