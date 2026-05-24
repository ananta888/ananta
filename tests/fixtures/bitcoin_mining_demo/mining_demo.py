#!/usr/bin/env python3
"""Deterministic toy mining demo for citation/evidence tests.

This script is explicitly NOT real Bitcoin mining.
"""

from __future__ import annotations

import hashlib
import json

TOY_INPUT = {
    "version": "00000001",
    "previous_block_hash": "0" * 64,
    "merkle_root": "1" * 64,
    "timestamp": 1700000000,
    "bits": "toy-easy-target",
    "nonce_range": [0, 100000],
}


def _double_sha256(payload_hex: str) -> str:
    raw = bytes.fromhex(payload_hex)
    return hashlib.sha256(hashlib.sha256(raw).digest()).hexdigest()


def _build_header(nonce: int) -> str:
    return (
        TOY_INPUT["version"]
        + TOY_INPUT["previous_block_hash"]
        + TOY_INPUT["merkle_root"]
        + f"{TOY_INPUT['timestamp']:08x}"
        + TOY_INPUT["bits"].encode("utf-8").hex().ljust(64, "0")[:64]
        + f"{nonce:08x}"
    )


def run_demo() -> dict:
    nonce_start, nonce_end = TOY_INPUT["nonce_range"]
    failed_nonce = nonce_start
    failed_hash = _double_sha256(_build_header(failed_nonce))

    valid_nonce = None
    valid_hash = None
    for nonce in range(nonce_start, nonce_end + 1):
        digest = _double_sha256(_build_header(nonce))
        # Artificially easy toy target: hash prefix must be "00".
        if digest.startswith("00"):
            valid_nonce = nonce
            valid_hash = digest
            break

    if valid_nonce is None or valid_hash is None:
        raise RuntimeError("No valid nonce found in configured deterministic range")

    target = "00ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    return {
        "disclaimer": "Toy/demo only. Not real Bitcoin mining.",
        "algorithm": "double_sha256(header)",
        "input": TOY_INPUT,
        "failed_sample": {
            "nonce": failed_nonce,
            "hash": failed_hash,
            "target": target,
            "valid": False,
        },
        "valid_result": {
            "nonce": valid_nonce,
            "hash": valid_hash,
            "target": target,
            "valid": True,
        },
    }


def main() -> None:
    print(json.dumps(run_demo(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
