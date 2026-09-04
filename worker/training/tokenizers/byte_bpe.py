"""Small deterministic byte-level BPE implementation.

The implementation deliberately has no download path and no dependency on a
model hub.  It is suitable for bounded CPU gates and produces a canonical
artifact that later stages load by digest.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ananta_contracts.research_training import canonical_json
from ananta_contracts.research_training_data import ResearchTokenizerManifestV1

_FORMAT = "ananta.byte-bpe-tokenizer.v1"


def _replace_pair(tokens: list[int], pair: tuple[int, int], replacement: int) -> list[int]:
    result: list[int] = []
    cursor = 0
    while cursor < len(tokens):
        if cursor + 1 < len(tokens) and (tokens[cursor], tokens[cursor + 1]) == pair:
            result.append(replacement)
            cursor += 2
        else:
            result.append(tokens[cursor])
            cursor += 1
    return result


@dataclass(frozen=True, slots=True)
class ByteBpeTokenizer:
    merges: tuple[tuple[int, int], ...]
    special_tokens: tuple[str, ...]

    @property
    def vocab_size(self) -> int:
        return 256 + len(self.merges) + len(self.special_tokens)

    @property
    def special_token_ids(self) -> dict[str, int]:
        offset = 256 + len(self.merges)
        return {token: offset + index for index, token in enumerate(self.special_tokens)}

    def encode(self, text: str) -> list[int]:
        if not isinstance(text, str):
            raise TypeError("research_tokenizer_text_invalid")
        if not text:
            return []
        encoded: list[int] = []
        cursor = 0
        ordered_specials = sorted(self.special_tokens, key=lambda item: (-len(item), item))
        while cursor < len(text):
            matches = [token for token in ordered_specials if text.startswith(token, cursor)]
            if matches:
                encoded.append(self.special_token_ids[matches[0]])
                cursor += len(matches[0])
                continue
            next_special = min(
                (position for token in ordered_specials if (position := text.find(token, cursor)) >= 0),
                default=len(text),
            )
            encoded.extend(self._encode_bytes(text[cursor:next_special].encode("utf-8")))
            cursor = next_special
        return encoded

    def decode(self, token_ids: Sequence[int]) -> str:
        return self._decode(token_ids, errors="strict")

    def decode_generated(self, token_ids: Sequence[int]) -> str:
        """Decode unconstrained model output without accepting unknown IDs."""

        return self._decode(token_ids, errors="replace")

    def _decode(self, token_ids: Sequence[int], *, errors: str) -> str:
        vocabulary = self._vocabulary()
        specials = {identifier: token for token, identifier in self.special_token_ids.items()}
        chunks: list[str] = []
        buffered = bytearray()
        for raw_identifier in token_ids:
            if not isinstance(raw_identifier, int) or isinstance(raw_identifier, bool):
                raise ValueError("research_tokenizer_id_invalid")
            if raw_identifier in specials:
                if buffered:
                    chunks.append(bytes(buffered).decode("utf-8", errors=errors))
                    buffered.clear()
                chunks.append(specials[raw_identifier])
            elif raw_identifier in vocabulary:
                buffered.extend(vocabulary[raw_identifier])
            else:
                raise ValueError("research_tokenizer_id_unknown")
        if buffered:
            chunks.append(bytes(buffered).decode("utf-8", errors=errors))
        return "".join(chunks)

    def serialize(self) -> bytes:
        payload = {
            "schema": _FORMAT,
            "merges": [list(pair) for pair in self.merges],
            "special_tokens": list(self.special_tokens),
        }
        return canonical_json(payload).encode("utf-8")

    @classmethod
    def from_bytes(cls, content: bytes, *, expected_digest: str | None = None) -> ByteBpeTokenizer:
        if expected_digest is not None and hashlib.sha256(content).hexdigest() != expected_digest:
            raise ValueError("research_tokenizer_artifact_digest_mismatch")
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("research_tokenizer_artifact_invalid") from exc
        if not isinstance(payload, dict) or set(payload) != {"schema", "merges", "special_tokens"}:
            raise ValueError("research_tokenizer_artifact_fields_invalid")
        if payload.get("schema") != _FORMAT:
            raise ValueError("research_tokenizer_artifact_schema_invalid")
        raw_merges = payload.get("merges")
        raw_specials = payload.get("special_tokens")
        if not isinstance(raw_merges, list) or not isinstance(raw_specials, list):
            raise ValueError("research_tokenizer_artifact_invalid")
        merges: list[tuple[int, int]] = []
        for index, pair in enumerate(raw_merges):
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or any(not isinstance(item, int) or isinstance(item, bool) for item in pair)
                or any(item < 0 or item >= 256 + index for item in pair)
            ):
                raise ValueError("research_tokenizer_merge_invalid")
            merges.append((pair[0], pair[1]))
        specials = tuple(str(item) for item in raw_specials)
        if any(not item or len(item.encode("utf-8")) > 128 for item in specials) or len(specials) != len(set(specials)):
            raise ValueError("research_tokenizer_special_tokens_invalid")
        return cls(tuple(merges), specials)

    def manifest(self, *, tokenizer_id: str, dataset_manifest_digest: str) -> ResearchTokenizerManifestV1:
        content = self.serialize()
        return ResearchTokenizerManifestV1.from_mapping(
            {
                "schema": ResearchTokenizerManifestV1.SCHEMA,
                "tokenizer_id": tokenizer_id,
                "algorithm": "byte_bpe_v1",
                "artifact_digest": hashlib.sha256(content).hexdigest(),
                "dataset_manifest_digest": dataset_manifest_digest,
                "vocab_size": self.vocab_size,
                "special_tokens": list(self.special_tokens),
                "normalizer": "none_v1",
            }
        )

    def _encode_bytes(self, content: bytes) -> list[int]:
        result = list(content)
        for index, pair in enumerate(self.merges):
            result = _replace_pair(result, pair, 256 + index)
        return result

    def _vocabulary(self) -> dict[int, bytes]:
        vocabulary = {identifier: bytes([identifier]) for identifier in range(256)}
        for index, (left, right) in enumerate(self.merges):
            vocabulary[256 + index] = vocabulary[left] + vocabulary[right]
        return vocabulary


class ByteBpeTrainer:
    def train(
        self,
        texts: Iterable[str],
        *,
        vocab_size: int,
        special_tokens: Sequence[str],
    ) -> ByteBpeTokenizer:
        specials = tuple(str(item) for item in special_tokens)
        if len(specials) != len(set(specials)) or any(not item for item in specials):
            raise ValueError("research_tokenizer_special_tokens_invalid")
        if not 256 + len(specials) <= vocab_size <= 65_536:
            raise ValueError("research_tokenizer_vocab_size_invalid")
        sequences = [list(text.encode("utf-8")) for text in texts if isinstance(text, str) and text]
        if not sequences:
            raise ValueError("research_tokenizer_training_data_empty")
        merges: list[tuple[int, int]] = []
        maximum_merges = vocab_size - 256 - len(specials)
        for index in range(maximum_merges):
            counts: Counter[tuple[int, int]] = Counter(
                pair
                for sequence in sequences
                for pair in zip(sequence, sequence[1:], strict=False)
            )
            if not counts:
                break
            maximum = max(counts.values())
            pair = min(candidate for candidate, count in counts.items() if count == maximum)
            replacement = 256 + index
            sequences = [_replace_pair(sequence, pair, replacement) for sequence in sequences]
            merges.append(pair)
        return ByteBpeTokenizer(tuple(merges), specials)


__all__ = ["ByteBpeTokenizer", "ByteBpeTrainer"]
