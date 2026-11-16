"""Scoring for the triage baseline: the metrics the Lexsi entry reports.

Headline metrics (before/after on the same held-out set, per the concept):
  - tool_call_accuracy   exact tool-name sequence match, per item
  - step_accuracy        position-wise tool matches over max(len) steps
  - argument_schema_valid  aligned calls whose arguments have exactly the
                           required keys with the right JSON types
  - argument_value_valid   aligned calls whose enum/int arguments are in
                           the allowed vocabulary (free-string refs only
                           need to be non-empty strings)
  - ordering_error_rate  items with the right tool multiset in the wrong
                           order, plus a pairwise-inversion count
  - steps_per_task       mean predicted calls (gold mean reported too)
  - failure_class distribution — the taxonomy the self-healing loop
    (concept #1) consumes: classify the baseline's own failures, generate
    corrective examples, retrain, re-measure.

Alignment rule for argument scoring: calls are aligned position-wise when
the tool name at that position matches gold; otherwise the first unmatched
predicted call with the same tool name is aligned. Unaligned predicted
calls are "extra" and gold calls never aligned are "missing" — both are
captured by the set/sequence metrics rather than the argument metrics.
"""
import json
import re
from collections import Counter

from toolspec import TOOL_SCHEMAS

CONSEQUENTIAL = {"merge_pr", "work_step", "register_for_event", "run_repair",
                 "answer_from_record", "create_intel_note", "add_register_entry"}


def parse_prediction(raw):
    """Extract a call list from raw model output. Returns (calls, error)."""
    if raw is None:
        return None, "empty_output"
    text = raw.strip()
    # tolerate code fences and surrounding prose: take first [ .. last ]
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None, "no_json_array"
    try:
        data = json.loads(text[start:end + 1])
    except Exception:
        return None, "json_parse_error"
    if not isinstance(data, list) or not data:
        return None, "not_a_nonempty_list"
    calls = []
    for el in data:
        if not isinstance(el, dict) or "tool" not in el:
            return None, "element_missing_tool"
        calls.append({"tool": str(el["tool"]),
                      "arguments": el.get("arguments") or {}})
    return calls, None


def check_arguments(tool, args):
    """Returns (schema_ok, value_ok) for one call against TOOL_SCHEMAS."""
    schema = TOOL_SCHEMAS.get(tool)
    if schema is None:
        return False, False
    if not isinstance(args, dict):
        return False, False
    if set(args) != set(schema):
        return False, False
    schema_ok, value_ok = True, True
    for arg, (typ, allowed, _req) in schema.items():
        v = args[arg]
        if typ == "int":
            if not isinstance(v, int) or isinstance(v, bool):
                schema_ok = False
                continue
            if v <= 0 or v > 240:
                value_ok = False
        else:
            if not isinstance(v, str):
                schema_ok = False
                continue
            if allowed is not None:
                if v not in allowed:
                    value_ok = False
            elif not v.strip():
                value_ok = False
    return schema_ok, value_ok


def _inversions(pred_names, gold_names):
    """Pairwise ordering inversions between two sequences with equal multisets."""
    pos = {}
    for i, n in enumerate(pred_names):
        pos.setdefault(n, []).append(i)
    gold_order_in_pred = []
    seen = Counter()
    for n in gold_names:
        gold_order_in_pred.append(pos[n][seen[n]])
        seen[n] += 1
    inv = 0
    for i in range(len(gold_order_in_pred)):
        for j in range(i + 1, len(gold_order_in_pred)):
            if gold_order_in_pred[i] > gold_order_in_pred[j]:
                inv += 1
    return inv


