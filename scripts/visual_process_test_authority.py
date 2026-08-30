"""Deterministic Hub pre-authorization policy for isolated VPA tests only.

The fixture supplies an identity to the tests; it does not derive one from
repository content and must never be used as production grounding evidence.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

AUTHORIZED_SOURCE_ID_ENV = "ANANTA_TEST_AUTHORIZED_SOURCE_ID"
AUTHORIZED_SOURCE_IDS_ENV = "ANANTA_TEST_AUTHORIZED_SOURCE_IDS"
HUB_PREAUTHORIZED_TEST_SOURCE_ID = "SRC_9001"


def hub_preauthorized_test_environment(
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a self-contained environment with explicit test authority."""

    environment = dict(os.environ if base is None else base)
    singular = str(environment.get(AUTHORIZED_SOURCE_ID_ENV) or "").strip()
    plural = str(environment.get(AUTHORIZED_SOURCE_IDS_ENV) or "").strip()
    if not singular and not plural:
        environment[AUTHORIZED_SOURCE_ID_ENV] = HUB_PREAUTHORIZED_TEST_SOURCE_ID
    return environment


__all__ = [
    "AUTHORIZED_SOURCE_ID_ENV",
    "AUTHORIZED_SOURCE_IDS_ENV",
    "HUB_PREAUTHORIZED_TEST_SOURCE_ID",
    "hub_preauthorized_test_environment",
]
