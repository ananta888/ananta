"""Bounded on-policy research engine with pluggable reward calculation."""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from ananta_contracts.research_training_rl import ResearchRlConfigV1
from worker.training.research.modeling import TinyCausalLmConfig, require_torch
from worker.training.research.reward import RewardProvider
from worker.training.research.training_engine import seed_everything
from worker.training.tokenizers.byte_bpe import ByteBpeTokenizer


class TorchRlEngine:
    def train(
        self,
        *,
        model: Any,
        model_config: TinyCausalLmConfig,
        tokenizer: ByteBpeTokenizer,
        records: Sequence[Mapping[str, Any]],
        config: ResearchRlConfigV1,
        reward_provider: RewardProvider,
    ) -> dict[str, float]:
        torch = require_torch()
        if not records:
            raise ValueError("research_rl_records_empty")
        seed_everything(config.seed)
        optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate)
        rewards: list[float] = []
        outputs: set[tuple[int, ...]] = set()
        for step in range(config.maximum_steps):
            record = records[step % len(records)]
            prompt = str(record.get("prompt") or "")
            expected = str(record.get("expected") or record.get("response") or "")
            if not prompt or not expected:
                raise ValueError("research_rl_record_invalid")
            prompt_ids = tokenizer.encode(prompt)[-model_config.context_length :]
            sample_rewards: list[float] = []
            sample_log_probabilities: list[Any] = []
            for sample_index in range(config.samples_per_prompt):
                torch.manual_seed(config.seed + step * config.samples_per_prompt + sample_index)
                generated, log_probability = self._sample(
                    model,
                    prompt_ids,
                    maximum_new_tokens=config.maximum_new_tokens,
                    context_length=model_config.context_length,
                    temperature=config.temperature,
                )
                outputs.add(tuple(generated))
                prediction = tokenizer.decode_generated(generated)
                reward = float(reward_provider.score(prediction=prediction, expected=expected))
                if abs(reward) > config.reward.maximum_absolute_reward:
                    raise ValueError("research_reward_bound_exceeded")
                sample_rewards.append(reward)
                sample_log_probabilities.append(log_probability)
            baseline = (
                sum(sample_rewards) / len(sample_rewards)
                if config.algorithm == "group_relative_v1"
                else 0.0
            )
            loss = -sum(
                (reward - baseline) * probability
                for reward, probability in zip(sample_rewards, sample_log_probabilities, strict=True)
            ) / len(sample_rewards)
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            rewards.extend(sample_rewards)
        mean = statistics.fmean(rewards)
        variance = statistics.pvariance(rewards) if len(rewards) > 1 else 0.0
        return {
            "reward_mean": mean,
            "reward_variance": variance,
            "reward_min": min(rewards),
            "reward_max": max(rewards),
            "unique_rollout_rate": len(outputs) / len(rewards),
            "collapse_indicator": float(len(outputs) == 1 and len(rewards) > 1),
            "optimizer_steps": float(config.maximum_steps),
        }

    @staticmethod
    def _sample(
        model: Any,
        prompt_ids: list[int],
        *,
        maximum_new_tokens: int,
        context_length: int,
        temperature: float,
    ) -> tuple[list[int], Any]:
        torch = require_torch()
        device = next(model.parameters()).device
        running = list(prompt_ids)
        generated: list[int] = []
        log_probabilities: list[Any] = []
        for _ in range(maximum_new_tokens):
            inputs = torch.tensor([running[-context_length:]], dtype=torch.long, device=device)
            logits = model(inputs)[0, -1] / temperature
            distribution = torch.distributions.Categorical(logits=logits)
            token = distribution.sample()
            log_probabilities.append(distribution.log_prob(token))
            identifier = int(token.detach().cpu())
            generated.append(identifier)
            running.append(identifier)
        return generated, torch.stack(log_probabilities).sum()


__all__ = ["TorchRlEngine"]
