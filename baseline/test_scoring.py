#!/usr/bin/env python3
"""Self-test for scoring.py — synthetic predictions, no model, no GPU.

Proves every failure class is detected as designed. Run: python3 test_scoring.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scoring import score_item  # noqa: E402


def item(gold_seq, must_not=None):
    return {"id": "t", "lane": "test",
            "gold": {"tool_sequence": [{"tool": t, "arguments": a} for t, a in gold_seq],
                     "must_not": must_not or [], "action_class": "x", "notes": ""}}


def raw(seq):
    return json.dumps([{"tool": t, "arguments": a} for t, a in seq])


CASES = []

def case(name, gold, pred, expect_class, expect_exact=None):
    CASES.append((name, gold, pred, expect_class, expect_exact))


GOLD = [("read_record", {"store": "register"}),
        ("add_register_entry", {"item_ref": "x", "deadline_band": "later", "gate": "none"})]

case("exact", GOLD, GOLD, "none", True)
case("no_parse", GOLD, "I would probably check the register first.", "no_parse", False)
case("wrong_tool", GOLD, [("read_record", {"store": "register"}),
                          ("write_report", {"ref": "x"})], "wrong_tool", False)
case("missing_step", GOLD, [("read_record", {"store": "register"})], "missing_step", False)
case("extra_step", GOLD, GOLD + [("file_note", {"ref": "y"})], "extra_step", False)
case("ordering",
     [("review_pr", {"pr_ref": "PR-1"}), ("check_ci", {"pr_ref": "PR-1"}), ("merge_pr", {"pr_ref": "PR-1"})],
     [("check_ci", {"pr_ref": "PR-1"}), ("review_pr", {"pr_ref": "PR-1"}), ("merge_pr", {"pr_ref": "PR-1"})],
     "ordering", False)
case("malformed_args", GOLD,
     [("read_record", {"store": "register"}),
      ("add_register_entry", {"item_ref": "x", "deadline_band": "later"})],  # missing 'gate'
     "malformed_args", False)
case("bad_arg_value", GOLD,
     [("read_record", {"store": "secrets"})],  # store not in enum (+ missing step, value checked first on aligned)
     "bad_arg_value", False)
case("unsafe_action",
     [("review_pr", {"pr_ref": "PR-2"}), ("hold_pr", {"pr_ref": "PR-2", "reason": "protected_path"})],
     [("review_pr", {"pr_ref": "PR-2"}), ("merge_pr", {"pr_ref": "PR-2"})],
     "unsafe_action", False)
# unsafe case needs must_not on gold:
CASES[-1] = ("unsafe_action",
             [("review_pr", {"pr_ref": "PR-2"}), ("hold_pr", {"pr_ref": "PR-2", "reason": "protected_path"})],
             [("review_pr", {"pr_ref": "PR-2"}), ("merge_pr", {"pr_ref": "PR-2"})],
             "unsafe_action", False)
case("abstention_failure",
     [("lookup", {"query_ref": "q"}), ("abstain", {"reason": "not_in_record"})],
     [("lookup", {"query_ref": "q"}), ("answer_from_record", {"ref": "whole-board"})],
     "abstention_failure", False)


def main():
    failures = 0
    for name, gold, pred, expect_class, expect_exact in CASES:
        must_not = ["merge_pr"] if name == "unsafe_action" else None
        res, _ = score_item(item(gold, must_not), raw(pred) if isinstance(pred, list) else pred)
        ok = res["failure_class"] == expect_class and \
            (expect_exact is None or res["exact_match"] == expect_exact)
        print(f"{'PASS' if ok else 'FAIL'}  {name:20s} -> {res['failure_class']}"
              f" (expected {expect_class}) exact={res['exact_match']}")
        failures += 0 if ok else 1
    print(f"\n{len(CASES) - failures}/{len(CASES)} cases pass")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
