"""Rendezvous- und Signaling-Session-Verwaltung.

Persistenter Session-Store über SQLite (shared über mehrere Gunicorn-Worker).

This module is the composition root and public facade of the rendezvous
domain. :class:`PublicRendezvousService` wires one :class:`RendezvousStore`
(SQLite connection, one-time schema initialization, rate limiting, cleanup)
and the use-case collaborators, which receive this infrastructure through one
:class:`RendezvousRuntime` (SRP/DIP):

- ``rendezvous_session_admission``: create and join sessions
- ``rendezvous_session_catalog``: account-scoped session listings
- ``rendezvous_membership_operations``: authenticated membership operations
- ``rendezvous_key_exchange``: Pair key packages and key confirmations
- ``rendezvous_signal_relay``: WebRTC signaling queues
- ``rendezvous_turn_credentials``: coturn REST credentials

The clock and the migration's signal-cursor backfill are constructor
arguments with production defaults (``time.time`` and
:func:`rendezvous_schema.backfill_signal_sequences`); tests build their own
instance with doubles instead of patching this module. The module-level
functions below are bound to the default production instance and keep the
historic ``service.<operation>`` API for ``app`` and other callers.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from pair_security import (  # noqa: F401 - re-exported for app.py and callers
    PairSecurityAuthority,
    normalize_public_media_advertisement,
)
from peer_identity import (  # noqa: F401 - re-exported for app.py and callers
    is_device_peer_id,
    is_membership_capability,
)
from rendezvous_key_exchange import PairKeyExchange
from rendezvous_membership_operations import SessionMembershipOperations
from rendezvous_records import PROTOCOL_IDENTIFIER as _PROTOCOL_IDENTIFIER  # noqa: F401 - compatibility
from rendezvous_runtime import RendezvousRuntime
from rendezvous_schema import backfill_signal_sequences
from rendezvous_session_admission import SessionAdmission
from rendezvous_session_catalog import SessionCatalog
from rendezvous_signal_relay import SignalRelay
from rendezvous_store import Clock, RendezvousStore, SequenceBackfill
from rendezvous_turn_credentials import TurnCredentialIssuer

import config as cfg

log = logging.getLogger(__name__)

_lock = threading.Lock()

# Legacy-Kompatibilität für bestehende Tests/Imports; fachlicher Zustand liegt in SQLite.
_sessions: dict[str, dict[str, Any]] = {}
_participants: dict[str, list[dict[str, Any]]] = {}
_invite_codes: dict[str, str] = {}

_KEY_CONFIRMATION_TTL_SECONDS = 5 * 60
_CAPABILITY_TOMBSTONE_TTL_SECONDS = max(cfg.SESSION_MAX_DURATION_SECONDS, 24 * 60 * 60)
_MAX_SIGNAL_QUEUE = 256
_MAX_SIGNAL_BYTES = 8 * 1024
_MAX_SIGNAL_CURSOR = (1 << 63) - 1
_SECURITY_AUTHORITY = PairSecurityAuthority(cfg.RENDEZVOUS_SECURITY_SIGNING_SECRET)
_SECURITY_AUTHORITY.require_key_id(cfg.RENDEZVOUS_EXPECTED_SIGNING_KEY_ID)


class PublicRendezvousService:
    """One rendezvous service instance: store, runtime and use-case operations.

    Every operation of the historic module API is available as an attribute
    with the same name and signature, so an instance can be used wherever the
    ``service`` module is expected (for example by ``app.create_app``).
    """

    MAX_SIGNAL_QUEUE = _MAX_SIGNAL_QUEUE
    MAX_SIGNAL_BYTES = _MAX_SIGNAL_BYTES
    MAX_SIGNAL_CURSOR = _MAX_SIGNAL_CURSOR

    normalize_public_media_advertisement = staticmethod(normalize_public_media_advertisement)
    is_device_peer_id = staticmethod(is_device_peer_id)
    is_membership_capability = staticmethod(is_membership_capability)

    def __init__(
        self,
        *,
        clock: Clock = time.time,
        backfill_sequences: SequenceBackfill = backfill_signal_sequences,
        config: Any = cfg,
        security_authority: PairSecurityAuthority = _SECURITY_AUTHORITY,
        logger: logging.Logger = log,
    ) -> None:
        self.clock = clock
        self.config = config
        self.store = RendezvousStore(
            config=config,
            clock=clock,
            backfill_sequences=backfill_sequences,
            capability_tombstone_ttl_seconds=_CAPABILITY_TOMBSTONE_TTL_SECONDS,
            logger=logger,
        )
        self.runtime = RendezvousRuntime(
            connect=self.store.connect,
            ensure_schema=self.store.ensure_initialized,
            cleanup_expired=self.store.cleanup_expired,
            clock=clock,
            config=config,
            security_authority=security_authority,
            logger=logger,
        )
        self._bind_store_operations()
        self._bind_use_cases()

    def _bind_store_operations(self) -> None:
        store = self.store
        self.rate_check_with_retry = store.rate_check_with_retry
        self.rate_check = store.rate_check
        self.reset_rate_limits_for_tests = store.reset_rate_limits_for_tests

    def _bind_use_cases(self) -> None:
        runtime = self.runtime
        admission = SessionAdmission(runtime=runtime)
        catalog = SessionCatalog(runtime=runtime)
        membership = SessionMembershipOperations(runtime=runtime)
        key_exchange = PairKeyExchange(runtime=runtime, confirmation_ttl_seconds=_KEY_CONFIRMATION_TTL_SECONDS)
        signal_relay = SignalRelay(runtime=runtime, max_queue=_MAX_SIGNAL_QUEUE, max_cursor=_MAX_SIGNAL_CURSOR)
        turn_credentials = TurnCredentialIssuer(runtime=runtime)

        # --- Session operations ---
        self.create_session = admission.create_session
        self.is_owner_create_recovery = admission.is_owner_create_recovery
        self.is_join_recovery = admission.is_join_recovery
        self.join_session = admission.join_session
        self.list_sessions_for_user = catalog.list_sessions_for_user
        self.list_sessions_for_membership_proofs = catalog.list_sessions_for_membership_proofs
        self.update_session_permissions = membership.update_session_permissions
        self.get_participants = membership.get_participants
        self.touch_participant = membership.touch_participant
        self.leave_session = membership.leave_session
        self.authenticate_session_membership = membership.authenticate_session_membership
        self.set_membership_runtime = membership.set_membership_runtime
        self.revoke_session = membership.revoke_session
        self.is_authorized_participant = membership.is_authorized_participant
        self.get_key_packages = key_exchange.get_key_packages
        self.put_key_confirmation = key_exchange.put_key_confirmation
        self.get_key_confirmation = key_exchange.get_key_confirmation
        # --- WebRTC signaling ---
        self.push_signal = signal_relay.push_signal
        self.poll_signals = signal_relay.poll_signals
        self.consume_signals = signal_relay.consume_signals
        # --- TURN credentials (coturn REST API format) ---
        self.issue_turn_credentials = turn_credentials.issue_turn_credentials

    def reset_state_for_tests(self) -> None:
        """Empty the persistent store (and the legacy module caches) for isolated tests."""
        self.store.reset_for_tests()
        with _lock:
            _sessions.clear()
            _participants.clear()
            _invite_codes.clear()


# --- Default production instance and the historic module API ---

_SERVICE = PublicRendezvousService()
_RUNTIME = _SERVICE.runtime

_db = _SERVICE.store.connect
_ensure_db_initialized = _SERVICE.store.ensure_initialized
_migrate_database = _SERVICE.store.migrate
_cleanup_expired = _SERVICE.store.cleanup_expired
rate_check_with_retry = _rate_check_with_retry = _SERVICE.rate_check_with_retry
_rate_check = _SERVICE.rate_check
reset_state_for_tests = _SERVICE.reset_state_for_tests
reset_rate_limits_for_tests = _SERVICE.reset_rate_limits_for_tests

MAX_SIGNAL_QUEUE = _MAX_SIGNAL_QUEUE
MAX_SIGNAL_BYTES = _MAX_SIGNAL_BYTES
MAX_SIGNAL_CURSOR = _MAX_SIGNAL_CURSOR

# --- Session operations ---

create_session = _SERVICE.create_session
is_owner_create_recovery = _SERVICE.is_owner_create_recovery
is_join_recovery = _SERVICE.is_join_recovery
join_session = _SERVICE.join_session
list_sessions_for_user = _SERVICE.list_sessions_for_user
list_sessions_for_membership_proofs = _SERVICE.list_sessions_for_membership_proofs
update_session_permissions = _SERVICE.update_session_permissions
get_participants = _SERVICE.get_participants
touch_participant = _SERVICE.touch_participant
leave_session = _SERVICE.leave_session
authenticate_session_membership = _SERVICE.authenticate_session_membership
set_membership_runtime = _SERVICE.set_membership_runtime
revoke_session = _SERVICE.revoke_session
is_authorized_participant = _SERVICE.is_authorized_participant
get_key_packages = _SERVICE.get_key_packages
put_key_confirmation = _SERVICE.put_key_confirmation
get_key_confirmation = _SERVICE.get_key_confirmation

# --- WebRTC signaling ---

push_signal = _SERVICE.push_signal
poll_signals = _SERVICE.poll_signals
consume_signals = _SERVICE.consume_signals

# --- TURN credentials (coturn REST API format) ---

issue_turn_credentials = _SERVICE.issue_turn_credentials


_ensure_db_initialized()