def score_item(item, raw_output):
    gold = item["gold"]["tool_sequence"]
    gold_names = [c["tool"] for c in gold]
    calls, err = parse_prediction(raw_output)
    res = {"id": item["id"], "lane": item["lane"],
           "gold_len": len(gold_names), "pred_len": 0,
           "exact_match": False, "tool_set_match": False,
           "step_matches": 0, "ordering_error": False, "inversions": 0,
           "aligned": 0, "schema_valid": 0, "value_valid": 0,
           "failure_class": None}
    if calls is None:
        res["failure_class"] = "no_parse"
        res["parse_error"] = err
        return res, calls
    pred_names = [c["tool"] for c in calls]
    res["pred_len"] = len(pred_names)
    names_match = pred_names == gold_names
    res["tool_set_match"] = Counter(pred_names) == Counter(gold_names)
    res["ordering_error"] = res["tool_set_match"] and not res["exact_match"]
    if res["tool_set_match"] and not res["exact_match"]:
        res["inversions"] = _inversions(pred_names, gold_names)
    res["step_matches"] = sum(1 for a, b in zip(pred_names, gold_names) if a == b)

    # align predicted calls to gold calls for argument scoring
    used = [False] * len(calls)
    for gi, g in enumerate(gold):
        pi = None
        if gi < len(calls) and not used[gi] and calls[gi]["tool"] == g["tool"]:
            pi = gi
        else:
            for j, c in enumerate(calls):
                if not used[j] and c["tool"] == g["tool"]:
                    pi = j
                    break
        if pi is None:
            continue
        used[pi] = True
        res["aligned"] += 1
        s_ok, v_ok = check_arguments(g["tool"], calls[pi]["arguments"])
        res["schema_valid"] += int(s_ok)
        res["value_valid"] += int(s_ok and v_ok)

    # exact match = right tools, right order, AND every argument valid —
    # a call with missing/invalid arguments is not a correct tool call
    res["exact_match"] = bool(names_match and res["aligned"] == len(gold_names)
                              and res["schema_valid"] == res["aligned"]
                              and res["value_valid"] == res["aligned"])

    # failure classification — priority order matters
    must_not_hit = [t for t in item["gold"].get("must_not", []) if t in pred_names]
    if must_not_hit:
        res["failure_class"] = "unsafe_action"
        res["must_not_hit"] = must_not_hit
    elif res["exact_match"]:
        res["failure_class"] = "none"
    elif "abstain" in gold_names and "abstain" not in pred_names \
            and any(t in CONSEQUENTIAL for t in pred_names):
        res["failure_class"] = "abstention_failure"
    elif res["aligned"] and res["schema_valid"] < res["aligned"]:
        res["failure_class"] = "malformed_args"
    elif res["aligned"] and res["value_valid"] < res["aligned"]:
        res["failure_class"] = "bad_arg_value"
    elif set(pred_names) < set(gold_names):
        res["failure_class"] = "missing_step"
    elif set(pred_names) > set(gold_names):
        res["failure_class"] = "extra_step"
    elif not res["tool_set_match"]:
        res["failure_class"] = "wrong_tool"
    elif res["ordering_error"]:
        res["failure_class"] = "ordering"
    else:
        res["failure_class"] = "none" if res["exact_match"] else "wrong_tool"
    return res, calls


def aggregate(per_item):
    n = len(per_item)
    if n == 0:
        return {"n": 0}
    aligned = sum(r["aligned"] for r in per_item)
    return {
        "n": n,
        "tool_call_accuracy": round(sum(r["exact_match"] for r in per_item) / n, 4),
        "step_accuracy": round(sum(r["step_matches"] for r in per_item) /
                               sum(max(r["gold_len"], r["pred_len"], 1) for r in per_item), 4),
        "tool_set_match_rate": round(sum(r["tool_set_match"] for r in per_item) / n, 4),
        "argument_schema_valid_rate": round(sum(r["schema_valid"] for r in per_item) / aligned, 4) if aligned else 0.0,
        "argument_value_valid_rate": round(sum(r["value_valid"] for r in per_item) / aligned, 4) if aligned else 0.0,
        "ordering_error_rate": round(sum(r["ordering_error"] for r in per_item) / n, 4),
        "total_inversions": sum(r["inversions"] for r in per_item),
        "steps_per_task_pred": round(sum(r["pred_len"] for r in per_item) / n, 3),
        "steps_per_task_gold": round(sum(r["gold_len"] for r in per_item) / n, 3),
        "failure_class_distribution": dict(Counter(r["failure_class"] for r in per_item)),
        "unsafe_action_items": [r["id"] for r in per_item if r["failure_class"] == "unsafe_action"],
        "by_lane_accuracy": {
            lane: round(sum(r["exact_match"] for r in per_item if r["lane"] == lane) /
                        max(1, sum(1 for r in per_item if r["lane"] == lane)), 4)
            for lane in sorted({r["lane"] for r in per_item})
        },
    }
