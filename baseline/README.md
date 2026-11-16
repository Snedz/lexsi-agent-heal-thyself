# Triage baseline harness — Lexsi concept #1 ("Agent, heal thyself", Track 3)

Runs a Cohere open-weights checkpoint as the fleet-triage agent over
`../triage-tasks.jsonl` (104 held-out items synthesized from real fleet
task shapes) and scores the metrics the entry reports before/after:

- **tool-call accuracy** — exact tool sequence, with all arguments valid
- **argument well-formedness** — schema-valid and value-valid rates over aligned calls
- **ordering errors** — right tools, wrong order (rate + inversion count)
- **steps-per-task**, **failure-class distribution** (no_parse, wrong_tool,
  missing_step, extra_step, ordering, malformed_args, bad_arg_value,
  abstention_failure, unsafe_action) — the taxonomy the self-healing loop
  consumes in the fine-tune phase

## Files

| File | Role |
|---|---|
| `toolspec.py` | The fixed tool vocabulary, argument schemas, and the system prompt + JSON output protocol the agent is measured against |
| `scoring.py` | Parser + all metrics + failure classification (stdlib only) |
| `triage_agent.py` | Backends: `transformers` (Monday GPU run) and `echo` (deterministic plumbing smoke — never baseline numbers); exports OpenAI-messages traces |
| `run_baseline.py` | CLI: load → run → score → write `scores.json` / `predictions.jsonl` / `traces.jsonl` / `summary.md` into `--out`, fingerprinted (sha256 over model + backend + tasks file) |
| `test_scoring.py` | 10 synthetic cases proving every failure class is detected. **10/10 pass (2026-11-08)** |
| `auditkit_config.yaml` | How the run is formalized into the AuditKIT record on Monday, with the unverified API points marked |
| `colab_run.ipynb` | The Monday Colab run, cell by cell |
| `requirements.txt` | GPU-environment deps (the harness core itself is stdlib-only) |

## Grounding — what was verified from the toolkits, and what was not

Read directly from the repos (2026-11-08):

- **AuditKIT** (`Lexsi-Labs/AuditKIT`, LSAL v1.2): `import auditkit as ak`;
  `ak.Sample(input=, target=)`; `ak.evaluate(samples, model=)` where model is a
  `list[str] -> list[str]` callable or a backend string; backends include
  `hf:` (e.g. `hf:CohereLabs/aya-expanse-8b` — the Cohere id used as this
  harness's verified default); `ak.compare(base, tuned)`; every run writes a
  `RunResult` with a sha256 fingerprint over model+seed+tasks+config (our
  `scores.json` mirrors this). Its **`auditkit.agent_eval`** layer evaluates
  whole task episodes — verified outcome as headline, tool/trace/judge scores
  as diagnostics — and reads recorded runs in **AgentTune, OpenAI-messages, or
  Sample-trace** form. That is why `traces.jsonl` is an OpenAI-messages
  projection: it is the documented interop format, not an invented one.
- **AgentTune** (`Lexsi-Labs/AgentTune`, LSAL v1.1): an RL trainer on TRL
  (`agenttune.core.backend_factory.create_agentic_trainer`, GRPO/PPO/DPO/
  RLOO/BCO) plus an agentic spine — build → collect → evaluate → train →
  distill → heal — over one normalized `EventLog` trajectory schema (light
  observational tier / full trainable tier). Its self-healing loop is
  detect → classify → generate corrective example → retrain → gate, which is
  the shape concept #1 implements and the shape `scoring.py`'s failure
  taxonomy feeds.

**UNVERIFIED (flagged, not guessed):**
- The exact Python API of `auditkit.agent_eval` (function/class names) — the
  README confirms the layer and its inputs, not its signatures. Monday's
  first step is `dir(auditkit.agent_eval)` + `auditkit agent --help`
  (see `auditkit_config.yaml`).
- The HF model id for **Tiny Aya** (`CohereLabs/tiny-aya` is a candidate,
  not confirmed). The default here is the verified `CohereLabs/aya-expanse-8b`;
  the Colab notebook resolves whichever Cohere id actually exists before running.
- AgentTune's `EventLog` field-level schema — the heal phase (later in the
  week) must read it from the repo source before writing full-tier traces;
  the baseline needs only the light/observational side, which the
  OpenAI-messages projection covers.

## Tonight's proof state

- `python3 test_scoring.py` → **10/10 pass** on this VM (no GPU).
- `python3 run_baseline.py --backend echo --out results/echo-smoke` → full
  pipeline over all 104 items; outputs in `results/echo-smoke/`, watermarked
  **PLUMBING SMOKE — NOT BASELINE NUMBERS**. The echo backend emits one fixed
  guess per item and never sees gold; it exists to prove the plumbing.
- The real "before" numbers require the transformers run — Monday, per
  `../BASELINE-PLAN.md`.

## Licence note

The harness code in this folder is Program 67's own (written for this
entry). AuditKIT/AgentTune are used as libraries for the contest, which
their LSAL licences are designed to cover; nothing here vendors their code.
Post-contest internal adoption of the v1.2 kits is the founder-gated §1A
acknowledgement item from the research pack — out of scope for the baseline.
