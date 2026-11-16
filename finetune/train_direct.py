#!/usr/bin/env python3
"""Direct QLoRA trainer for the Lexsi heal loop — bypasses AgentTune.

Why this exists (Nov 15, 2026, ~10:42 p.m. ET): agenttune 1.1.5's
create_distill_trainer cannot accept a quantization config at all — its
factory loads the base in bf16 only, and trl 1.7.1's SFTConfig rejects the
model_kwargs the AgentTune path forwards. On a 16 GB T4 a bf16 load of the
8B checkpoint OOMs, so that path is dead. This script trains the identical
job — same train split, same contamination guard (reused from
train_finetune.load_triples), same hyperparameters (epochs 3, lr 1e-4,
batch 2, grad-accum 4, LoRA r16, 4-bit NF4 QLoRA) — with plain
transformers Trainer + peft, loading the base quantized directly.

Training format matches inference exactly: the harness renders
system+user through the tokenizer chat template with a generation prompt;
here the full text is the same template with the assistant turn appended
(the gold JSON tool sequence), and labels mask everything except the
assistant tokens, so the model learns to emit the gold calls, not to
reproduce the prompt.

Usage (Kaggle GPU session):
    python3 finetune/train_direct.py \
        --train finetune/train-split.jsonl \
        --model CohereLabs/aya-expanse-8b \
        --out runs/adapter-v1
    # then merge (bf16, device_map auto — may spill to CPU RAM):
    python3 finetune/train_direct.py --merge-only \
        --model CohereLabs/aya-expanse-8b --out runs/adapter-v1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from train_finetune import load_triples  # noqa: E402  (contamination guard)


def build_examples(tokenizer, triples, max_len=1536):
    examples = []
    for system, user, assistant in triples:
        msgs_prompt = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        msgs_full = msgs_prompt + [{"role": "assistant", "content": assistant}]
        prompt_text = tokenizer.apply_chat_template(
            msgs_prompt, tokenize=False, add_generation_prompt=True)
        full_text = tokenizer.apply_chat_template(
            msgs_full, tokenize=False, add_generation_prompt=False)
        prompt_ids = tokenizer(prompt_text, add_special_tokens=False)["input_ids"]
        full_ids = tokenizer(full_text, add_special_tokens=False)["input_ids"]
        if len(full_ids) > max_len:
            continue
        labels = [-100] * len(prompt_ids) + full_ids[len(prompt_ids):]
        if all(l == -100 for l in labels):
            raise SystemExit("empty completion after masking — template mismatch")
        examples.append({"input_ids": full_ids, "labels": labels})
    return examples


class ListDataset:
    """Minimal torch Dataset over the example dicts (avoids datasets lib)."""

    def __init__(self, rows):
        self.rows = rows

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        return self.rows[i]


class Collator:
    def __init__(self, pad_id):
        self.pad_id = pad_id

    def __call__(self, features):
        maxlen = max(len(f["input_ids"]) for f in features)
        input_ids, labels, attn = [], [], []
        for f in features:
            ids = list(f["input_ids"])
            lab = list(f["labels"])
            pad = maxlen - len(ids)
            input_ids.append(ids + [self.pad_id] * pad)
            labels.append(lab + [-100] * pad)
            attn.append([1] * len(ids) + [0] * pad)
        import torch
        return {
            "input_ids": torch.tensor(input_ids, dtype=torch.long),
            "labels": torch.tensor(labels, dtype=torch.long),
            "attention_mask": torch.tensor(attn, dtype=torch.long),
        }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--train", default="finetune/train-split.jsonl")
    ap.add_argument("--model", default="CohereLabs/aya-expanse-8b")
    ap.add_argument("--out", required=True, help="adapter output dir")
    ap.add_argument("--epochs", type=float, default=3.0)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--batch", type=int, default=2)
    ap.add_argument("--grad-accum", type=int, default=4)
    ap.add_argument("--lora-r", type=int, default=16)
    ap.add_argument("--merge-only", action="store_true",
                    help="skip training; merge <out> adapter into <out>-merged")
    args = ap.parse_args()

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    if args.merge_only:
        from peft import PeftModel
        merged_dir = f"{args.out}-merged"
        print(f"merging {args.out} into {merged_dir} (bf16, device_map=auto)")
        base = AutoModelForCausalLM.from_pretrained(
            args.model, dtype=torch.bfloat16, device_map="auto")
        model = PeftModel.from_pretrained(base, args.out)
        model = model.merge_and_unload()
        model.save_pretrained(merged_dir)
        tokenizer.save_pretrained(merged_dir)
        print(f"merged model saved: {merged_dir}")
        return 0

    triples = load_triples(Path(args.train))
    print(f"triples: {len(triples)}")
    examples = build_examples(tokenizer, triples)
    print(f"examples kept (<=1536 tokens): {len(examples)}")
    if len(examples) < 300:
        raise SystemExit(f"too few examples after tokenization: {len(examples)}")

    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import BitsAndBytesConfig, Trainer, TrainingArguments

    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        args.model, quantization_config=bnb, device_map="auto")
    model = prepare_model_for_kbit_training(model)
    lora = LoraConfig(
        r=args.lora_r,
        lora_alpha=2 * args.lora_r,
        lora_dropout=0.05,
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
    )
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    targs = TrainingArguments(
        output_dir=args.out,
        num_train_epochs=args.epochs,
        learning_rate=args.lr,
        per_device_train_batch_size=args.batch,
        gradient_accumulation_steps=args.grad_accum,
        warmup_steps=4,
        bf16=True,
        logging_steps=5,
        save_strategy="no",
        report_to=[],
        optim="paged_adamw_8bit",
        gradient_checkpointing=True,
    )
    model.config.use_cache = False
    trainer = Trainer(
        model=model,
        args=targs,
        train_dataset=ListDataset(examples),
        data_collator=Collator(tokenizer.pad_token_id),
    )
    result = trainer.train()
    print(f"training done: {result}")
    model.save_pretrained(args.out)
    tokenizer.save_pretrained(args.out)
    print(f"adapter saved: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
