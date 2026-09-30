"""Shared infrastructure handed from ``service`` to the rendezvous use cases."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from pair_security import PairSecurityAuthority


@dataclass(frozen=True)
class RendezvousRuntime:
    """Store access, clock, configuration and signing authority of one service instance.

    ``service`` builds exactly one runtime and passes it to every use-case
    collaborator (DIP): the collaborators never import ``service`` or the
    configuration module themselves.
    """

    connect: Callable[[], AbstractContextManager[sqlite3.Connection]]
    ensure_schema: Callable[[], None]
    cleanup_expired: Callable[[], None]
    clock: Callable[[], float]
    config: Any
    security_authority: PairSecurityAuthority
    logger: logging.Logger


__all__ = ["RendezvousRuntime"]
