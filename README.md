# Agent, heal thyself — a fleet agent, measured on its own triage work, healed from its measured failures

**Lexsi — AI Safety and Alignment Hackathon · Track 3 (Agentic
Fine-tuning), PS8 (self-healing) + PS7 (workflow/decision agents),
with an embedded Track 2b drift probe · Solo entry, Mission Winning
(Hugging Face: Snedz)**

A coding-agent fleet generates its own failure data every day. We
point a general open-weights checkpoint — `CohereLabs/aya-expanse-8b`,
loaded in 8-bit — at 104 held-out operations-triage tasks synthesized
from those real task shapes, measure exactly where it fails, train
only on fresh examples of the failure classes it actually measured,
and re-measure on the untouched held-out set. The contribution is
the closed loop, run end to end on one checkpoint: measure →
classify → generate fresh correctives in exactly those classes →
retrain → gate → re-measure identically.

## Headline result (n = 104, both runs, identical scoring)

| Metric | Base | Healed |
|---|---|---|
| Tool-call accuracy (exact sequence, all arguments valid) | 0.0% (0/104), 95% CI [0.0%, 3.6%] | 22.1% (23/104), 95% CI [15.2%, 31.0%] |
| Step accuracy | 8.1% | 46.8% |
| Argument schema-valid rate | 99.0% | 100% |
| Argument value-valid rate | 98.1% | 100% |

Baseline failure classes: wrong_tool 74, missing_step 14,
unsafe_action 9, extra_step 5, malformed_args 1, bad_arg_value 1.
The outputs were almost always well-formed — this is a
tool-selection failure, not a formatting failure. Baseline run
fingerprint:
`a0fe0240707f078a116b172595be4689650a43cc07922095faa088dc2e3fd53f`.

Healed failure classes: wrong_tool 49, missing_step 15,
extra_step 10, unsafe_action 6, ordering 1 — 23 of 104 exact.
Paired delta +22.1 percentage points (23 items flipped
fail → pass, 0 flipped back; McNemar exact p = 0.0000).
After-run fingerprint:
`9e9cfcfac29d71f4379389f56859df0db32b29d7e77c70d95b4f94000fc89c6a`.

