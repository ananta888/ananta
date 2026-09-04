"""Backend-neutral tokenizer quality and regression reports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ananta_contracts.research_training import canonical_digest
from worker.training.tokenizers.byte_bpe import ByteBpeTokenizer


class TokenizerEvaluator:
    def evaluate(
        self,
        tokenizer: ByteBpeTokenizer,
        *,
        splits: Mapping[str, Sequence[str]],
    ) -> dict[str, Any]:
        if not splits or set(splits) - {"train", "validation", "test"}:
            raise ValueError("research_tokenizer_eval_splits_invalid")
        split_reports = {
            name: self._split(tokenizer, values)
            for name, values in sorted(splits.items())
        }
        totals = {
            key: sum(int(report[key]) for report in split_reports.values())
            for key in ("bytes", "characters", "tokens", "special_tokens")
        }
        report = {
            "schema": "ananta.research-training-tokenizer-report.v1",
            "vocab_size": tokenizer.vocab_size,
            "splits": split_reports,
            "bytes_per_token": totals["bytes"] / max(1, totals["tokens"]),
            "characters_per_token": totals["characters"] / max(1, totals["tokens"]),
            "unknown_token_rate": 0.0,
            "special_token_rate": totals["special_tokens"] / max(1, totals["tokens"]),
            "roundtrip_verified": True,
        }
        report["report_digest"] = canonical_digest(report)
        return report

    def compare(
        self,
        baseline: Mapping[str, Any],
        candidate: Mapping[str, Any],
        *,
        maximum_bytes_per_token_regression: float,
    ) -> dict[str, Any]:
        threshold = float(maximum_bytes_per_token_regression)
        if threshold < 0:
            raise ValueError("research_tokenizer_regression_threshold_invalid")
        baseline_rate = float(baseline["bytes_per_token"])
        candidate_rate = float(candidate["bytes_per_token"])
        regression = max(0.0, baseline_rate - candidate_rate)
        result = {
            "schema": "ananta.research-training-tokenizer-comparison.v1",
            "baseline_digest": str(baseline["report_digest"]),
            "candidate_digest": str(candidate["report_digest"]),
            "bytes_per_token_regression": regression,
            "maximum_regression": threshold,
            "eligible": regression <= threshold,
            "reason_codes": [] if regression <= threshold else ["research_tokenizer_compression_regression"],
        }
        result["comparison_digest"] = canonical_digest(result)
        return result

    @staticmethod
    def _split(tokenizer: ByteBpeTokenizer, texts: Sequence[str]) -> dict[str, int | float]:
        if not isinstance(texts, Sequence) or isinstance(texts, (str, bytes)) or not texts:
            raise ValueError("research_tokenizer_eval_examples_invalid")
        byte_count = character_count = token_count = special_count = 0
        special_ids = set(tokenizer.special_token_ids.values())
        for text in texts:
            if not isinstance(text, str):
                raise ValueError("research_tokenizer_eval_text_invalid")
            encoded = tokenizer.encode(text)
            if tokenizer.decode(encoded) != text:
                raise ValueError("research_tokenizer_roundtrip_failed")
            byte_count += len(text.encode("utf-8"))
            character_count += len(text)
            token_count += len(encoded)
            special_count += sum(identifier in special_ids for identifier in encoded)
        return {
            "examples": len(texts),
            "bytes": byte_count,
            "characters": character_count,
            "tokens": token_count,
            "special_tokens": special_count,
            "bytes_per_token": byte_count / max(1, token_count),
            "characters_per_token": character_count / max(1, token_count),
        }


__all__ = ["TokenizerEvaluator"]
