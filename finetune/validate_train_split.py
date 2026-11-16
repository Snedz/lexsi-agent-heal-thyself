#!/usr/bin/env python3
"""Validate finetune/train-split.jsonl against the heal loop's hard rules.

Checks:
  * file parses row by row as JSON, in the triage-tasks.jsonl shape
  * ids unique, all prefixed "train-", disjoint from held-out ids
  * provenance == "train" on every row
  * no item text identical to any held-out item (exact and normalised)
  * every gold call's arguments pass scoring.check_arguments (schema + values)
  * gold sequences non-empty; must_not tools are in the vocabulary
  * train_finetune.py's contamination guard (load_triples) passes on the file
  * prints counts by target class and by lane

Exits non-zero on the first failing check group, listing every failure.
"""
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEXSI = HERE.parent
sys.path.insert(0, str(LEXSI / "baseline"))

import scoring  # noqa: E402
import toolspec  # noqa: E402
import train_finetune  # noqa: E402  (finetune/ is on sys.path as script dir)

TRAIN = HERE / "train-split.jsonl"
HELD_OUT = LEXSI / "triage-tasks.jsonl"


def norm(text):
    return " ".join(str(text).lower().split())


def main():
    failures = []

    rows = []
    for lineno, line in enumerate(TRAIN.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception as exc:
            failures.append(f"line {lineno}: JSON parse error: {exc}")
    print(f"rows parsed: {len(rows)}")

    held = [json.loads(l) for l in HELD_OUT.read_text(encoding="utf-8").splitlines() if l.strip()]
    held_ids = {r["id"] for r in held}
    held_items = {r["item"] for r in held}
    held_items_norm = {norm(r["item"]) for r in held}

    ids = [r.get("id") for r in rows]
    if len(ids) != len(set(ids)):
        dupes = [i for i, n in Counter(ids).items() if n > 1]
        failures.append(f"duplicate train ids: {dupes}")
    bad_prefix = [i for i in ids if not str(i).startswith("train-")]
    if bad_prefix:
        failures.append(f"ids missing train- prefix: {bad_prefix[:5]}")
    overlap = set(ids) & held_ids
    if overlap:
        failures.append(f"ids overlapping held-out set: {sorted(overlap)}")

    bad_prov = [r["id"] for r in rows if r.get("provenance") != "train"]
    if bad_prov:
        failures.append(f"rows whose provenance is not 'train': {bad_prov[:5]}")

    exact_hits = [r["id"] for r in rows if r["item"] in held_items]
    if exact_hits:
        failures.append(f"item text identical to a held-out item: {exact_hits}")
    norm_hits = [r["id"] for r in rows if norm(r["item"]) in held_items_norm]
    if norm_hits:
        failures.append(f"item text identical to a held-out item after normalisation: {norm_hits}")
    train_items = [r["item"] for r in rows]
    if len(train_items) != len(set(train_items)):
        failures.append("duplicate item texts inside the train split")

    bad_calls = []
    for r in rows:
        seq = r["gold"]["tool_sequence"]
        if not seq:
            failures.append(f"{r['id']}: empty gold tool_sequence")
        for call in seq:
            tool, args = call["tool"], call["arguments"]
            if tool not in toolspec.TOOL_SCHEMAS:
                bad_calls.append(f"{r['id']}: unknown tool {tool}")
                continue
            s_ok, v_ok = scoring.check_arguments(tool, args)
            if not (s_ok and v_ok):
                bad_calls.append(f"{r['id']}: {tool} args schema_ok={s_ok} value_ok={v_ok}: {args}")
        for t in r["gold"].get("must_not", []):
            if t not in toolspec.TOOL_SCHEMAS:
                bad_calls.append(f"{r['id']}: must_not tool not in vocabulary: {t}")
        # gold must never emit a must_not tool itself
        gold_tools = {c["tool"] for c in seq}
        clash = gold_tools & set(r["gold"].get("must_not", []))
        if clash:
            bad_calls.append(f"{r['id']}: gold emits its own must_not tool(s): {sorted(clash)}")
    if bad_calls:
        failures.extend(bad_calls[:20])
        failures.append(f"argument validation failures: {len(bad_calls)} total")

    # The real contamination guard from train_finetune.py.
    try:
        triples = train_finetune.load_triples(TRAIN)
        if len(triples) != len(rows):
            failures.append(
                f"contamination guard loaded {len(triples)} triples for {len(rows)} rows")
        else:
            print(f"contamination guard (train_finetune.load_triples): PASS "
                  f"({len(triples)} triples, no held-out ids, no held-out provenance)")
    except SystemExit as exc:
        failures.append(f"contamination guard REFUSED the file: {exc}")

    print("counts by target class:", dict(Counter(r["target_class"] for r in rows)))
    print("counts by lane:", dict(Counter(r["lane"] for r in rows)))
    print("counts by difficulty:", dict(Counter(r["difficulty"] for r in rows)))

    if failures:
        print("\nVALIDATION FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nALL VALIDATION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
