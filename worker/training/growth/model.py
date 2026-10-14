"""Optional Torch reference transformer with explicit growth-compatible projections.

This reference architecture is deliberately bounded. Generic HF architectures
are rejected by the transformation ports rather than silently rewritten.
"""

from __future__ import annotations

from ananta_contracts.adaptive_growth import GrowthModelConfig, SUPPORTED_ARCHITECTURE
from worker.training.research.modeling import require_torch


def build_model(config: GrowthModelConfig):
    torch = require_torch()
    nn = torch.nn

    class Expert(nn.Module):
        def __init__(self):
            super().__init__()
            self.up = nn.Linear(config.width, 4 * config.width)
            self.down = nn.Linear(4 * config.width, config.width)

        def forward(self, value):
            return self.down(torch.nn.functional.gelu(self.up(value)))

    class Block(nn.Module):
        def __init__(self):
            super().__init__()
            self.norm1, self.norm2 = nn.LayerNorm(config.width), nn.LayerNorm(config.width)
            self.query, self.key, self.value = (nn.Linear(config.width, config.width) for _ in range(3))
            self.projection = nn.Linear(config.width, config.width)
            self.experts = nn.ModuleList(Expert() for _ in range(config.experts))
            self.router = nn.Linear(config.width, config.experts, bias=False)
            nn.init.zeros_(self.router.weight)
            self.last_utilization = None
            self.balance_loss = None

        def forward(self, hidden):
            batch, length, width = hidden.shape
            normalized = self.norm1(hidden)

            def heads(projection):
                return projection(normalized).view(batch, length, config.heads, width // config.heads).transpose(1, 2)

            attended = (
                torch.nn.functional.scaled_dot_product_attention(
                    heads(self.query),
                    heads(self.key),
                    heads(self.value),
                    is_causal=True,
                )
                .transpose(1, 2)
                .reshape(batch, length, width)
            )
            hidden = hidden + self.projection(attended)
            normalized = self.norm2(hidden)
            if config.experts == 1:
                self.last_utilization = torch.ones(1, device=hidden.device)
                self.balance_loss = hidden.new_zeros(())
                return hidden + self.experts[0](normalized)
            probabilities = self.router(normalized).softmax(-1)
            weights, indices = probabilities.topk(config.top_k, dim=-1)
            weights = weights / weights.sum(-1, keepdim=True)
            routed = torch.zeros_like(hidden)
            counts = []
            # Execute only selected token/expert pairs, not every expert for
            # every token. Differentiable index updates preserve router grads.
            for index, expert in enumerate(self.experts):
                mask = indices == index
                token_mask = mask.any(-1)
                coefficient = (weights * mask).sum(-1)
                routed[token_mask] += expert(normalized[token_mask]) * coefficient[token_mask].unsqueeze(-1)
                counts.append(mask.sum())
            utilization = torch.stack(counts).float() / (batch * length * config.top_k)
            self.last_utilization = utilization.detach()
            self.balance_loss = config.experts * (probabilities.mean((0, 1)) * utilization.detach()).sum()
            return hidden + routed

    class GrowthTransformer(nn.Module):
        architecture = SUPPORTED_ARCHITECTURE

        def __init__(self):
            super().__init__()
            self.config = config
            self.embedding = nn.Embedding(config.vocab_size, config.width)
            self.position = nn.Embedding(config.context_length, config.width)
            self.blocks = nn.ModuleList(Block() for _ in range(config.depth))
            self.norm = nn.LayerNorm(config.width)
            self.output = nn.Linear(config.width, config.vocab_size, bias=False)

        def forward(self, tokens):
            if tokens.ndim != 2 or not 1 <= tokens.shape[1] <= config.context_length:
                raise ValueError("growth_input_shape_invalid")
            positions = torch.arange(tokens.shape[1], device=tokens.device)
            hidden = self.embedding(tokens) + self.position(positions)
            for block in self.blocks:
                hidden = block(hidden)
            return self.output(self.norm(hidden))

        def auxiliary_loss(self):
            return sum(block.balance_loss for block in self.blocks)

    return GrowthTransformer()
