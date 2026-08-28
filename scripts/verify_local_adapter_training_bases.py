#!/usr/bin/env python3
"""Verify pinned local-adapter training bases and run bounded offline smokes."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.services.local_adapter_training_base_verifier import (  # noqa: E402
    LocalAdapterTrainingBaseVerifier,
)
from ananta_contracts.local_adapter_training_base import (  # noqa: E402
    LocalAdapterTrainingBaseCatalog,
    LocalAdapterTrainingBasePin,
)

DEFAULT_CATALOG = ROOT / "config/models/local-adapter-training-bases.v1.json"

_NEEDLE_SMOKE = r"""
import hashlib
import json
import socket
import sys
from pathlib import Path
from importlib.metadata import version
from needle.model.architecture import SimpleAttentionNetwork
from needle.model.run import load_checkpoint
from needle.model.tokenizer import SANTokenizer

def _network_denied(*_args, **_kwargs):
    raise RuntimeError("offline_smoke_network_denied")

socket.create_connection = _network_denied
socket.socket.connect = _network_denied

root = Path(sys.argv[1])
expected = json.loads(sys.argv[2])
if version("cactus-needle") != expected["runtime_version"]:
    raise SystemExit("needle_runtime_version_mismatch")
checkpoint = root / "checkpoints/needle2.pkl"
tokenizer_path = root / "tokenizer/tokenizer.model"
params, config = load_checkpoint(str(checkpoint))
model = SimpleAttentionNetwork(config)
tokenizer = SANTokenizer(str(tokenizer_path))
if tokenizer.vocab_size != config.vocab_size or not params:
    raise SystemExit("needle_checkpoint_tokenizer_incompatible")
print(json.dumps({"model": type(model).__name__, "vocab_size": tokenizer.vocab_size,
                  "max_seq_len": model.config.max_seq_len}, sort_keys=True))
"""

_LFM_SMOKE = r"""
import json
import socket
import sys
from pathlib import Path
from accelerate import init_empty_weights
from peft import LoraConfig, get_peft_model
from safetensors import safe_open
from transformers import AutoConfig, AutoModelForCausalLM, PreTrainedTokenizerFast

def _network_denied(*_args, **_kwargs):
    raise RuntimeError("offline_smoke_network_denied")

socket.create_connection = _network_denied
socket.socket.connect = _network_denied

root = Path(sys.argv[1])
index = json.loads((root / "model.safetensors.index.json").read_text(encoding="utf-8"))
expected_keys = set(index["weight_map"])
observed_keys = set()
for shard_name in sorted(set(index["weight_map"].values())):
    with safe_open(root / shard_name, framework="pt", device="cpu") as shard:
        observed_keys.update(shard.keys())
        for key in shard.keys():
            shape = shard.get_slice(key).get_shape()
            if not shape or any(int(dimension) < 1 for dimension in shape):
                raise SystemExit("lfm_weight_shape_invalid")
if observed_keys != expected_keys:
    raise SystemExit("lfm_weight_index_mismatch")
config = AutoConfig.from_pretrained(root, local_files_only=True, trust_remote_code=False)
tokenizer_configuration = json.loads((root / "tokenizer_config.json").read_text(encoding="utf-8"))
if tokenizer_configuration.get("tokenizer_class") != "TokenizersBackend":
    raise SystemExit("lfm_tokenizer_contract_mismatch")
tokenizer = PreTrainedTokenizerFast(
    tokenizer_file=str(root / "tokenizer.json"),
    bos_token=tokenizer_configuration["bos_token"],
    eos_token=tokenizer_configuration["eos_token"],
    pad_token=tokenizer_configuration["pad_token"],
)
tokenizer.chat_template = (root / "chat_template.jinja").read_text(encoding="utf-8")
if config.model_type != "lfm2" or config.architectures != ["Lfm2ForCausalLM"]:
    raise SystemExit("lfm_agentic_architecture_mismatch")
rendered = tokenizer.apply_chat_template(
    [{"role": "user", "content": "Return a safe tool decision."}],
    tokenize=False,
    add_generation_prompt=True,
)
if not rendered or tokenizer.chat_template is None:
    raise SystemExit("lfm_chat_template_unavailable")
with init_empty_weights():
    model = AutoModelForCausalLM.from_config(config, trust_remote_code=False)
    target_modules = sorted({
        name.rsplit(".", 1)[-1]
        for name, module in model.named_modules()
        if module.__class__.__name__ == "Linear"
        and name.rsplit(".", 1)[-1] in {"q_proj", "k_proj", "v_proj", "out_proj"}
    })
    if not target_modules:
        raise SystemExit("lfm_lora_targets_unavailable")
    attached = get_peft_model(model, LoraConfig(
        r=8,
        lora_alpha=16,
        lora_dropout=0.0,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=target_modules,
    ))
    if not any("lora_" in name for name, _ in attached.named_parameters()):
        raise SystemExit("lfm_adapter_attach_failed")
print(json.dumps({"architecture": config.architectures[0], "weight_tensors": len(observed_keys),
                  "lora_targets": target_modules}, sort_keys=True))
"""


def _catalog(path: Path) -> LocalAdapterTrainingBaseCatalog:
    return LocalAdapterTrainingBaseCatalog.model_validate_json(path.read_text(encoding="utf-8"))


def _base(catalog: LocalAdapterTrainingBaseCatalog, target: str) -> LocalAdapterTrainingBasePin:
    return next(base for base in catalog.bases if base.release_target == target)


def _offline_environment() -> dict[str, str]:
    return {
        **os.environ,
        "HF_DATASETS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_OFFLINE": "1",
        "NO_PROXY": "*",
        "TRANSFORMERS_OFFLINE": "1",
    }


def _run_smoke(python: Path, script: str, root: Path, *arguments: str) -> dict[str, Any]:
    result = subprocess.run(
        [str(python), "-c", script, str(root), *arguments],
        check=False,
        capture_output=True,
        env=_offline_environment(),
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-1_000:]
        raise RuntimeError(f"local_adapter_training_base_smoke_failed:{detail}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--needle-root", type=Path)
    parser.add_argument("--needle-python", type=Path)
    parser.add_argument("--lfm-root", type=Path)
    parser.add_argument("--lfm-python", type=Path)
    args = parser.parse_args()
    catalog = _catalog(args.catalog)
    verifier = LocalAdapterTrainingBaseVerifier()
    report: dict[str, Any] = {"schema_version": catalog.schema_version, "targets": {}}
    requested = (
        ("needle2", args.needle_root, args.needle_python),
        ("lfm2.5-2.6b-agentic", args.lfm_root, args.lfm_python),
    )
    for target, root, python in requested:
        if root is None:
            continue
        base = _base(catalog, target)
        verification = verifier.verify(base, root)
        item: dict[str, Any] = {
            "catalog_id": base.catalog_id,
            "files_verified": verification.verified_artifacts,
            "passed": verification.passed,
            "reason_codes": list(verification.reason_codes),
        }
        if not verification.passed:
            report["targets"][target] = item
            print(json.dumps(report, sort_keys=True))
            return 1
        if python is not None:
            if target == "needle2":
                if base.training_runtime is None:
                    raise RuntimeError("needle_training_runtime_pin_missing")
                item["offline_smoke"] = _run_smoke(
                    python,
                    _NEEDLE_SMOKE,
                    root,
                    json.dumps({"runtime_version": base.training_runtime.version}),
                )
            else:
                item["offline_smoke"] = _run_smoke(python, _LFM_SMOKE, root)
        report["targets"][target] = item
    report["passed"] = bool(report["targets"]) and all(
        item["passed"] for item in report["targets"].values()
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
