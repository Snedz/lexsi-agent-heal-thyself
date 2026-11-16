#!/usr/bin/env python3
"""Paired before/after statistics for Lexsi baseline runs.

Usage:
    python3 paired_stats.py BEFORE_RUN_DIR AFTER_RUN_DIR
    python3 paired_stats.py BEFORE_RUN_DIR AFTER_RUN_DIR \
        --before-label base --after-label healed

Each run directory must contain a scores.json as written by
baseline/run_baseline.py: a "per_item" list whose records carry
"id" (task id), "lane" (task lane) and "exact_match" (bool — the
per-item pass/fail; the headline tool_call_accuracy is its mean).

Prints a markdown report to stdout:
  - each run's success count / n and success rate with a Wilson 95% CI
  - the paired delta (after - before)
  - McNemar's exact test (two-sided binomial) on the discordant pairs,
    with the discordant counts stated
  - a per-lane before/after table

The two runs must cover exactly the same item ids (and the same lane
per id). If they differ, the script exits non-zero — a paired test on
silently-subset data would be meaningless.

Pure Python stdlib (no numpy/scipy): Wilson interval and the exact
binomial McNemar p-value are closed-form.
"""
import argparse
import json
import math
import os
import sys

Z_95 = 1.959963984540054  # standard-normal 97.5th percentile


def fail(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def load_run(run_dir):
    path = os.path.join(run_dir, "scores.json")
    if not os.path.isfile(path):
        fail(f"{path} not found — not a baseline run directory")
    with open(path) as f:
        data = json.load(f)
    per_item = data.get("per_item")
    if not isinstance(per_item, list) or not per_item:
        fail(f"{path}: missing or empty 'per_item' list")
    results = {}
    for r in per_item:
        for field in ("id", "lane", "exact_match"):
            if field not in r:
                fail(f"{path}: per-item record missing field {field!r}: {r!r}")
        if r["id"] in results:
            fail(f"{path}: duplicate item id {r['id']!r}")
        results[r["id"]] = {"lane": r["lane"], "pass": bool(r["exact_match"])}
    run_meta = data.get("run", {})
    return results, run_meta


def wilson_ci(successes, n, z=Z_95):
    """Wilson score interval for a binomial proportion."""
    if n == 0:
        return 0.0, 0.0, 0.0
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return p, max(0.0, centre - half), min(1.0, centre + half)


def mcnemar_exact(b, c):
    """Two-sided exact McNemar p-value: binomial test on the b+c
    discordant pairs under p = 0.5. Returns None when b + c == 0
    (test undefined — no discordant evidence either way)."""
    n = b + c
    if n == 0:
        return None
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) * (0.5 ** n)
    return min(1.0, 2.0 * tail)


