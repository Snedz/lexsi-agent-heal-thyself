#!/usr/bin/env python3
"""Lexsi entry "Agent, heal thyself" — fine-tune step (AgentTune distillation / SFT).

Trains the baseline Cohere checkpoint on the FRESH train split (never the
held-out set) using AgentTune's one-call distillation trainer
(`create_distill_trainer`, agenttune==1.1.5), which wraps a TRL SFTTrainer
with LoRA by default. GRPO and the other RL algorithms AgentTune exposes are
deliberately NOT used: on a free T4 they do not fit the session budget, and
the contest claim is the failure-driven repair loop, not an RL method.

QLoRA load (fixed Nov 14): the pinned checkpoint is the 8B
`CohereLabs/aya-expanse-8b`. Loaded in bf16 with no quantization it needs
~16 GB for weights alone — plus LoRA, gradients and optimizer states it
does NOT fit a 16 GB T4. The base is therefore loaded in 4-bit NF4
(bitsandbytes, double quantization, bf16 compute) by default and the
LoRA adapters train on top of the frozen quantized base — the QLoRA
recipe this header always claimed. `--no-load-in-4bit` restores the
full-precision load for larger GPUs; `--load-in-8bit` is the middle
option. If the installed AgentTune trainer cannot accept the
quantization config, the run aborts loudly rather than silently
training in bf16 and OOMing mid-session.

Inputs (either shape, one JSONL):
  * task records — the triage-tasks.jsonl shape
    {id, lane, difficulty, item, context, gold: {tool_sequence, ...}}
    rendered through baseline/toolspec.py exactly as the harness renders them.
  * message records — the build_correctives.py shape
    {messages: [system, user, assistant], provenance, ...}

Contamination guard (hard rule of this entry): any record whose provenance
mentions the held-out set aborts the run. Held-out-derived correctives are
diagnostics and authoring templates only; the publishable fine-tune trains on
freshly authored items and is re-measured on the untouched held-out set.

After training, the LoRA adapter is merged into the base weights and saved
next to the adapter dir as `<out>-merged`, so the identical baseline harness
can score it with no code changes:

    python3 baseline/run_baseline.py --model <out>-merged \
        --tasks triage-tasks.jsonl --out runs/after-<stamp>

Usage (GPU machine / Colab — T4: default 4-bit QLoRA load):
    pip install "agenttune==1.1.5" bitsandbytes
    python3 finetune/train_finetune.py \
        --train finetune/train-split.jsonl \
        --model CohereLabs/aya-expanse-8b \
        --out runs/adapter-v1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEXSI = HERE.parent
sys.path.insert(0, str(LEXSI / "baseline"))

import toolspec  # noqa: E402  (the harness's own prompt builder)


def gold_text(task: dict) -> str:
    return json.dumps(task["gold"]["tool_sequence"], ensure_ascii=False)


HELD_OUT = LEXSI / "triage-tasks.jsonl"


def held_out_ids() -> set[str]:
    if not HELD_OUT.exists():
        return set()
    ids = set()
    for line in HELD_OUT.read_text(encoding="utf-8").splitlines():
        if line.strip():
            ids.add(json.loads(line).get("id"))
    return ids


def load_triples(path: Path) -> list[tuple[str, str, str]]:
    """Normalize either input shape to (system, user, assistant) triples."""
    if path.resolve() == HELD_OUT.resolve():
        raise SystemExit(
            "REFUSING to train: the train file IS the held-out set "
            "(triage-tasks.jsonl). Author a fresh train split instead."
        )
    banned_ids = held_out_ids()
    triples: list[tuple[str, str, str]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("id") in banned_ids:
            raise SystemExit(
                f"REFUSING to train: {path}:{lineno} reuses held-out item id "
                f"{rec.get('id')!r}. Train items must be freshly authored, "
                "with fresh ids."
            )
        prov = str(rec.get("provenance", ""))
        if "held_out" in prov or "held-out" in prov:
            raise SystemExit(
                f"REFUSING to train: {path}:{lineno} is held-out-derived "
                f"(provenance={prov!r}). Held-out correctives are diagnostics "
                "only — author a fresh train split in the failing classes."
            )
        if "messages" in rec:
            msgs = {m["role"]: m["content"] for m in rec["messages"]}
            triples.append((msgs["system"], msgs["user"], msgs["assistant"]))
        elif "gold" in rec:
            triples.append(
                (
                    toolspec.build_system_prompt(),
                    toolspec.build_user_prompt(rec),
                    gold_text(rec),
                )
            )
        else:
            raise SystemExit(f"{path}:{lineno}: unrecognized record shape")
    return triples


DEFAULT_MODEL = "CohereLabs/aya-expanse-8b"  # pinned checkpoint of this entry


def load_precision_label(load_in_4bit: bool, load_in_8bit: bool) -> str:
    """Human/log label for the base-model load precision (no GPU imports)."""
    if load_in_4bit:
        return "int4 NF4 QLoRA (load_in_4bit, bf16 compute)"
    if load_in_8bit:
        return "int8 (load_in_8bit)"
    return "bf16 full precision (no quantization)"


def build_bnb_config(load_in_4bit: bool, load_in_8bit: bool):
    """BitsAndBytesConfig for the requested load, or None for full precision.

    Imports torch/transformers lazily so this module (and its contamination
    guards) stays importable and testable on a CPU-only machine.
    """
    if not load_in_4bit and not load_in_8bit:
        return None
    import torch
    from transformers import BitsAndBytesConfig
    if load_in_4bit:
        return BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )
    return BitsAndBytesConfig(load_in_8bit=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--train", required=True, help="train-split JSONL (see header)")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help="base checkpoint (HF id or path); default is the "
                         "pinned CohereLabs/aya-expanse-8b")
    ap.add_argument("--out", required=True, help="adapter output dir")
    ap.add_argument("--epochs", type=float, default=3.0)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--batch", type=int, default=2)
    ap.add_argument("--grad-accum", type=int, default=4)
    ap.add_argument("--lora-r", type=int, default=16)
    ap.add_argument("--load-in-4bit", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="load the base in 4-bit NF4 and train LoRA on top "
                         "(QLoRA) — REQUIRED on a 16 GB T4 for the 8B "
                         "checkpoint; --no-load-in-4bit restores bf16")
    ap.add_argument("--load-in-8bit", action="store_true",
                    help="load the base in 8-bit instead of 4-bit "
                         "(mutually exclusive with --load-in-4bit)")
    ap.add_argument("--skip-merge", action="store_true")
    args = ap.parse_args()

    if args.load_in_4bit and args.load_in_8bit:
        raise SystemExit("--load-in-4bit and --load-in-8bit are mutually exclusive")

    triples = load_triples(Path(args.train))
    systems = {t[0] for t in triples}
    if len(systems) != 1:
        raise SystemExit(
            f"expected exactly one shared system prompt, found {len(systems)} — "
            "the harness renders one fixed system prompt; mixed systems mean a "
            "malformed train split."
        )
    system_prompt = systems.pop()
    demonstrations = [{"prompt": u, "response": a} for _, u, a in triples]
    print(f"train records: {len(demonstrations)} (system prompt {len(system_prompt)} chars)")

    precision = load_precision_label(args.load_in_4bit, args.load_in_8bit)
    print(f"base load precision: {precision}")
    bnb_config = build_bnb_config(args.load_in_4bit, args.load_in_8bit)
    # QLoRA wiring: the quantization config travels as TRL's model_kwargs;
    # with use_peft=True the SFTTrainer prepares the quantized base for
    # k-bit training itself. If this AgentTune build cannot forward
    # model_kwargs, abort loudly — silently falling back to a bf16 load
    # would OOM the T4 mid-session and waste the run.
    trainer_kwargs = {}
    if bnb_config is not None:
        trainer_kwargs["model_kwargs"] = {"quantization_config": bnb_config}

    from agenttune import create_distill_trainer

    try:
        trainer = create_distill_trainer(
            student=args.model,
            demonstrations=demonstrations,
            system_prompt=system_prompt,
            use_peft=True,
            lora_r=args.lora_r,
            lora_alpha=2 * args.lora_r,
            lora_dropout=0.05,
            output_dir=args.out,
            num_train_epochs=args.epochs,
            learning_rate=args.lr,
            per_device_train_batch_size=args.batch,
            gradient_accumulation_steps=args.grad_accum,
            warmup_ratio=0.03,
            bf16=True,
            logging_steps=5,
            save_strategy="no",
            report_to=[],
            **trainer_kwargs,
        )
    except TypeError as exc:
        if trainer_kwargs and "model_kwargs" in str(exc):
            raise SystemExit(
                f"REFUSING to train: quantized load requested ({precision}) "
                "but this create_distill_trainer build does not accept "
                "model_kwargs, so the base would load in bf16 and OOM a "
                f"16 GB T4. Original error: {exc}"
            )
        raise

    # GPU-free sanity: preview the exact SFT rows AgentTune will train on.
    try:
        rows = trainer.sft_dataset()
        print(f"sft rows preview: {len(rows)} rows; first row keys: {list(rows[0])[:6]}")
    except Exception as exc:  # preview is a courtesy, never a blocker
        print(f"sft preview unavailable ({exc}); proceeding to train")

    result = trainer.train()
    print(f"training done: {result}")

    if args.skip_merge:
        print(f"adapter saved under {args.out} (merge skipped)")
        return 0

    # Merge the adapter so run_baseline.py can load the result as a plain model.
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    merged_dir = f"{args.out}-merged"
    # Merging DEQUANTIZES: the adapter trained on the 4-bit base is merged
    # into full-precision bf16 weights (~16 GB for the 8B), whatever the
    # training load precision was. device_map="auto" lets the load spill
    # to CPU RAM; if the T4 session cannot hold it, run this merge step in
    # a bigger-RAM session — the adapter dir alone is a valid stopping
    # point (--skip-merge) and can be merged later.
    base = AutoModelForCausalLM.from_pretrained(
        args.model, torch_dtype=torch.bfloat16, device_map="auto")
    model = PeftModel.from_pretrained(base, args.out)
    model = model.merge_and_unload()
    model.save_pretrained(merged_dir)
    AutoTokenizer.from_pretrained(args.model).save_pretrained(merged_dir)
    print(f"merged model saved: {merged_dir}")
    print("after-run (identical command to the before run; on a T4 the "
          "merged bf16 weights need the 8-bit load flag, exactly as the "
          "baseline did):")
    print(
        f"  python3 baseline/run_baseline.py --model {merged_dir} "
        "--load-in-8bit "
        "--tasks triage-tasks.jsonl --out runs/after-v1"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
