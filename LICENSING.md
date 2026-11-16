# Licensing — what covers what in this repo

| Component | Licence |
|---|---|
| All code in this repository (harness, scripts, notebooks) | MIT — see `LICENSE` |
| `triage-tasks.jsonl` (held-out dataset; also published on Hugging Face as `Snedz/lexsi-triage-tasks`) | CC BY-NC 4.0 |
| `finetune/train-split.jsonl` (training split) | CC BY-NC 4.0 (same terms as the dataset it accompanies) |
| Base checkpoint `CohereLabs/aya-expanse-8b` (not in this repo; downloaded from Hugging Face) | CC-BY-NC (Cohere) |
| Published adapter `Snedz/lexsi-aya-expanse-8b-triage-adapter` and merged model `Snedz/lexsi-aya-expanse-8b-triage-healed` (on Hugging Face, not in this repo) | Inherit the base checkpoint's CC-BY-NC terms |
| AgentTune (`Lexsi-Labs/AgentTune`) and AuditKIT (`Lexsi-Labs/AuditKIT`) | LSAL v1.1 / LSAL v1.2 respectively — used as libraries; none of their code is vendored in this repo |

The MIT licence for the code was chosen by the entrant for this
repo; the submission pack did not prescribe a code licence. The
base model's non-commercial terms bind the model artefacts
regardless of the code licence.