def pct(x):
    return f"{100 * x:.1f}%"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("before_dir", help="run directory of the 'before' run")
    ap.add_argument("after_dir", help="run directory of the 'after' run")
    ap.add_argument("--before-label", default=None)
    ap.add_argument("--after-label", default=None)
    args = ap.parse_args()

    before, bmeta = load_run(args.before_dir)
    after, ameta = load_run(args.after_dir)

    b_ids, a_ids = set(before), set(after)
    if b_ids != a_ids:
        only_b = sorted(b_ids - a_ids)
        only_a = sorted(a_ids - b_ids)
        fail("item sets differ between runs — refusing to subset silently. "
             f"only in before ({len(only_b)}): {only_b[:5]}; "
             f"only in after ({len(only_a)}): {only_a[:5]}")
    for item_id in sorted(b_ids):
        if before[item_id]["lane"] != after[item_id]["lane"]:
            fail(f"item {item_id!r} has lane {before[item_id]['lane']!r} in "
                 f"before but {after[item_id]['lane']!r} in after — "
                 "the runs are not over the same task set")

    ids = sorted(b_ids)
    n = len(ids)

    def label(meta, given, fallback):
        if given:
            return given
        parts = [meta.get("model"), meta.get("backend")]
        text = " / ".join(str(p) for p in parts if p)
        return text or fallback

    b_label = label(bmeta, args.before_label, "before")
    a_label = label(ameta, args.after_label, "after")

    b_pass = {i: before[i]["pass"] for i in ids}
    a_pass = {i: after[i]["pass"] for i in ids}
    b_succ = sum(b_pass.values())
    a_succ = sum(a_pass.values())
    b_rate, b_lo, b_hi = wilson_ci(b_succ, n)
    a_rate, a_lo, a_hi = wilson_ci(a_succ, n)

    # paired cells: b = fail->pass (gained), c = pass->fail (lost)
    gained = sum(1 for i in ids if not b_pass[i] and a_pass[i])
    lost = sum(1 for i in ids if b_pass[i] and not a_pass[i])
    both_pass = sum(1 for i in ids if b_pass[i] and a_pass[i])
    both_fail = n - gained - lost - both_pass
    p_val = mcnemar_exact(gained, lost)
    delta = a_rate - b_rate

    lanes = sorted({before[i]["lane"] for i in ids})

    out = []
    out.append("# Paired before/after statistics — Lexsi triage baseline")
    out.append("")
    out.append(f"- Before: **{b_label}** — `{args.before_dir}`"
               + (f" (fingerprint `{bmeta['fingerprint'][:12]}…`)"
                  if bmeta.get("fingerprint") else ""))
    out.append(f"- After: **{a_label}** — `{args.after_dir}`"
               + (f" (fingerprint `{ameta['fingerprint'][:12]}…`)"
                  if ameta.get("fingerprint") else ""))
    out.append(f"- Success = per-item `exact_match` (exact tool sequence, "
               f"all arguments valid), aligned by item id; n = {n} items.")
    out.append("")
    out.append("## Headline (exact-match success)")
    out.append("")
    out.append("| Run | Successes | Rate | Wilson 95% CI |")
    out.append("|---|---:|---:|---:|")
    out.append(f"| Before ({b_label}) | {b_succ}/{n} | {pct(b_rate)} | "
               f"[{pct(b_lo)}, {pct(b_hi)}] |")
    out.append(f"| After ({a_label}) | {a_succ}/{n} | {pct(a_rate)} | "
               f"[{pct(a_lo)}, {pct(a_hi)}] |")
    out.append(f"| **Paired delta (after − before)** | {a_succ - b_succ:+d} "
               f"items | **{100 * delta:+.1f} pp** | — |")
    out.append("")
    out.append("## McNemar exact test (paired, two-sided)")
    out.append("")
    out.append(f"- Discordant pairs: **{gained + lost}** "
               f"(fail → pass: {gained}; pass → fail: {lost})")
    out.append(f"- Concordant pairs: {both_pass} pass in both runs, "
               f"{both_fail} fail in both runs")
    if p_val is None:
        out.append("- **p = 1.0 by convention — the exact test is undefined "
                   "with zero discordant pairs** (no item changed outcome, "
                   "so there is no evidence of any paired difference).")
    else:
        out.append(f"- **McNemar exact p = {p_val:.4f}** "
                   f"(binomial test on the {gained + lost} discordant pairs)")
    out.append("")
    out.append("## Per-lane before/after")
    out.append("")
    out.append("| Lane | n | Before | After | Delta |")
    out.append("|---|---:|---:|---:|---:|")
    for lane in lanes:
        lane_ids = [i for i in ids if before[i]["lane"] == lane]
        ln = len(lane_ids)
        lb = sum(b_pass[i] for i in lane_ids)
        la = sum(a_pass[i] for i in lane_ids)
        out.append(f"| {lane} | {ln} | {lb}/{ln} ({pct(lb / ln)}) | "
                   f"{la}/{ln} ({pct(la / ln)}) | "
                   f"{100 * (la - lb) / ln:+.1f} pp |")
    out.append(f"| **All lanes** | {n} | {b_succ}/{n} ({pct(b_rate)}) | "
               f"{a_succ}/{n} ({pct(a_rate)}) | {100 * delta:+.1f} pp |")
    out.append("")
    print("\n".join(out))


if __name__ == "__main__":
    main()
