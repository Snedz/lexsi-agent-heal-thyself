"""Model backends for the triage baseline + trace export.

Two backends:
  - TransformersBackend: a local Hugging Face checkpoint (the Monday GPU
    run). Defaults to CohereLabs/aya-expanse-8b — the Cohere open-weights
    id verified in AuditKIT's own README examples. The Tiny Aya candidate
    id is UNVERIFIED (see README.md); pass --model to use it once checked.
  - EchoBackend: a deterministic plumbing smoke. It does NOT look at gold
    answers; it emits one fixed, well-formed guess for every item so the
    full pipeline (load -> run -> parse -> score -> write) can be proven
    on any machine with no GPU. Its scores are pipeline evidence only and
    are watermarked as NOT baseline numbers everywhere they are written.

Trace export: every run also writes an OpenAI-messages projection of each
episode (system/user/assistant with tool_calls). That is deliberate
interop: AuditKIT's `auditkit.agent_eval` layer is documented to read
recorded runs in OpenAI-messages form (and AgentTune EventLog traces),
which is how these baselines get formalized into the AuditKIT record on
Monday without inventing an API surface we could not verify tonight.

VRAM / load precision (fixed Nov 14): aya-expanse-8b at FP16 needs
~17.9 GB, which does NOT fit a 16 GB T4 — `torch_dtype="auto"` alone
spills layers to CPU RAM and the run crawls instead of failing. On
T4-class routes load in 8-bit instead (`load_in_8bit=True`, via
bitsandbytes): ~8 GB weights, same checkpoint, same greedy decoding,
same scorer. The load precision is part of the run fingerprint (see
run_baseline.py) so a replicated run on a bigger GPU is never silently
compared against an 8-bit T4 run. Set the backend flag, the
`--load-in-8bit` CLI flag, or the `LEXSI_LOAD_IN_8BIT=1` env var.
"""
import json
import os

from toolspec import build_system_prompt, build_user_prompt

DEFAULT_MODEL = "CohereLabs/aya-expanse-8b"  # verified id (AuditKIT README)
TINY_AYA_CANDIDATE = "CohereLabs/tiny-aya-global"  # corrected id (Nov 13); fallback only — its gated-licence accept is still outstanding


class EchoBackend:
    name = "echo-plumbing-smoke"
    model_id = "echo"
    load_precision = "n/a (echo, no model loaded)"

    def generate(self, system_prompt, user_prompt):
        return json.dumps([
            {"tool": "classify_item", "arguments": {"category": "mailbox"}},
            {"tool": "abstain", "arguments": {"reason": "insufficient_info"}},
        ])


class TransformersBackend:
    name = "transformers"

    def __init__(self, model_id=DEFAULT_MODEL, max_new_tokens=320,
                 load_in_8bit=None):
        self.model_id = model_id
        self.max_new_tokens = max_new_tokens
        if load_in_8bit is None:
            load_in_8bit = os.environ.get("LEXSI_LOAD_IN_8BIT", "") \
                .strip().lower() in ("1", "true", "yes")
        self.load_in_8bit = bool(load_in_8bit)
        self._tok = None
        self._model = None

    @property
    def load_precision(self):
        """Serving precision of this run — fingerprinted in scores.json."""
        return "int8 (load_in_8bit)" if self.load_in_8bit else "auto (fp16/bf16)"

    def _load(self):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self._tok = AutoTokenizer.from_pretrained(self.model_id)
        if self.load_in_8bit:
            # T4 route: 8-bit weights (~8 GB) fit the 16 GB card; FP16
            # (~17.9 GB) does not. Requires bitsandbytes (requirements.txt).
            # transformers 5.x removed the bare load_in_8bit kwarg — pass
            # it via BitsAndBytesConfig (same pattern as train_finetune.py).
            from transformers import BitsAndBytesConfig
            self._model = AutoModelForCausalLM.from_pretrained(
                self.model_id,
                quantization_config=BitsAndBytesConfig(load_in_8bit=True),
                device_map="auto")
        else:
            self._model = AutoModelForCausalLM.from_pretrained(
                self.model_id, torch_dtype="auto", device_map="auto")
        self._model.eval()

    def generate(self, system_prompt, user_prompt):
        if self._model is None:
            self._load()
        import torch
        messages = [{"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}]
        if hasattr(self._tok, "apply_chat_template"):
            prompt = self._tok.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True)
        else:
            prompt = system_prompt + "\n\n" + user_prompt + "\n\n["
        inputs = self._tok(prompt, return_tensors="pt").to(self._model.device)
        with torch.no_grad():
            out = self._model.generate(
                **inputs, max_new_tokens=self.max_new_tokens,
                do_sample=False, temperature=None, top_p=None,
                pad_token_id=self._tok.eos_token_id)
        text = self._tok.decode(out[0][inputs["input_ids"].shape[1]:],
                                skip_special_tokens=True)
        return text


def run_items(items, backend, limit=None):
    """Run the agent over items; returns (predictions, traces)."""
    system_prompt = build_system_prompt()
    predictions, traces = [], []
    for item in items[:limit] if limit else items:
        user_prompt = build_user_prompt(item)
        raw = backend.generate(system_prompt, user_prompt)
        predictions.append({"id": item["id"], "raw_output": raw})
        # OpenAI-messages projection for AuditKIT agent_eval consumption
        traces.append({
            "id": item["id"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": raw},
            ],
            "gold_tool_sequence": item["gold"]["tool_sequence"],
            "gold_action_class": item["gold"]["action_class"],
        })
    return predictions, traces
