"""Repeatable target-runtime inference measurements."""

from __future__ import annotations

import statistics
import time
from collections.abc import Sequence
from typing import Any

from worker.training.research.modeling import TinyCausalLmConfig, require_torch
from worker.training.tokenizers.byte_bpe import ByteBpeTokenizer


class TorchInferenceBenchmark:
    def run(
        self,
        *,
        model: Any,
        config: TinyCausalLmConfig,
        tokenizer: ByteBpeTokenizer,
        prompts: Sequence[str],
        repetitions: int,
        maximum_new_tokens: int,
        runtime_digest: str,
        hardware_digest: str,
    ) -> dict[str, Any]:
        torch = require_torch()
        if not prompts or not 1 <= repetitions <= 100 or not 1 <= maximum_new_tokens <= 2048:
            raise ValueError("research_inference_benchmark_limits_invalid")
        if any(not isinstance(prompt, str) or not prompt for prompt in prompts):
            raise ValueError("research_inference_benchmark_prompt_invalid")
        device = next(model.parameters()).device
        model.eval()
        latencies: list[float] = []
        first_token_latencies: list[float] = []
        generated_tokens = 0
        with torch.inference_mode():
            for _ in range(repetitions):
                for prompt in prompts:
                    running = tokenizer.encode(prompt)[-config.context_length :]
                    started = time.perf_counter()
                    first_token = 0.0
                    for index in range(maximum_new_tokens):
                        inputs = torch.tensor([running[-config.context_length :]], dtype=torch.long, device=device)
                        logits = model(inputs)
                        identifier = int(torch.argmax(logits[0, -1]).detach().cpu())
                        running.append(identifier)
                        generated_tokens += 1
                        if index == 0:
                            first_token = time.perf_counter() - started
                    latencies.append(time.perf_counter() - started)
                    first_token_latencies.append(first_token)
        total = sum(latencies)
        peak_memory = (
            int(torch.cuda.max_memory_allocated(device))
            if device.type == "cuda"
            else 0
        )
        return {
            "schema": "ananta.research-training-inference-benchmark.v1",
            "runtime_digest": runtime_digest,
            "hardware_digest": hardware_digest,
            "repetitions": repetitions,
            "samples": len(latencies),
            "generated_tokens": generated_tokens,
            "ttft_p50_ms": statistics.median(first_token_latencies) * 1000,
            "ttft_p95_ms": self._percentile(first_token_latencies, 0.95) * 1000,
            "latency_p50_ms": statistics.median(latencies) * 1000,
            "latency_p95_ms": self._percentile(latencies, 0.95) * 1000,
            "throughput_tokens_s": generated_tokens / max(total, 1e-9),
            "peak_memory_bytes": peak_memory,
            "maximum_stable_context": config.context_length,
            "kv_cache_enabled": False,
        }

    @staticmethod
    def _percentile(values: Sequence[float], quantile: float) -> float:
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * quantile)))
        return ordered[index]


__all__ = ["TorchInferenceBenchmark"]