Model repos:
[adapter](https://huggingface.co/Snedz/lexsi-aya-expanse-8b-triage-adapter)
·
[healed (merged)](https://huggingface.co/Snedz/lexsi-aya-expanse-8b-triage-healed)

The full write-up (method, per-lane and per-class tables, paired
statistics, drift probe, limitations) is published as the entry's
Hugging Face article and mirrored in the submission; this README
carries the reproducibility notes.

## Method disclosure

> The 104 held-out tasks and the scorer are both authored by us,
> synthesized from the real task shapes of our own agent fleet.
> After the baseline run, we inspected its failure classes on the
> held-out set and authored the training split in those classes.
> Training rows are fresh items only: held-out items are never
> copied, and held-out-derived records are used as diagnostics,
> never as training data. This is enforced in code, not by
> intention: the corrective generator marks held-out-derived
> records `heldout-derived` and excludes them from training output,
> and the fine-tune script aborts on any held-out contamination.
> The guards stop copying; they do not stop taxonomy leakage — the
> training split deliberately targets the same failure taxonomy the
> held-out set measures. We state this plainly because it is the
> design: the claim is that a fleet can heal its own measured
> failure classes, not that the model generalises to an unseen
> taxonomy.
>
> **Scorer validation.** The scoring harness passes its own
> self-test 10/10, and the AuditKIT formalization's verdicts matched
> `scoring.py` on 104/104 items in the echo smoke test. The baseline
> was also independently re-scored off-platform from its downloaded
> predictions: the re-score reproduced the original `scores.json`
> exactly on every compared metric and per-item field.

## Artefacts (all public and ungated)

| Artefact | Location |
|---|---|
| Held-out dataset (104 triage tasks) | https://huggingface.co/datasets/Snedz/lexsi-triage-tasks |
| Collection (dataset + models) | https://huggingface.co/collections/Snedz/lexsi-agent-heal-thyself |
| Adapter (LoRA, as trained) | https://huggingface.co/Snedz/lexsi-aya-expanse-8b-triage-adapter — PUBLISHED |
| Healed model (merged) | https://huggingface.co/Snedz/lexsi-aya-expanse-8b-triage-healed — PUBLISHED |
| Training split | `finetune/train-split.jsonl` in this repo (340 fresh items, `provenance="train"`) |
| Raw traces + AuditKIT records, both runs | After-run traces in the healed-model repo root (`scores.json`, `predictions.jsonl`, `traces.jsonl`, `summary.md`); both runs' AuditKIT records published with the entry via the HF collection |

## Repo layout

| Path | What it is |
|---|---|
| `triage-tasks.jsonl` | The 104 held-out items (also the HF dataset) |
| `build_tasks.py` | Generator for the held-out set, incl. its privacy scan |
| `baseline/` | The scoring harness: `toolspec.py` (tool vocabulary + prompts), `scoring.py` (deterministic scorer + failure taxonomy), `triage_agent.py` (model backends), `run_baseline.py` (run + fingerprint), `test_scoring.py` (self-test), `formalize_auditkit.py` (AuditKIT record), notebooks for Kaggle/Colab |
| `zerogpu/build_correctives.py` | Failure report + corrective generator; marks held-out-derived records and excludes them from training output |
| `finetune/build_train_tasks.py` | Generator for the fresh 340-item train split |
| `finetune/validate_train_split.py` | Split validator (ids disjoint from held-out, no text overlap, gold calls valid) |
| `finetune/train_direct.py` | The fine-tune as run: QLoRA trainer (transformers Trainer + peft) + `--merge-only` merge mode |
| `finetune/train_finetune.py` | The AgentTune-trainer variant + the shared contamination guard (`load_triples`) |
| `finetune/train-split.jsonl` | The 340-item training split |
| `stats/paired_stats.py` | Paired before/after statistics: Wilson CIs, paired delta, McNemar exact test, per-lane table (stdlib only) |
| `probe/` | Track 2b drift-probe prompts (`overrefusal-prompts.jsonl`) and the base-model results (`results-base/`) |

## Run instructions

1. Scorer self-test (no GPU): `python3 baseline/test_scoring.py` —
   expect 10/10.
2. Baseline: open `baseline/kaggle_run.ipynb`, attach the pack
   dataset, add the `HF_TOKEN` secret, Run All — writes
   `scores.json`, `predictions.jsonl`, `traces.jsonl`, `summary.md`,
   fingerprinted. (Equivalent CLI:
   `python3 baseline/run_baseline.py --backend transformers --model
   CohereLabs/aya-expanse-8b --load-in-8bit --out results/aya-expanse-8b-base`.)
3. Correctives + train split: `python3 zerogpu/build_correctives.py`
   on the baseline output → failure report; the fresh split is
   generated by `finetune/build_train_tasks.py` and checked by
   `finetune/validate_train_split.py`.
4. Fine-tune (as run): `python3 finetune/train_direct.py --train
   finetune/train-split.jsonl --model CohereLabs/aya-expanse-8b
   --out runs/adapter-v1 --epochs 1 --batch 1 --grad-accum 8`, then
   `python3 finetune/train_direct.py --merge-only --model
   CohereLabs/aya-expanse-8b --out runs/adapter-v1`.
   Hyperparameters: 4-bit NF4 QLoRA (double quant, bf16 compute),
   LoRA r 16 / alpha 32 / dropout 0.05, targets q/k/v/o/gate/up/down,
   lr 1e-4, warmup 4 steps, `paged_adamw_8bit`, max_len 1536,
   43 steps, 145.3 min on Kaggle T4, final train loss 0.4614.
5. After-run: the identical baseline command against the merged
   model; then `baseline/formalize_auditkit.py` on both runs and
   `python3 stats/paired_stats.py <base-run> <after-run>`.

Kits: AgentTune (`Lexsi-Labs/AgentTune`, LSAL v1.1 — heal-loop
architecture; its trainer was evaluated and could not accept a
quantization config, see the write-up §9) and AuditKIT
(`Lexsi-Labs/AuditKIT`, LSAL v1.2; package 1.1.12 — run records +
comparison). Versions as run: auditkit 1.1.12, transformers 5.20.2,
bitsandbytes 0.50.2, peft 0.21.2.

## Licences

- Code in this repo: MIT (see `LICENSE`).
- Dataset (`triage-tasks.jsonl`, and the HF dataset): CC BY-NC 4.0.
- Base checkpoint `CohereLabs/aya-expanse-8b`: CC-BY-NC (Cohere);
  the adapter and merged model inherit the base model's terms and
  are never presented as commercial assets.
- Kits are used as libraries under their LSAL licences; none of
  their code is vendored here. See `LICENSING.md`.

## Limitations (summary — full text in the write-up)

Single run, no replication; no generic-control ablation (named as
not run); self-authored tasks and scorer with taxonomy leakage
disclosed above; one fleet's task shapes, synthesized — no fleet
data used; both runs load in 8-bit, so absolutes are not comparable
against bf16 entries.

## Outside-work declaration

Solo entrant (Mission Winning). Declared as pre-existing: the fleet
task shapes the dataset is synthesized from, and the scoring
harness, which predate this entry and are the entrant's own
tooling. Everything else for this entry — the dataset itself, the
corrective pipeline, the fine-tune, and every run reported here —
was produced during the event window.
