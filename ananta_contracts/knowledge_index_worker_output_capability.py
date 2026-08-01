"""Transport headers for assignment-bound worker output retrieval."""

from __future__ import annotations

from ananta_contracts.knowledge_index_payload_capability import (
    KNOWLEDGE_INDEX_PAYLOAD_CAPABILITY_HEADER,
    decode_knowledge_index_payload_capability,
    encode_knowledge_index_payload_capability,
)


KNOWLEDGE_INDEX_OUTPUT_CAPABILITY_HEADER = (
    KNOWLEDGE_INDEX_PAYLOAD_CAPABILITY_HEADER
)
KNOWLEDGE_INDEX_OUTPUT_JOB_ID_HEADER = "X-Ananta-Knowledge-Index-Job-ID"
KNOWLEDGE_INDEX_OUTPUT_INDEX_ID_HEADER = "X-Ananta-Knowledge-Index-ID"
KNOWLEDGE_INDEX_OUTPUT_RUN_ID_HEADER = "X-Ananta-Knowledge-Index-Run-ID"
KNOWLEDGE_INDEX_OUTPUT_ROLE_HEADER = "X-Ananta-Knowledge-Index-Output-Role"
KNOWLEDGE_INDEX_OUTPUT_SHA256_HEADER = "X-Ananta-Artifact-SHA256"
KNOWLEDGE_INDEX_OUTPUT_SIZE_HEADER = "X-Ananta-Artifact-Size"
KNOWLEDGE_INDEX_OUTPUT_MEDIA_TYPE_HEADER = "X-Ananta-Artifact-Media-Type"


def encode_knowledge_index_output_capability(manifest):
    return encode_knowledge_index_payload_capability(manifest)


def decode_knowledge_index_output_capability(encoded: str):
    return decode_knowledge_index_payload_capability(encoded)


__all__ = [
    "KNOWLEDGE_INDEX_OUTPUT_CAPABILITY_HEADER",
    "KNOWLEDGE_INDEX_OUTPUT_INDEX_ID_HEADER",
    "KNOWLEDGE_INDEX_OUTPUT_JOB_ID_HEADER",
    "KNOWLEDGE_INDEX_OUTPUT_MEDIA_TYPE_HEADER",
    "KNOWLEDGE_INDEX_OUTPUT_ROLE_HEADER",
    "KNOWLEDGE_INDEX_OUTPUT_RUN_ID_HEADER",
    "KNOWLEDGE_INDEX_OUTPUT_SHA256_HEADER",
    "KNOWLEDGE_INDEX_OUTPUT_SIZE_HEADER",
    "decode_knowledge_index_output_capability",
    "encode_knowledge_index_output_capability",
]
