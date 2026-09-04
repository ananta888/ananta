"""Optional PyTorch tiny causal-transformer model for research stages.

Torch is imported only inside the Worker execution path.  Importing Ananta's
Hub and shared contracts therefore remains dependency-free.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from ananta_contracts.research_training import canonical_json


@dataclass(frozen=True, slots=True)
class TinyCausalLmConfig:
    vocab_size: int
    context_length: int
    hidden_size: int
    num_layers: int
    attention_heads: int
    dropout: float = 0.0

    def validate(self) -> None:
        if not 256 <= self.vocab_size <= 65_536:
            raise ValueError("research_model_vocab_size_invalid")
        if not 2 <= self.context_length <= 8192:
            raise ValueError("research_model_context_length_invalid")
        if not 32 <= self.hidden_size <= 4096:
            raise ValueError("research_model_hidden_size_invalid")
        if not 1 <= self.num_layers <= 128:
            raise ValueError("research_model_layers_invalid")
        if not 1 <= self.attention_heads <= 128 or self.hidden_size % self.attention_heads:
            raise ValueError("research_model_attention_heads_invalid")
        if not 0 <= self.dropout < 1:
            raise ValueError("research_model_dropout_invalid")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TinyCausalLmConfig:
        if set(value) != {
            "vocab_size",
            "context_length",
            "hidden_size",
            "num_layers",
            "attention_heads",
            "dropout",
        }:
            raise ValueError("research_model_config_fields_invalid")
        config = cls(
            vocab_size=int(value["vocab_size"]),
            context_length=int(value["context_length"]),
            hidden_size=int(value["hidden_size"]),
            num_layers=int(value["num_layers"]),
            attention_heads=int(value["attention_heads"]),
            dropout=float(value["dropout"]),
        )
        config.validate()
        return config


def require_torch():
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - exercised in the dependency-free image gate
        raise RuntimeError("research_torch_runtime_unavailable") from exc
    return torch


def build_model(config: TinyCausalLmConfig):
    torch = require_torch()
    config.validate()

    class TinyCausalLm(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.token_embedding = torch.nn.Embedding(config.vocab_size, config.hidden_size)
            self.position_embedding = torch.nn.Embedding(config.context_length, config.hidden_size)
            layer = torch.nn.TransformerEncoderLayer(
                d_model=config.hidden_size,
                nhead=config.attention_heads,
                dim_feedforward=config.hidden_size * 4,
                dropout=config.dropout,
                activation="gelu",
                batch_first=True,
                norm_first=True,
            )
            self.transformer = torch.nn.TransformerEncoder(layer, num_layers=config.num_layers)
            self.normalization = torch.nn.LayerNorm(config.hidden_size)
            self.output = torch.nn.Linear(config.hidden_size, config.vocab_size, bias=False)
            self.output.weight = self.token_embedding.weight

        def forward(self, tokens):
            if tokens.ndim != 2 or tokens.shape[1] > config.context_length:
                raise ValueError("research_model_input_shape_invalid")
            positions = torch.arange(tokens.shape[1], device=tokens.device)
            hidden = self.token_embedding(tokens) + self.position_embedding(positions)[None, :, :]
            causal = torch.nn.Transformer.generate_square_subsequent_mask(
                tokens.shape[1], device=tokens.device
            )
            hidden = self.transformer(hidden, mask=causal, is_causal=True)
            return self.output(self.normalization(hidden))

    return TinyCausalLm()


def load_checkpoint(content: bytes):
    try:
        from safetensors.torch import load
    except ImportError as exc:  # pragma: no cover - dependency image gate
        raise RuntimeError("research_safetensors_runtime_unavailable") from exc
    tensors = load(content)
    # safe_open requires a path; the metadata is also stored in a tiny tensor-free
    # envelope in newer safetensors, but byte loading intentionally exposes only
    # tensors. Recover configuration from the reserved metadata tensor below.
    raw_config = tensors.pop("__ananta_config_bytes__", None)
    if raw_config is None:
        raise ValueError("research_checkpoint_config_missing")
    torch = require_torch()
    if raw_config.dtype != torch.uint8:
        raise ValueError("research_checkpoint_config_invalid")
    try:
        config_payload = json.loads(bytes(raw_config.tolist()))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("research_checkpoint_config_invalid") from exc
    config = TinyCausalLmConfig.from_dict(config_payload)
    model = build_model(config)
    model.load_state_dict(tensors, strict=True)
    return model, config


def serialize_portable_checkpoint(
    model,
    config: TinyCausalLmConfig,
    *,
    metadata: dict[str, str],
) -> bytes:
    """Serialize weights and config without pickle/code execution."""

    try:
        from safetensors.torch import save
    except ImportError as exc:  # pragma: no cover - dependency image gate
        raise RuntimeError("research_safetensors_runtime_unavailable") from exc
    torch = require_torch()
    # Clone every entry so tied weights (embedding/output projection) do not
    # share storage inside the safetensors payload. The model constructor ties
    # them again before strict loading.
    tensors = {
        key: value.detach().cpu().contiguous().clone()
        for key, value in model.state_dict().items()
    }
    tensors["__ananta_config_bytes__"] = torch.tensor(
        list(canonical_json(config.to_dict()).encode("utf-8")), dtype=torch.uint8
    )
    return save(
        tensors,
        metadata={
            "schema": "ananta.research-training-checkpoint.v1",
            **{str(key): str(value) for key, value in metadata.items()},
        },
    )


__all__ = [
    "TinyCausalLmConfig",
    "build_model",
    "load_checkpoint",
    "require_torch",
    "serialize_portable_checkpoint",
]
