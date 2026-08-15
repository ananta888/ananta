from __future__ import annotations

import ast
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlmodel import SQLModel, create_engine

from agent.db_models.workflow_runtime import WorkflowAuthorizationGrantDB
from agent.services.workflow_authorization_grant_service import (
    InMemoryWorkflowAuthorizationGrantService,
    SQLAlchemyWorkflowAuthorizationGrantService,
    WorkflowAuthorizationGrant,
    WorkflowAuthorizationGrantConflict,
    workflow_authorization_grant_digest,
)
from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.security import (
    HmacKeyRing,
    RuntimeAuthorizationEnvelope,
)
from agent.services.workflow_transition_authorization_grant import (
    WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_EFFECT_SCHEMA,
    RetainedEd25519AuthorizationEnvelopeIntegrityVerifier,
    WorkflowTransitionAuthorizationGrantError,
    WorkflowTransitionAuthorizationGrantExecutor,
    WorkflowTransitionAuthorizationGrantObserver,
    assert_active_workflow_transition_authorization_grant_proof,
    assert_current_workflow_transition_authorization_grant_validity,
    assert_durable_workflow_transition_authorization_grant_proof,
    build_workflow_transition_authorization_grant_effect,
    workflow_transition_authorization_grant_envelope_id,
    workflow_transition_authorization_grant_idempotency_key,
    workflow_transition_authorization_grant_nonce,
)
from agent.services.workflow_transition_effect_execution import (
    EffectAlreadyApplied,
    EffectApplied,
    EffectExecutable,
    EffectQuarantine,
    EffectRetry,
    WorkflowTransitionEffectAttempt,
    WorkflowTransitionEffectObservation,
)
from agent.services.workflow_transition_effect_proofs import (
    WorkflowTransitionEffectProofError,
)
from agent.services.workflow_transition_outbox import (
    EFFECT_AUTHORIZATION_GRANT,
    EFFECT_STATE_APPLIED,
    EFFECT_STATE_APPLYING,
    TRANSITION_KIND_ADVANCE,
    TRANSITION_RUNTIME_LANGGRAPH,
    TRANSITION_RUNTIME_NATIVE,
    TRANSITION_STATE_APPLYING,
    WorkflowTransition,
    WorkflowTransitionEffect,
    thaw_json,
    workflow_transition_effect_fingerprint,
    workflow_transition_effect_id,
    workflow_transition_effect_result_digest,
    workflow_transition_effect_result_envelope,
    workflow_transition_id,
)
from ananta_contracts.runtime_authorization_crypto import (
    ED25519_ALGORITHM,
    Ed25519SigningKeyRing,
    Ed25519VerificationKeyRing,
)

_TABLES = [WorkflowAuthorizationGrantDB.__table__]
_KNOWN_TRANSITION_ID = "wft-42cbab5ae7eb7b02ce8946b8e316dd3bad3f84700565ac5c1e4af2da1b043a0a"
_KNOWN_ENVELOPE_ID = "wftag-75a3b96d95de9d7b76a354e07c21a9f3aae5256f864e62ed8aef0b17802b4466"
_KNOWN_NONCE = "wftagn-40f0f928cfa0aa1f9c96fdc47e447ae1a180cac9c56d0360d5147e41d3111707"
_KNOWN_ENVELOPE_DIGEST = "2ce731f5a03220ecff90368c3e4f6fde7e4fae4e7438c23171e23f98b570e8d0"
_KNOWN_IDEMPOTENCY_KEY = "wftagi-919e8e2180051bddcd8c260697956de4731d1ce9433cab0108e0fbe545a397ca"
_KNOWN_EFFECT_ID = "wfx-4a4287f63b2b74e7fd9dad81dc9be70457bde50a844b94dca4fa401d1560e649"
_KNOWN_EFFECT_BYTES = '{"applied_generation":0,"created_at":1000.0,"effect_id":"wfx-4a4287f63b2b74e7fd9dad81dc9be70457bde50a844b94dca4fa401d1560e649","idempotency_key":"wftagi-919e8e2180051bddcd8c260697956de4731d1ce9433cab0108e0fbe545a397ca","kind":"authorization_grant","ordinal":1,"payload":{"effect_id":"wfx-4a4287f63b2b74e7fd9dad81dc9be70457bde50a844b94dca4fa401d1560e649","effect_ordinal":1,"envelope":{"allowed_artifacts":["artifact://input-a"],"allowed_tools":["artifact.read","provider.write"],"budgets":{"provider_attempts":2,"tokens":1000},"envelope_id":"wftag-75a3b96d95de9d7b76a354e07c21a9f3aae5256f864e62ed8aef0b17802b4466","expires_at":1300.0,"issued_at":1000.0,"key_id":"grant-key-v1","nonce":"wftagn-40f0f928cfa0aa1f9c96fdc47e447ae1a180cac9c56d0360d5147e41d3111707","plan_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","policy_version":"policy-v1","run_id":"run-a","schema":"ananta.runtime_authorization.v1","signature":"YFoRu2LOEYHJ94BAFJO/bfJT3svQLaF7IE5Ac8U/2bNn1ZGhnyAhLRAscgdt4R9kr9fczwp+NR59WnIalVJ4Dw==","step_id":"step-a","tenant_id":"tenant-a","workflow_id":"workflow-a"},"envelope_digest":"2ce731f5a03220ecff90368c3e4f6fde7e4fae4e7438c23171e23f98b570e8d0","runtime_id":"ananta-native","schema":"ananta.workflow_transition_authorization_grant_effect.v1","signature_algorithm":"ed25519","transition_id":"wft-42cbab5ae7eb7b02ce8946b8e316dd3bad3f84700565ac5c1e4af2da1b043a0a","ttl_seconds":300.0},"payload_digest":"498d71eb372bd7f37029d172fad615df7d8c20da5af441e81dfd01ecbf7e9dd7","result_digest":"","result_payload":{},"revision":1,"schema":"ananta.workflow-transition-effect.v1","state":"planned","transition_id":"wft-42cbab5ae7eb7b02ce8946b8e316dd3bad3f84700565ac5c1e4af2da1b043a0a","updated_at":1000.0}'
_VALID_PROVIDER_BINDING_ID = f"provider-binding:{'a' * 64}"


@dataclass
class _GrantCase:
    name: str
    store: Any
    engine: sa.Engine | None = None


@pytest.fixture(params=("memory", "sql"))
def grant_authority(request: pytest.FixtureRequest, tmp_path: Path) -> _GrantCase:
    if request.param == "memory":
        return _GrantCase("memory", InMemoryWorkflowAuthorizationGrantService())
    engine = create_engine(
        f"sqlite:///{tmp_path / 'transition-authorization-grants.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine, tables=_TABLES)
    request.addfinalizer(engine.dispose)
    return _GrantCase(
        "sql",
        SQLAlchemyWorkflowAuthorizationGrantService(engine),
        engine,
    )


class _Heartbeat:
    def __init__(self) -> None:
        self.calls = 0

    def heartbeat(self) -> None:
        self.calls += 1


class _HardCrash(BaseException):
    pass


class _FaultAuthority:
    def __init__(self, delegate: Any, mode: str) -> None:
        self.delegate = delegate
        self.mode = mode
        self.commit_calls = 0

    def get(self, **binding: Any) -> WorkflowAuthorizationGrant | None:
        return self.delegate.get(**binding)

    def commit_transition_grant(
        self,
        envelope: RuntimeAuthorizationEnvelope,
        *,
        recorded_at: float,
    ) -> WorkflowAuthorizationGrant | None:
        self.commit_calls += 1
        if self.mode == "exception_before_commit":
            raise RuntimeError("injected pre-commit failure")
        if self.mode == "return_without_commit":
            return None
        result = self.delegate.commit_transition_grant(
            envelope,
            recorded_at=recorded_at,
        )
        if self.mode == "exception_after_commit":
            raise RuntimeError("injected lost response")
        if self.mode == "hard_crash_after_commit":
            raise _HardCrash("injected hard crash")
        return result


class _StaticRead:
    def __init__(self, grant: WorkflowAuthorizationGrant | None) -> None:
        self.grant = grant

    def get(self, **_binding: Any) -> WorkflowAuthorizationGrant | None:
        return self.grant


class _CountingRead:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.calls = 0

    def get(self, **binding: Any) -> WorkflowAuthorizationGrant | None:
        self.calls += 1
        return self.delegate.get(**binding)


class _HmacHistoricalIntegrity:
    signature_algorithm = "hmac-sha256"

    def verify_issued(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _RecordingVerifier:
    def __init__(self, delegate: Any, events: list[str]) -> None:
        self.delegate = delegate
        self.events = events

    @property
    def signature_algorithm(self) -> str:
        return self.delegate.signature_algorithm

    def verify(self, **arguments: Any) -> None:
        self.events.append("verify")
        self.delegate.verify(**arguments)


class _RecordingAuthority:
    def __init__(self, delegate: Any, events: list[str]) -> None:
        self.delegate = delegate
        self.events = events

    def get(self, **binding: Any) -> WorkflowAuthorizationGrant | None:
        self.events.append("read")
        return self.delegate.get(**binding)

    def commit_transition_grant(
        self,
        envelope: RuntimeAuthorizationEnvelope,
        *,
        recorded_at: float,
    ) -> WorkflowAuthorizationGrant:
        self.events.append("commit")
        return self.delegate.commit_transition_grant(
            envelope,
            recorded_at=recorded_at,
        )


class _RevokeAfterCommitAuthority:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate

    def get(self, **binding: Any) -> WorkflowAuthorizationGrant | None:
        return self.delegate.get(**binding)

    def commit_transition_grant(
        self,
        envelope: RuntimeAuthorizationEnvelope,
        *,
        recorded_at: float,
    ) -> WorkflowAuthorizationGrant:
        result = self.delegate.commit_transition_grant(
            envelope,
            recorded_at=recorded_at,
        )
        self.delegate.revoke(
            envelope.envelope_id,
            reason_code="revoked_at_commit_boundary",
            expected_revision=1,
        )
        return result


class _CommitRevokeThenFailVerifier:
    def __init__(
        self,
        *,
        delegate: Any,
        authority: Any,
        envelope: RuntimeAuthorizationEnvelope,
    ) -> None:
        self.delegate = delegate
        self.authority = authority
        self.envelope = envelope
        self.calls = 0

    @property
    def signature_algorithm(self) -> str:
        return self.delegate.signature_algorithm

    def verify(self, **arguments: Any) -> None:
        self.calls += 1
        self.delegate.verify(**arguments)
        self.authority.commit_transition_grant(
            self.envelope,
            recorded_at=self.envelope.issued_at,
        )
        self.authority.revoke(
            self.envelope.envelope_id,
            reason_code="concurrent_current_revocation",
            expected_revision=1,
        )
        raise RuntimeError("current authority changed after concurrent commit")


class _TransientRereadAuthority:
    def __init__(self, delegate: Any, *, commit_raises: bool = False) -> None:
        self.delegate = delegate
        self.commit_raises = commit_raises
        self.commit_calls = 0
        self.read_calls = 0

    def get(self, **binding: Any) -> WorkflowAuthorizationGrant | None:
        self.read_calls += 1
        if self.read_calls == 2:
            raise OSError("transient authoritative read failure")
        return self.delegate.get(**binding)

    def commit_transition_grant(
        self,
        envelope: RuntimeAuthorizationEnvelope,
        *,
        recorded_at: float,
    ) -> WorkflowAuthorizationGrant:
        self.commit_calls += 1
        result = self.delegate.commit_transition_grant(
            envelope,
            recorded_at=recorded_at,
        )
        if self.commit_raises:
            raise RuntimeError("injected lost response")
        return result


class _UnavailableRead:
    def __init__(self) -> None:
        self.commit_calls = 0

    def get(self, **_binding: Any) -> WorkflowAuthorizationGrant | None:
        raise OSError("grant store unavailable")

    def commit_transition_grant(
        self,
        _envelope: RuntimeAuthorizationEnvelope,
        *,
        recorded_at: float,
    ) -> WorkflowAuthorizationGrant:
        del recorded_at
        self.commit_calls += 1
        raise AssertionError("unavailable read must not authorize a commit")


class _ConflictRead(_UnavailableRead):
    def get(self, **_binding: Any) -> WorkflowAuthorizationGrant | None:
        raise WorkflowAuthorizationGrantConflict("workflow_authorization_grant_projection_conflict")


def _signer(*, key_id: str = "grant-key-v1", seed: int = 1) -> Ed25519SigningKeyRing:
    return Ed25519SigningKeyRing(
        {key_id: base64.b64encode(bytes([seed]) * 32)},
        active_key_id=key_id,
    )


def _provider_binding() -> dict[str, Any]:
    return {
        "binding_id": _VALID_PROVIDER_BINDING_ID,
        "provider_id": "openai",
        "model_id": "gpt-4o",
        "endpoint_identity": "https://api.openai.com/v1/chat/completions",
    }


def _provider_attempt(*, maximum_attempts: Any = 2) -> dict[str, Any]:
    return {
        "profile_id": "profile-a",
        **_provider_binding(),
        "maximum_attempts": maximum_attempts,
    }


def _plan(
    *,
    signer: Ed25519SigningKeyRing | None = None,
    identity_key: str = "authorization-grant-transition-a",
    runtime_id: str = TRANSITION_RUNTIME_NATIVE,
    step_id: str = "step-a",
    ttl_seconds: float = 300.0,
) -> tuple[WorkflowTransition, WorkflowTransitionEffect]:
    key_ring = signer or _signer()
    transition_id = workflow_transition_id(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=runtime_id,
        kind=TRANSITION_KIND_ADVANCE,
        identity_key=identity_key,
    )
    effect = build_workflow_transition_authorization_grant_effect(
        signing_key_ring=key_ring,
        transition_id=transition_id,
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=runtime_id,
        ordinal=1,
        step_id=step_id,
        plan_hash="a" * 64,
        policy_version="policy-v1",
        allowed_tools=("artifact.read", "provider.write"),
        allowed_artifacts=("artifact://input-a",),
        budgets={"provider_attempts": 2, "tokens": 1000},
        ttl_seconds=ttl_seconds,
        planned_at=1_000.0,
    )
    transition = WorkflowTransition.build(
        transition_id=transition_id,
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=runtime_id,
        kind=TRANSITION_KIND_ADVANCE,
        request_payload={"request_id": identity_key},
        effects=(effect,),
        expected_revision=0,
        expected_checkpoint_ref="checkpoint-0",
        created_at=1_000.0,
    )
    return transition, effect


def _claimed(transition: WorkflowTransition, *, generation: int) -> WorkflowTransition:
    return replace(
        transition,
        state=TRANSITION_STATE_APPLYING,
        claim_owner=f"owner-{generation}",
        claim_generation=generation,
        attempt_count=generation,
        claim_expires_at=1_500.0 + generation,
        last_heartbeat_at=1_000.0 + generation,
        revision=transition.revision + generation,
        updated_at=1_000.0 + generation,
    )


def _with_effect(
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
) -> WorkflowTransition:
    return replace(
        transition,
        effect_fingerprint=workflow_transition_effect_fingerprint((effect,)),
    )


def _applying(
    effect: WorkflowTransitionEffect,
    *,
    generation: int,
) -> WorkflowTransitionEffect:
    return replace(
        effect,
        state=EFFECT_STATE_APPLYING,
        applied_generation=generation,
        revision=effect.revision + 1,
        updated_at=1_000.0 + generation,
    )


def _observation(
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
) -> WorkflowTransitionEffectObservation:
    return WorkflowTransitionEffectObservation(
        transition,
        effect,
        transition.claim_generation,
    )


def _attempt(
    transition: WorkflowTransition,
    effect: WorkflowTransitionEffect,
) -> WorkflowTransitionEffectAttempt:
    return WorkflowTransitionEffectAttempt(
        transition,
        effect,
        transition.claim_generation,
    )


def _envelope(effect: WorkflowTransitionEffect) -> RuntimeAuthorizationEnvelope:
    return RuntimeAuthorizationEnvelope.from_mapping(thaw_json(effect.payload)["envelope"])


def _historical(
    signer: Ed25519SigningKeyRing,
) -> RetainedEd25519AuthorizationEnvelopeIntegrityVerifier:
    return RetainedEd25519AuthorizationEnvelopeIntegrityVerifier(signer.public_keys())


def _observer(
    store: Any,
    signer: Ed25519SigningKeyRing,
    *,
    now: float = 1_050.0,
    current: Any | None = None,
) -> WorkflowTransitionAuthorizationGrantObserver:
    return WorkflowTransitionAuthorizationGrantObserver(
        reads=store,
        historical_integrity=_historical(signer),
        current_verifier=current or signer.verification_key_ring(),
        clock=lambda: now,
    )


def _executor(
    store: Any,
    signer: Ed25519SigningKeyRing,
    *,
    now: float = 1_050.0,
    current: Any | None = None,
) -> WorkflowTransitionAuthorizationGrantExecutor:
    return WorkflowTransitionAuthorizationGrantExecutor(
        authority=store,
        historical_integrity=_historical(signer),
        current_verifier=current or signer.verification_key_ring(),
        clock=lambda: now,
    )


def _exact(effect: WorkflowTransitionEffect) -> dict[str, str]:
    envelope = _envelope(effect)
    return {
        "tenant_id": envelope.tenant_id,
        "workflow_id": envelope.workflow_id,
        "run_id": envelope.run_id,
        "step_id": envelope.step_id,
        "envelope_id": envelope.envelope_id,
    }


def _applied_effect(
    effect: WorkflowTransitionEffect,
    result: EffectApplied | EffectAlreadyApplied,
    *,
    generation: int,
    mode: str,
) -> WorkflowTransitionEffect:
    envelope = workflow_transition_effect_result_envelope(
        mode=mode,
        result_payload=result.result_payload,
        proof_payload=result.proof_payload,
        stage_attempt_count=generation,
    )
    return replace(
        effect,
        state=EFFECT_STATE_APPLIED,
        applied_generation=generation,
        result_payload=envelope,
        result_digest=workflow_transition_effect_result_digest(envelope),
        revision=effect.revision + 2,
        updated_at=1_010.0 + generation,
    )


def _replace_persisted_evidence(
    effect: WorkflowTransitionEffect,
    *,
    result_payload: dict[str, Any] | None = None,
    proof_payload: dict[str, Any] | None = None,
) -> WorkflowTransitionEffect:
    envelope = thaw_json(effect.result_payload)
    if result_payload is not None:
        envelope["effect_result"] = result_payload
    if proof_payload is not None:
        envelope["effect_proof"] = proof_payload
    return replace(
        effect,
        result_payload=envelope,
        result_digest=workflow_transition_effect_result_digest(envelope),
    )


def _row_count(case: _GrantCase) -> int:
    if case.name == "memory":
        return len(case.store._values)
    assert case.engine is not None
    with case.engine.connect() as connection:
        return int(
            connection.scalar(sa.select(sa.func.count()).select_from(WorkflowAuthorizationGrantDB.__table__)) or 0
        )


def _rebuild_effect(
    effect: WorkflowTransitionEffect,
    payload: dict[str, Any],
) -> WorkflowTransitionEffect:
    return WorkflowTransitionEffect.build(
        transition_id=effect.transition_id,
        ordinal=effect.ordinal,
        kind=effect.kind,
        idempotency_key=effect.idempotency_key,
        payload=payload,
        created_at=effect.created_at,
    )


def _effect_for_signed_envelope(
    *,
    transition: WorkflowTransition,
    envelope: RuntimeAuthorizationEnvelope,
) -> WorkflowTransitionEffect:
    envelope_digest = workflow_authorization_grant_digest(envelope)
    idempotency_key = workflow_transition_authorization_grant_idempotency_key(
        envelope_id=envelope.envelope_id,
        envelope_digest=envelope_digest,
    )
    effect_id = workflow_transition_effect_id(
        transition_id=transition.transition_id,
        ordinal=1,
        kind=EFFECT_AUTHORIZATION_GRANT,
        idempotency_key=idempotency_key,
    )
    return WorkflowTransitionEffect.build(
        transition_id=transition.transition_id,
        ordinal=1,
        kind=EFFECT_AUTHORIZATION_GRANT,
        idempotency_key=idempotency_key,
        payload={
            "schema": WORKFLOW_TRANSITION_AUTHORIZATION_GRANT_EFFECT_SCHEMA,
            "transition_id": transition.transition_id,
            "effect_id": effect_id,
            "runtime_id": transition.runtime_id,
            "effect_ordinal": 1,
            "signature_algorithm": ED25519_ALGORITHM,
            "ttl_seconds": 300.0,
            "envelope_digest": envelope_digest,
            "envelope": envelope.to_dict(),
        },
        created_at=transition.created_at,
    )


def test_grant_effect_planning_is_ed25519_only_acyclic_and_byte_deterministic() -> None:
    from agent.services.workflow_runtime import security as security_module

    signer = _signer()
    with (
        patch.object(
            security_module.time,
            "time",
            side_effect=AssertionError("hidden clock used"),
        ),
        patch.object(
            security_module.uuid,
            "uuid4",
            side_effect=AssertionError("random identifier used"),
        ),
    ):
        transition, first = _plan(signer=signer)
        _again, second = _plan(signer=signer)
        store = InMemoryWorkflowAuthorizationGrantService(
            clock=lambda: (_ for _ in ()).throw(AssertionError("hidden grant-store clock used"))
        )
        claimed = _claimed(transition, generation=1)
        executable = _observer(store, signer).observe_or_adopt(
            _observation(claimed, first),
            heartbeat=_Heartbeat(),
        )
        assert type(executable) is EffectExecutable
        executed = _executor(store, signer).execute(
            _attempt(claimed, _applying(first, generation=1)),
            executable=executable,
            heartbeat=_Heartbeat(),
        )
        assert type(executed) is EffectApplied

    envelope = _envelope(first)
    raw = thaw_json(first.payload)
    assert first.to_dict() == second.to_dict()
    assert transition.transition_id == _KNOWN_TRANSITION_ID
    assert envelope.envelope_id == _KNOWN_ENVELOPE_ID
    assert envelope.nonce == _KNOWN_NONCE
    assert raw["envelope_digest"] == _KNOWN_ENVELOPE_DIGEST
    assert first.idempotency_key == _KNOWN_IDEMPOTENCY_KEY
    assert first.effect_id == _KNOWN_EFFECT_ID
    assert canonical_json(first.to_dict()) == _KNOWN_EFFECT_BYTES
    assert envelope.envelope_id == workflow_transition_authorization_grant_envelope_id(
        transition_id=transition.transition_id,
        ordinal=first.ordinal,
    )
    assert envelope.nonce == workflow_transition_authorization_grant_nonce(
        transition_id=transition.transition_id,
        ordinal=first.ordinal,
    )
    assert first.idempotency_key == workflow_transition_authorization_grant_idempotency_key(
        envelope_id=envelope.envelope_id,
        envelope_digest=raw["envelope_digest"],
    )
    assert envelope.issued_at == transition.created_at == first.created_at
    assert envelope.expires_at == 1_300.0

    with pytest.raises(WorkflowTransitionAuthorizationGrantError):
        build_workflow_transition_authorization_grant_effect(
            signing_key_ring=HmacKeyRing(
                {"legacy": "x" * 32},
                active_key_id="legacy",
            ),
            transition_id=transition.transition_id,
            tenant_id=transition.tenant_id,
            workflow_id=transition.workflow_id,
            run_id=transition.run_id,
            runtime_id=transition.runtime_id,
            ordinal=1,
            step_id="step-a",
            plan_hash="a" * 64,
            policy_version="policy-v1",
            ttl_seconds=300.0,
            planned_at=1_000.0,
        )


@pytest.mark.parametrize(
    "override",
    (
        {"planned_at": True},
        {"planned_at": float("nan")},
        {"planned_at": 10**400},
        {"ttl_seconds": 0},
        {"ttl_seconds": float("inf")},
        {"allowed_tools": "artifact.read"},
        {"budgets": False},
        {"budgets": 0},
        {"budgets": ""},
        {"budgets": []},
        {"budgets": {"tokens": float("nan")}},
        {"budgets": {"tokens": 10**400}},
        {"policy_version": " bad "},
    ),
)
def test_grant_effect_builder_rejects_coercible_or_nonfinite_inputs(
    override: dict[str, Any],
) -> None:
    transition, _effect = _plan()
    arguments: dict[str, Any] = {
        "signing_key_ring": _signer(),
        "transition_id": transition.transition_id,
        "tenant_id": transition.tenant_id,
        "workflow_id": transition.workflow_id,
        "run_id": transition.run_id,
        "runtime_id": transition.runtime_id,
        "ordinal": 1,
        "step_id": "step-a",
        "plan_hash": "a" * 64,
        "policy_version": "policy-v1",
        "ttl_seconds": 300.0,
        "planned_at": 1_000.0,
    }
    arguments.update(override)
    with pytest.raises(WorkflowTransitionAuthorizationGrantError):
        build_workflow_transition_authorization_grant_effect(**arguments)


def test_grant_effect_builder_bounds_nested_json_and_cycles() -> None:
    transition, _effect = _plan()
    cyclic: dict[str, Any] = {}
    cyclic["self"] = cyclic
    deep: dict[str, Any] = {"binding_id": "provider-a"}
    for _ in range(40):
        deep = {"nested": deep}
    for bindings in ((cyclic,), (deep,)):
        with pytest.raises(WorkflowTransitionAuthorizationGrantError):
            build_workflow_transition_authorization_grant_effect(
                signing_key_ring=_signer(),
                transition_id=transition.transition_id,
                tenant_id=transition.tenant_id,
                workflow_id=transition.workflow_id,
                run_id=transition.run_id,
                runtime_id=transition.runtime_id,
                ordinal=1,
                step_id="step-a",
                plan_hash="a" * 64,
                policy_version="policy-v1",
                allowed_provider_bindings=bindings,
                ttl_seconds=300.0,
                planned_at=1_000.0,
            )


def test_builder_accepts_canonical_provider_binding_and_attempt_plan() -> None:
    signer = _signer()
    transition, _effect = _plan(signer=signer)
    binding = _provider_binding()
    attempt = _provider_attempt()
    effect = build_workflow_transition_authorization_grant_effect(
        signing_key_ring=signer,
        transition_id=transition.transition_id,
        tenant_id=transition.tenant_id,
        workflow_id=transition.workflow_id,
        run_id=transition.run_id,
        runtime_id=transition.runtime_id,
        ordinal=1,
        step_id="step-a",
        plan_hash="a" * 64,
        policy_version="policy-v1",
        allowed_provider_bindings=(binding,),
        provider_attempt_plan=(attempt,),
        budgets={"provider_attempts": 2},
        ttl_seconds=300.0,
        planned_at=transition.created_at,
    )
    transition = _with_effect(transition, effect)
    store = InMemoryWorkflowAuthorizationGrantService()
    observed = _observer(store, signer).observe_or_adopt(
        _observation(_claimed(transition, generation=1), effect),
        heartbeat=_Heartbeat(),
    )

    assert type(observed) is EffectExecutable
    assert _envelope(effect).to_dict()["allowed_provider_bindings"] == [binding]
    assert _envelope(effect).to_dict()["provider_attempt_plan"] == [attempt]
    assert store.get(**_exact(effect)) is None


@pytest.mark.parametrize(
    "mutation",
    ("unknown_field", "whitespace_identity", "numeric_identity"),
)
def test_builder_rejects_provider_binding_normalization_aliases(
    mutation: str,
) -> None:
    transition, _effect = _plan()
    binding = _provider_binding()
    if mutation == "unknown_field":
        binding["unknown_constraint"] = "ignored-by-legacy-parser"
    elif mutation == "whitespace_identity":
        binding["binding_id"] = f" {_VALID_PROVIDER_BINDING_ID} "
    else:
        binding["provider_id"] = 7
    with pytest.raises(WorkflowTransitionAuthorizationGrantError):
        build_workflow_transition_authorization_grant_effect(
            signing_key_ring=_signer(),
            transition_id=transition.transition_id,
            tenant_id=transition.tenant_id,
            workflow_id=transition.workflow_id,
            run_id=transition.run_id,
            runtime_id=transition.runtime_id,
            ordinal=1,
            step_id="step-a",
            plan_hash="a" * 64,
            policy_version="policy-v1",
            allowed_provider_bindings=(binding,),
            ttl_seconds=300.0,
            planned_at=1_000.0,
        )


@pytest.mark.parametrize(
    "maximum_attempts",
    (True, "2", 2.0),
    ids=("boolean", "string", "float"),
)
def test_builder_rejects_provider_attempt_count_coercion(
    maximum_attempts: Any,
) -> None:
    transition, _effect = _plan()
    binding = _provider_binding()
    attempt = _provider_attempt(maximum_attempts=maximum_attempts)
    with pytest.raises(WorkflowTransitionAuthorizationGrantError):
        build_workflow_transition_authorization_grant_effect(
            signing_key_ring=_signer(),
            transition_id=transition.transition_id,
            tenant_id=transition.tenant_id,
            workflow_id=transition.workflow_id,
            run_id=transition.run_id,
            runtime_id=transition.runtime_id,
            ordinal=1,
            step_id="step-a",
            plan_hash="a" * 64,
            policy_version="policy-v1",
            allowed_provider_bindings=(binding,),
            provider_attempt_plan=(attempt,),
            budgets={"provider_attempts": 2},
            ttl_seconds=300.0,
            planned_at=1_000.0,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("unknown_constraint", "ignored-by-legacy-parser"),
        ("profile_id", " profile-a "),
        ("profile_id", 7),
    ),
    ids=("unknown_field", "whitespace_identity", "numeric_identity"),
)
def test_builder_rejects_provider_attempt_normalization_aliases(
    field: str,
    value: Any,
) -> None:
    transition, _effect = _plan()
    attempt = _provider_attempt()
    attempt[field] = value
    with pytest.raises(WorkflowTransitionAuthorizationGrantError):
        build_workflow_transition_authorization_grant_effect(
            signing_key_ring=_signer(),
            transition_id=transition.transition_id,
            tenant_id=transition.tenant_id,
            workflow_id=transition.workflow_id,
            run_id=transition.run_id,
            runtime_id=transition.runtime_id,
            ordinal=1,
            step_id="step-a",
            plan_hash="a" * 64,
            policy_version="policy-v1",
            allowed_provider_bindings=(_provider_binding(),),
            provider_attempt_plan=(attempt,),
            budgets={"provider_attempts": 2},
            ttl_seconds=300.0,
            planned_at=1_000.0,
        )


@pytest.mark.parametrize("provider_attempts", (2.0, 2.9))
def test_builder_rejects_non_integer_provider_attempt_budget(
    provider_attempts: float,
) -> None:
    transition, _effect = _plan()
    with pytest.raises(WorkflowTransitionAuthorizationGrantError):
        build_workflow_transition_authorization_grant_effect(
            signing_key_ring=_signer(),
            transition_id=transition.transition_id,
            tenant_id=transition.tenant_id,
            workflow_id=transition.workflow_id,
            run_id=transition.run_id,
            runtime_id=transition.runtime_id,
            ordinal=1,
            step_id="step-a",
            plan_hash="a" * 64,
            policy_version="policy-v1",
            allowed_provider_bindings=(_provider_binding(),),
            provider_attempt_plan=(_provider_attempt(),),
            budgets={"provider_attempts": provider_attempts},
            ttl_seconds=300.0,
            planned_at=1_000.0,
        )


@pytest.mark.parametrize("provider_attempts", (2.0, 2.9))
def test_observer_rejects_signed_non_integer_provider_attempt_budget(
    provider_attempts: float,
) -> None:
    signer = _signer()
    transition, _effect = _plan(signer=signer)
    envelope = RuntimeAuthorizationEnvelope.issue(
        key_ring=signer,
        tenant_id=transition.tenant_id,
        workflow_id=transition.workflow_id,
        run_id=transition.run_id,
        step_id="step-a",
        plan_hash="a" * 64,
        policy_version="policy-v1",
        allowed_provider_bindings=(_provider_binding(),),
        provider_attempt_plan=(_provider_attempt(),),
        budgets={"provider_attempts": provider_attempts},
        ttl_seconds=300.0,
        now=transition.created_at,
        envelope_id=workflow_transition_authorization_grant_envelope_id(
            transition_id=transition.transition_id,
            ordinal=1,
        ),
        nonce=workflow_transition_authorization_grant_nonce(
            transition_id=transition.transition_id,
            ordinal=1,
        ),
    )
    effect = _effect_for_signed_envelope(
        transition=transition,
        envelope=envelope,
    )
    transition = _with_effect(transition, effect)

    observed = _observer(
        InMemoryWorkflowAuthorizationGrantService(),
        signer,
    ).observe_or_adopt(
        _observation(_claimed(transition, generation=1), effect),
        heartbeat=_Heartbeat(),
    )

    assert type(observed) is EffectQuarantine


@pytest.mark.parametrize(
    "budgets",
    ({" tokens": 1}, {"tokens ": 1}, {"tokens": True}),
    ids=("leading_whitespace_key", "trailing_whitespace_key", "boolean_value"),
)
def test_builder_rejects_budget_normalization_aliases(budgets: dict[str, Any]) -> None:
    transition, _effect = _plan()
    with pytest.raises(WorkflowTransitionAuthorizationGrantError):
        build_workflow_transition_authorization_grant_effect(
            signing_key_ring=_signer(),
            transition_id=transition.transition_id,
            tenant_id=transition.tenant_id,
            workflow_id=transition.workflow_id,
            run_id=transition.run_id,
            runtime_id=transition.runtime_id,
            ordinal=1,
            step_id="step-a",
            plan_hash="a" * 64,
            policy_version="policy-v1",
            budgets=budgets,
            ttl_seconds=300.0,
            planned_at=1_000.0,
        )


def test_staged_effect_rejects_numeric_alias_for_canonical_ttl() -> None:
    signer = _signer()
    transition, effect = _plan(signer=signer)
    payload = thaw_json(effect.payload)
    payload["ttl_seconds"] = 300
    aliased = _rebuild_effect(effect, payload)
    observed = _observer(
        InMemoryWorkflowAuthorizationGrantService(),
        signer,
    ).observe_or_adopt(
        _observation(_claimed(transition, generation=1), aliased),
        heartbeat=_Heartbeat(),
    )
    assert type(observed) is EffectQuarantine


def test_staged_effect_strict_parser_rejects_missing_extra_and_typed_alias_fields() -> None:
    signer = _signer()
    transition, effect = _plan(signer=signer)
    mutations: list[dict[str, Any]] = []
    missing = thaw_json(effect.payload)
    del missing["envelope"]["nonce"]
    mutations.append(missing)
    extra = thaw_json(effect.payload)
    extra["envelope"]["unexpected"] = True
    mutations.append(extra)
    boolean_time = thaw_json(effect.payload)
    boolean_time["envelope"]["issued_at"] = True
    mutations.append(boolean_time)
    long_policy = thaw_json(effect.payload)
    long_policy["envelope"]["policy_version"] = "p" * 257
    mutations.append(long_policy)

    for payload in mutations:
        candidate = _rebuild_effect(effect, payload)
        observed = _observer(
            InMemoryWorkflowAuthorizationGrantService(),
            signer,
        ).observe_or_adopt(
            _observation(_claimed(transition, generation=1), candidate),
            heartbeat=_Heartbeat(),
        )
        assert type(observed) is EffectQuarantine


@pytest.mark.parametrize("field", ("tenant_id", "workflow_id", "run_id", "step_id"))
def test_grant_scope_identity_bound_matches_exact_read_port(field: str) -> None:
    signer = _signer()

    def build(length: int) -> WorkflowTransitionEffect:
        values = {
            "tenant_id": "tenant-a",
            "workflow_id": "workflow-a",
            "run_id": "run-a",
            "step_id": "step-a",
        }
        values[field] = "x" * length
        transition_id = workflow_transition_id(
            tenant_id=values["tenant_id"],
            workflow_id=values["workflow_id"],
            run_id=values["run_id"],
            runtime_id=TRANSITION_RUNTIME_NATIVE,
            kind=TRANSITION_KIND_ADVANCE,
            identity_key=f"identity-bound-{field}-{length}",
        )
        return build_workflow_transition_authorization_grant_effect(
            signing_key_ring=signer,
            transition_id=transition_id,
            tenant_id=values["tenant_id"],
            workflow_id=values["workflow_id"],
            run_id=values["run_id"],
            runtime_id=TRANSITION_RUNTIME_NATIVE,
            ordinal=1,
            step_id=values["step_id"],
            plan_hash="a" * 64,
            policy_version="policy-v1",
            ttl_seconds=300.0,
            planned_at=1_000.0,
        )

    assert build(160).kind == "authorization_grant"
    with pytest.raises(WorkflowTransitionAuthorizationGrantError):
        build(161)


def test_observer_and_executor_are_read_only_then_commit_exactly_once(
    grant_authority: _GrantCase,
) -> None:
    signer = _signer()
    transition, effect = _plan(signer=signer)
    claimed = _claimed(transition, generation=1)
    heartbeat = _Heartbeat()

    observed = _observer(grant_authority.store, signer).observe_or_adopt(
        _observation(claimed, effect),
        heartbeat=heartbeat,
    )
    assert type(observed) is EffectExecutable
    assert grant_authority.store.get(**_exact(effect)) is None
    assert _row_count(grant_authority) == 0
    assert heartbeat.calls == 0

    applied = _executor(grant_authority.store, signer).execute(
        _attempt(claimed, _applying(effect, generation=1)),
        executable=observed,
        heartbeat=heartbeat,
    )
    assert type(applied) is EffectApplied
    stored = grant_authority.store.get(**_exact(effect))
    assert stored is not None
    assert stored.status == "active"
    assert stored.revision == 1
    assert stored.updated_at == effect.created_at
    assert _row_count(grant_authority) == 1
    assert heartbeat.calls == 0

    assert_active_workflow_transition_authorization_grant_proof(
        applied.proof_payload,
        result_payload=applied.result_payload,
        transition=claimed,
        effect=_applying(effect, generation=1),
        claim_generation=1,
        reads=grant_authority.store,
        historical_integrity=_historical(signer),
    )


@pytest.mark.parametrize("path", ("observer", "executor"))
def test_initial_authoritative_read_unavailability_retries_without_mutation(
    path: str,
) -> None:
    signer = _signer()
    transition, effect = _plan(signer=signer)
    claimed = _claimed(transition, generation=1)
    unavailable = _UnavailableRead()
    if path == "observer":
        result = _observer(unavailable, signer).observe_or_adopt(
            _observation(claimed, effect),
            heartbeat=_Heartbeat(),
        )
    else:
        executable = _observer(
            InMemoryWorkflowAuthorizationGrantService(),
            signer,
        ).observe_or_adopt(
            _observation(claimed, effect),
            heartbeat=_Heartbeat(),
        )
        assert type(executable) is EffectExecutable
        result = _executor(unavailable, signer).execute(
            _attempt(claimed, _applying(effect, generation=1)),
            executable=executable,
            heartbeat=_Heartbeat(),
        )

    assert type(result) is EffectRetry
    assert unavailable.commit_calls == 0


@pytest.mark.parametrize("path", ("observer", "executor"))
def test_initial_proven_authoritative_read_conflict_quarantines(
    path: str,
) -> None:
    signer = _signer()
    transition, effect = _plan(signer=signer)
    claimed = _claimed(transition, generation=1)
    conflict = _ConflictRead()
    if path == "observer":
        result = _observer(conflict, signer).observe_or_adopt(
            _observation(claimed, effect),
            heartbeat=_Heartbeat(),
        )
    else:
        executable = _observer(
            InMemoryWorkflowAuthorizationGrantService(),
            signer,
        ).observe_or_adopt(
            _observation(claimed, effect),
            heartbeat=_Heartbeat(),
        )
        assert type(executable) is EffectExecutable
        result = _executor(conflict, signer).execute(
            _attempt(claimed, _applying(effect, generation=1)),
            executable=executable,
            heartbeat=_Heartbeat(),
        )

    assert type(result) is EffectQuarantine
    assert conflict.commit_calls == 0


def test_current_verifier_is_rechecked_after_observation_immediately_before_commit(
    grant_authority: _GrantCase,
) -> None:
    signer = _signer()
    transition, effect = _plan(signer=signer)
    claimed = _claimed(transition, generation=1)
    executable = _observer(grant_authority.store, signer).observe_or_adopt(
        _observation(claimed, effect),
        heartbeat=_Heartbeat(),
    )
    assert type(executable) is EffectExecutable
    envelope = _envelope(effect)
    revoked = Ed25519VerificationKeyRing(
        signer.public_keys(),
        revoked_contract_ids=(envelope.envelope_id,),
    )

    result = _executor(
        grant_authority.store,
        signer,
        current=revoked,
    ).execute(
        _attempt(claimed, _applying(effect, generation=1)),
        executable=executable,
        heartbeat=_Heartbeat(),
    )
    assert type(result) is EffectQuarantine
    assert grant_authority.store.get(**_exact(effect)) is None


@pytest.mark.parametrize("path", ("observer", "executor"))
def test_concurrent_exact_commit_is_adopted_when_current_verification_then_fails(
    grant_authority: _GrantCase,
    path: str,
) -> None:
    signer = _signer()
    transition, effect = _plan(signer=signer)
    claimed = _claimed(transition, generation=1)
    current = _CommitRevokeThenFailVerifier(
        delegate=signer.verification_key_ring(),
        authority=grant_authority.store,
        envelope=_envelope(effect),
    )

    if path == "observer":
        result = _observer(
            grant_authority.store,
            signer,
            current=current,
        ).observe_or_adopt(
            _observation(claimed, effect),
            heartbeat=_Heartbeat(),
        )
        assert type(result) is EffectAlreadyApplied
    else:
        executable = _observer(grant_authority.store, signer).observe_or_adopt(
            _observation(claimed, effect),
            heartbeat=_Heartbeat(),
        )
        assert type(executable) is EffectExecutable
        result = _executor(
            grant_authority.store,
            signer,
            current=current,
        ).execute(
            _attempt(claimed, _applying(effect, generation=1)),
            executable=executable,
            heartbeat=_Heartbeat(),
        )
        assert type(result) is EffectApplied

    stored = grant_authority.store.get(**_exact(effect))
    assert stored is not None and stored.status == "revoked"
    assert current.calls == 1
    assert _row_count(grant_authority) == 1


@pytest.mark.parametrize("path", ("observer", "executor"))
def test_current_failure_with_unreadable_reconciliation_retries_without_commit(
    grant_authority: _GrantCase,
    path: str,
) -> None:
    signer = _signer()
    transition, effect = _plan(signer=signer)
    claimed = _claimed(transition, generation=1)
    transient = _TransientRereadAuthority(grant_authority.store)
    invalid_current = Ed25519VerificationKeyRing(
        signer.public_keys(),
        revoked_contract_ids=(_envelope(effect).envelope_id,),
    )
    if path == "observer":
        result = _observer(
            transient,
            signer,
            current=invalid_current,
        ).observe_or_adopt(
            _observation(claimed, effect),
            heartbeat=_Heartbeat(),
        )
    else:
        executable = _observer(grant_authority.store, signer).observe_or_adopt(
            _observation(claimed, effect),
            heartbeat=_Heartbeat(),
        )
        assert type(executable) is EffectExecutable
        result = _executor(
            transient,
            signer,
            current=invalid_current,
        ).execute(
            _attempt(claimed, _applying(effect, generation=1)),
            executable=executable,
            heartbeat=_Heartbeat(),
        )

    assert type(result) is EffectRetry
    assert transient.read_calls == 2
    assert transient.commit_calls == 0
    assert _row_count(grant_authority) == 0


def test_absence_proof_is_validated_before_final_current_verify_and_commit(
    grant_authority: _GrantCase,
) -> None:
    signer = _signer()
    transition, effect = _plan(signer=signer)
    claimed = _claimed(transition, generation=1)
    executable = _observer(grant_authority.store, signer).observe_or_adopt(
        _observation(claimed, effect),
        heartbeat=_Heartbeat(),
    )
    assert type(executable) is EffectExecutable
    events: list[str] = []
    executor = WorkflowTransitionAuthorizationGrantExecutor(
        authority=_RecordingAuthority(grant_authority.store, events),
        historical_integrity=_historical(signer),
        current_verifier=_RecordingVerifier(
            signer.verification_key_ring(),
            events,
        ),
        clock=lambda: 1_050.0,
    )
    applied = executor.execute(
        _attempt(claimed, _applying(effect, generation=1)),
        executable=executable,
        heartbeat=_Heartbeat(),
    )
    assert type(applied) is EffectApplied
    assert events == ["read", "verify", "commit", "read"]

    other_transition, other_effect = _plan(
        signer=signer,
        identity_key="authorization-grant-transition-proof-replay",
    )
    other_claimed = _claimed(other_transition, generation=1)
    tampered = EffectExecutable(proof_payload=executable.proof_payload)
    replay_events: list[str] = []
    replay = WorkflowTransitionAuthorizationGrantExecutor(
        authority=_RecordingAuthority(
            InMemoryWorkflowAuthorizationGrantService(),
            replay_events,
        ),
        historical_integrity=_historical(signer),
        current_verifier=_RecordingVerifier(
            signer.verification_key_ring(),
            replay_events,
        ),
        clock=lambda: 1_050.0,
    ).execute(
        _attempt(other_claimed, _applying(other_effect, generation=1)),
        executable=tampered,
        heartbeat=_Heartbeat(),
    )
    assert type(replay) is EffectQuarantine
    assert replay_events == ["read"]


def test_revocation_or_expiry_after_commit_preserves_historical_applied_result(
    grant_authority: _GrantCase,
) -> None:
    signer = _signer()
    transition, effect = _plan(signer=signer)
    claimed = _claimed(transition, generation=1)
    executable = _observer(grant_authority.store, signer).observe_or_adopt(
        _observation(claimed, effect),
        heartbeat=_Heartbeat(),
    )
    clock_calls: list[float] = []

    def crossing_clock() -> float:
        value = 1_050.0 if not clock_calls else 1_301.0
        clock_calls.append(value)
        return value

    executor = WorkflowTransitionAuthorizationGrantExecutor(
        authority=_RevokeAfterCommitAuthority(grant_authority.store),
        historical_integrity=_historical(signer),
        current_verifier=signer.verification_key_ring(),
        clock=crossing_clock,
    )
    result = executor.execute(
        _attempt(claimed, _applying(effect, generation=1)),
        executable=executable,
        heartbeat=_Heartbeat(),
    )
    assert type(result) is EffectApplied
    assert clock_calls == [1_050.0]
    stored = grant_authority.store.get(**_exact(effect))
    assert stored is not None and stored.status == "revoked"

    durable = _applied_effect(
        effect,
        result,
        generation=1,
        mode="execute",
    )
    assert_durable_workflow_transition_authorization_grant_proof(
        transition=_claimed(transition, generation=2),
        effect=durable,
        reads=grant_authority.store,
        historical_integrity=_historical(signer),
    )
    with pytest.raises(WorkflowTransitionAuthorizationGrantError):
        assert_current_workflow_transition_authorization_grant_validity(
            transition=_claimed(transition, generation=2),
            effect=durable,
            reads=grant_authority.store,
            historical_integrity=_historical(signer),
            current_verifier=signer.verification_key_ring(),
            clock=lambda: 1_301.0,
        )


@pytest.mark.parametrize(
    "mode,expected_type,expected_rows",
    (
        ("exception_before_commit", EffectRetry, 0),
        ("exception_after_commit", EffectApplied, 1),
        ("return_without_commit", EffectQuarantine, 0),
    ),
)
def test_executor_classifies_commit_failures_by_authoritative_reread(
    grant_authority: _GrantCase,
    mode: str,
    expected_type: type[Any],
    expected_rows: int,
) -> None:
    signer = _signer()
    transition, effect = _plan(signer=signer)
    claimed = _claimed(transition, generation=1)
    executable = _observer(grant_authority.store, signer).observe_or_adopt(
        _observation(claimed, effect),
        heartbeat=_Heartbeat(),
    )
    assert type(executable) is EffectExecutable
    fault = _FaultAuthority(grant_authority.store, mode)

    result = _executor(fault, signer).execute(
        _attempt(claimed, _applying(effect, generation=1)),
        executable=executable,
        heartbeat=_Heartbeat(),
    )
    assert type(result) is expected_type
    assert fault.commit_calls == 1
    assert _row_count(grant_authority) == expected_rows


@pytest.mark.parametrize("commit_raises", (False, True), ids=("normal_return", "lost_response"))
def test_transient_post_commit_reread_retries_then_next_claim_adopts(
    grant_authority: _GrantCase,
    commit_raises: bool,
) -> None:
    signer = _signer()
    transition, effect = _plan(signer=signer)
    claimed = _claimed(transition, generation=1)
    executable = _observer(grant_authority.store, signer).observe_or_adopt(
        _observation(claimed, effect),
        heartbeat=_Heartbeat(),
    )
    assert type(executable) is EffectExecutable
    transient = _TransientRereadAuthority(
        grant_authority.store,
        commit_raises=commit_raises,
    )

    retried = _executor(transient, signer).execute(
        _attempt(claimed, _applying(effect, generation=1)),
        executable=executable,
        heartbeat=_Heartbeat(),
    )

    assert type(retried) is EffectRetry
    assert transient.commit_calls == 1
    assert transient.read_calls == 2
    assert _row_count(grant_authority) == 1
    adopted = _observer(grant_authority.store, signer).observe_or_adopt(
        _observation(_claimed(transition, generation=2), effect),
        heartbeat=_Heartbeat(),
    )
    assert type(adopted) is EffectAlreadyApplied


def test_hard_crash_after_commit_is_adopted_next_generation_without_second_write(
    grant_authority: _GrantCase,
) -> None:
    signer = _signer()
    transition, effect = _plan(signer=signer)
    claimed = _claimed(transition, generation=1)
    executable = _observer(grant_authority.store, signer).observe_or_adopt(
        _observation(claimed, effect),
        heartbeat=_Heartbeat(),
    )
    fault = _FaultAuthority(grant_authority.store, "hard_crash_after_commit")

    with pytest.raises(_HardCrash):
        _executor(fault, signer).execute(
            _attempt(claimed, _applying(effect, generation=1)),
            executable=executable,
            heartbeat=_Heartbeat(),
        )
    assert _row_count(grant_authority) == 1

    next_claim = _claimed(transition, generation=2)
    adopted = _observer(grant_authority.store, signer).observe_or_adopt(
        _observation(next_claim, effect),
        heartbeat=_Heartbeat(),
    )
    assert type(adopted) is EffectAlreadyApplied
    assert adopted.proof_payload["context"]["claim_generation"] == 2
    assert fault.commit_calls == 1
    assert _row_count(grant_authority) == 1

    durable_effect = _applied_effect(
        effect,
        adopted,
        generation=2,
        mode="adopt",
    )
    later = _claimed(transition, generation=4)
    assert_durable_workflow_transition_authorization_grant_proof(
        transition=later,
        effect=durable_effect,
        reads=grant_authority.store,
        historical_integrity=_historical(signer),
    )
    stale = thaw_json(adopted.proof_payload)
    stale["context"]["claim_generation"] = 1
    stale_effect = _replace_persisted_evidence(
        durable_effect,
        proof_payload=stale,
    )
    with pytest.raises(WorkflowTransitionEffectProofError):
        assert_durable_workflow_transition_authorization_grant_proof(
            transition=later,
            effect=stale_effect,
            reads=grant_authority.store,
            historical_integrity=_historical(signer),
        )


def test_concurrent_identical_effect_execution_converges_on_one_grant(
    grant_authority: _GrantCase,
) -> None:
    signer = _signer()
    transition, effect = _plan(signer=signer)
    claimed = _claimed(transition, generation=1)
    executable = _observer(grant_authority.store, signer).observe_or_adopt(
        _observation(claimed, effect),
        heartbeat=_Heartbeat(),
    )
    attempt = _attempt(claimed, _applying(effect, generation=1))

    def execute(_index: int) -> Any:
        return _executor(grant_authority.store, signer).execute(
            attempt,
            executable=executable,
            heartbeat=_Heartbeat(),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(execute, range(2)))
    assert all(type(result) is EffectApplied for result in results)
    assert results[0].result_payload == results[1].result_payload
    assert _row_count(grant_authority) == 1


def test_historical_proof_survives_expiry_and_revocation_but_current_validity_does_not(
    grant_authority: _GrantCase,
) -> None:
    signer = _signer()
    transition, effect = _plan(signer=signer)
    claimed = _claimed(transition, generation=1)
    executable = _observer(grant_authority.store, signer).observe_or_adopt(
        _observation(claimed, effect),
        heartbeat=_Heartbeat(),
    )
    applied = _executor(grant_authority.store, signer).execute(
        _attempt(claimed, _applying(effect, generation=1)),
        executable=executable,
        heartbeat=_Heartbeat(),
    )
    assert type(applied) is EffectApplied
    durable_effect = _applied_effect(
        effect,
        applied,
        generation=1,
        mode="execute",
    )
    later = _claimed(transition, generation=3)
    counting_reads = _CountingRead(grant_authority.store)
    assert (
        assert_current_workflow_transition_authorization_grant_validity(
            transition=later,
            effect=durable_effect,
            reads=counting_reads,
            historical_integrity=_historical(signer),
            current_verifier=signer.verification_key_ring(),
            clock=lambda: 1_050.0,
        )
        is None
    )
    assert counting_reads.calls == 1
    grant_authority.store.revoke(
        _envelope(effect).envelope_id,
        reason_code="contract_revoked",
        expected_revision=1,
    )

    assert_durable_workflow_transition_authorization_grant_proof(
        transition=later,
        effect=durable_effect,
        reads=grant_authority.store,
        historical_integrity=_historical(signer),
    )
    revoked_current = Ed25519VerificationKeyRing(
        signer.public_keys(),
        revoked_key_ids=("grant-key-v1",),
    )
    with pytest.raises(WorkflowTransitionAuthorizationGrantError):
        assert_current_workflow_transition_authorization_grant_validity(
            transition=later,
            effect=durable_effect,
            reads=grant_authority.store,
            historical_integrity=_historical(signer),
            current_verifier=revoked_current,
            clock=lambda: 2_000.0,
        )

    adopted = _observer(
        grant_authority.store,
        signer,
        now=2_000.0,
        current=revoked_current,
    ).observe_or_adopt(
        _observation(_claimed(transition, generation=4), effect),
        heartbeat=_Heartbeat(),
    )
    assert type(adopted) is EffectAlreadyApplied


@pytest.mark.parametrize(
    "axis",
    ("row_revoked", "expired", "key_revoked", "contract_revoked"),
)
def test_current_validity_rejects_each_independent_live_axis(
    grant_authority: _GrantCase,
    axis: str,
) -> None:
    signer = _signer()
    transition, effect = _plan(signer=signer)
    claimed = _claimed(transition, generation=1)
    executable = _observer(grant_authority.store, signer).observe_or_adopt(
        _observation(claimed, effect),
        heartbeat=_Heartbeat(),
    )
    applied = _executor(grant_authority.store, signer).execute(
        _attempt(claimed, _applying(effect, generation=1)),
        executable=executable,
        heartbeat=_Heartbeat(),
    )
    assert type(applied) is EffectApplied
    durable = _applied_effect(
        effect,
        applied,
        generation=1,
        mode="execute",
    )
    now = 1_050.0
    current: Any = signer.verification_key_ring()
    if axis == "row_revoked":
        grant_authority.store.revoke(
            _envelope(effect).envelope_id,
            reason_code="row_revoked",
            expected_revision=1,
        )
    elif axis == "expired":
        now = 1_300.0
    elif axis == "key_revoked":
        current = Ed25519VerificationKeyRing(
            signer.public_keys(),
            revoked_key_ids=("grant-key-v1",),
        )
    else:
        current = Ed25519VerificationKeyRing(
            signer.public_keys(),
            revoked_contract_ids=(_envelope(effect).envelope_id,),
        )

    with pytest.raises(WorkflowTransitionAuthorizationGrantError):
        assert_current_workflow_transition_authorization_grant_validity(
            transition=_claimed(transition, generation=2),
            effect=durable,
            reads=grant_authority.store,
            historical_integrity=_historical(signer),
            current_verifier=current,
            clock=lambda: now,
        )
    adopted = _observer(
        grant_authority.store,
        signer,
        current=current,
        now=now,
    ).observe_or_adopt(
        _observation(_claimed(transition, generation=3), effect),
        heartbeat=_Heartbeat(),
    )
    assert type(adopted) is EffectAlreadyApplied


def test_absent_expired_revoked_unknown_or_forged_envelope_never_writes(
    grant_authority: _GrantCase,
) -> None:
    trusted = _signer()
    rogue = _signer(seed=2)
    unknown = _signer(key_id="unknown-key", seed=5)
    transition, trusted_effect = _plan(signer=trusted)
    _rogue_transition, rogue_effect = _plan(signer=rogue)
    _unknown_transition, unknown_effect = _plan(signer=unknown)
    claimed = _claimed(transition, generation=1)
    envelope = _envelope(trusted_effect)
    current_cases = (
        (trusted_effect, trusted.verification_key_ring(), 1_301.0),
        (
            trusted_effect,
            Ed25519VerificationKeyRing(
                trusted.public_keys(),
                revoked_key_ids=("grant-key-v1",),
            ),
            1_050.0,
        ),
        (
            trusted_effect,
            Ed25519VerificationKeyRing(
                trusted.public_keys(),
                revoked_contract_ids=(envelope.envelope_id,),
            ),
            1_050.0,
        ),
        (rogue_effect, trusted.verification_key_ring(), 1_050.0),
        (unknown_effect, trusted.verification_key_ring(), 1_050.0),
    )
    for candidate, verifier, now in current_cases:
        result = _observer(
            grant_authority.store,
            trusted,
            current=verifier,
            now=now,
        ).observe_or_adopt(
            _observation(claimed, candidate),
            heartbeat=_Heartbeat(),
        )
        assert type(result) is EffectQuarantine
    assert _row_count(grant_authority) == 0


def test_rotation_overlap_and_retained_key_removal_are_explicitly_separate(
    grant_authority: _GrantCase,
) -> None:
    old = _signer(key_id="old-key", seed=3)
    new = _signer(key_id="new-key", seed=4)
    transition, effect = _plan(signer=old)
    claimed = _claimed(transition, generation=1)
    overlap = Ed25519VerificationKeyRing({**old.public_keys(), **new.public_keys()})
    observed = _observer(
        grant_authority.store,
        old,
        current=overlap,
    ).observe_or_adopt(
        _observation(claimed, effect),
        heartbeat=_Heartbeat(),
    )
    assert type(observed) is EffectExecutable
    applied = _executor(
        grant_authority.store,
        old,
        current=overlap,
    ).execute(
        _attempt(claimed, _applying(effect, generation=1)),
        executable=observed,
        heartbeat=_Heartbeat(),
    )
    assert type(applied) is EffectApplied

    missing_old = RetainedEd25519AuthorizationEnvelopeIntegrityVerifier(new.public_keys())
    with pytest.raises(WorkflowTransitionAuthorizationGrantError):
        assert_active_workflow_transition_authorization_grant_proof(
            applied.proof_payload,
            result_payload=applied.result_payload,
            transition=claimed,
            effect=_applying(effect, generation=1),
            claim_generation=1,
            reads=grant_authority.store,
            historical_integrity=missing_old,
        )


def test_historical_integrity_port_cannot_substitute_hmac_for_ed25519(
    grant_authority: _GrantCase,
) -> None:
    signer = _signer()
    transition, effect = _plan(signer=signer)
    grant_authority.store.commit_transition_grant(
        _envelope(effect),
        recorded_at=effect.created_at,
    )
    with pytest.raises(WorkflowTransitionAuthorizationGrantError):
        WorkflowTransitionAuthorizationGrantObserver(
            reads=grant_authority.store,
            historical_integrity=_HmacHistoricalIntegrity(),
            current_verifier=signer.verification_key_ring(),
            clock=lambda: 1_050.0,
        )
    assert _row_count(grant_authority) == 1


@pytest.mark.parametrize(
    "field,mutation",
    (
        ("nonce", "other-nonce"),
        ("signature", "A" * 88),
        ("key_id", "other-key"),
        ("tenant_id", "tenant-b"),
        ("workflow_id", "workflow-b"),
        ("run_id", "run-b"),
        ("step_id", "step-b"),
        ("plan_hash", "b" * 64),
        ("policy_version", "policy-v2"),
        ("issued_at", 1_001.0),
        ("issued_at", 10**400),
        ("expires_at", 1_301.0),
    ),
)
def test_signed_envelope_tampering_quarantines_without_write(
    grant_authority: _GrantCase,
    field: str,
    mutation: Any,
) -> None:
    signer = _signer()
    transition, effect = _plan(signer=signer)
    payload = thaw_json(effect.payload)
    payload["envelope"][field] = mutation
    tampered = _rebuild_effect(effect, payload)

    result = _observer(grant_authority.store, signer).observe_or_adopt(
        _observation(_claimed(transition, generation=1), tampered),
        heartbeat=_Heartbeat(),
    )
    assert type(result) is EffectQuarantine
    assert _row_count(grant_authority) == 0


def test_same_envelope_id_divergence_and_cross_transition_envelope_replay_quarantine(
    grant_authority: _GrantCase,
) -> None:
    signer = _signer()
    transition, effect = _plan(signer=signer)
    original = grant_authority.store.commit_transition_grant(
        _envelope(effect),
        recorded_at=effect.created_at,
    )
    divergent = build_workflow_transition_authorization_grant_effect(
        signing_key_ring=signer,
        transition_id=transition.transition_id,
        tenant_id=transition.tenant_id,
        workflow_id=transition.workflow_id,
        run_id=transition.run_id,
        runtime_id=transition.runtime_id,
        ordinal=1,
        step_id="step-a",
        plan_hash="a" * 64,
        policy_version="policy-v1",
        allowed_tools=("artifact.delete",),
        budgets={"provider_attempts": 2, "tokens": 1000},
        ttl_seconds=300.0,
        planned_at=transition.created_at,
    )
    assert _envelope(divergent).envelope_id == _envelope(effect).envelope_id
    assert _envelope(divergent).nonce == _envelope(effect).nonce
    assert thaw_json(divergent.payload)["envelope_digest"] != thaw_json(effect.payload)["envelope_digest"]
    divergent_result = _observer(grant_authority.store, signer).observe_or_adopt(
        _observation(
            _claimed(_with_effect(transition, divergent), generation=1),
            divergent,
        ),
        heartbeat=_Heartbeat(),
    )
    assert type(divergent_result) is EffectQuarantine

    other_transition, _other_effect = _plan(
        signer=signer,
        identity_key="authorization-grant-cross-transition-envelope-replay",
    )
    replay = _effect_for_signed_envelope(
        transition=other_transition,
        envelope=_envelope(effect),
    )
    replay_result = _observer(grant_authority.store, signer).observe_or_adopt(
        _observation(
            _claimed(_with_effect(other_transition, replay), generation=1),
            replay,
        ),
        heartbeat=_Heartbeat(),
    )
    assert type(replay_result) is EffectQuarantine
    assert grant_authority.store.get(**_exact(effect)) == original
    assert _row_count(grant_authority) == 1


def test_semantic_proofs_reject_resource_context_result_and_row_replay(
    grant_authority: _GrantCase,
) -> None:
    signer = _signer()
    transition, effect = _plan(signer=signer)
    claimed = _claimed(transition, generation=1)
    executable = _observer(grant_authority.store, signer).observe_or_adopt(
        _observation(claimed, effect),
        heartbeat=_Heartbeat(),
    )
    applied = _executor(grant_authority.store, signer).execute(
        _attempt(claimed, _applying(effect, generation=1)),
        executable=executable,
        heartbeat=_Heartbeat(),
    )
    assert type(applied) is EffectApplied
    applying = _applying(effect, generation=1)

    for field, value in (
        ("transition_id", "wft-" + "0" * 64),
        ("effect_id", "wfx-" + "0" * 64),
        ("runtime_id", TRANSITION_RUNTIME_LANGGRAPH),
        ("claim_generation", 2),
        ("transition_request_fingerprint", "0" * 64),
        ("effect_payload_digest", "0" * 64),
    ):
        tampered = thaw_json(applied.proof_payload)
        tampered["context"][field] = value
        with pytest.raises((WorkflowTransitionEffectProofError, WorkflowTransitionAuthorizationGrantError)):
            assert_active_workflow_transition_authorization_grant_proof(
                tampered,
                result_payload=applied.result_payload,
                transition=claimed,
                effect=applying,
                claim_generation=1,
                reads=grant_authority.store,
                historical_integrity=_historical(signer),
            )

    resource = thaw_json(applied.proof_payload)
    resource["resource"]["digest"] = "0" * 64
    with pytest.raises(WorkflowTransitionEffectProofError):
        assert_active_workflow_transition_authorization_grant_proof(
            resource,
            result_payload=applied.result_payload,
            transition=claimed,
            effect=applying,
            claim_generation=1,
            reads=grant_authority.store,
            historical_integrity=_historical(signer),
        )
    result = thaw_json(applied.result_payload)
    result["issuance"]["envelope_digest"] = "0" * 64
    with pytest.raises(WorkflowTransitionAuthorizationGrantError):
        assert_active_workflow_transition_authorization_grant_proof(
            applied.proof_payload,
            result_payload=result,
            transition=claimed,
            effect=applying,
            claim_generation=1,
            reads=grant_authority.store,
            historical_integrity=_historical(signer),
        )

    durable_effect = _applied_effect(
        effect,
        applied,
        generation=1,
        mode="execute",
    )
    later = _claimed(transition, generation=3)
    for field, value in (
        ("issued_revision", True),
        ("issued_at", 1_000),
        ("expires_at", 1_300),
    ):
        coercible = thaw_json(applied.result_payload)
        coercible["issuance"][field] = value
        with pytest.raises(WorkflowTransitionAuthorizationGrantError):
            assert_active_workflow_transition_authorization_grant_proof(
                applied.proof_payload,
                result_payload=coercible,
                transition=claimed,
                effect=applying,
                claim_generation=1,
                reads=grant_authority.store,
                historical_integrity=_historical(signer),
            )
        with pytest.raises(WorkflowTransitionAuthorizationGrantError):
            assert_durable_workflow_transition_authorization_grant_proof(
                transition=later,
                effect=_replace_persisted_evidence(
                    durable_effect,
                    result_payload=coercible,
                ),
                reads=grant_authority.store,
                historical_integrity=_historical(signer),
            )

    stored = grant_authority.store.get(**_exact(effect))
    assert stored is not None
    corrupted = replace(stored, revoked_at=1_100.0)
    observed = WorkflowTransitionAuthorizationGrantObserver(
        reads=_StaticRead(corrupted),
        historical_integrity=_historical(signer),
        current_verifier=signer.verification_key_ring(),
        clock=lambda: 1_050.0,
    ).observe_or_adopt(
        _observation(_claimed(transition, generation=2), effect),
        heartbeat=_Heartbeat(),
    )
    assert type(observed) is EffectQuarantine


def test_sql_raw_projection_tampering_is_never_adopted(
    grant_authority: _GrantCase,
) -> None:
    if grant_authority.name != "sql":
        pytest.skip("raw SQL projection applies only to SQL adapter")
    signer = _signer()
    transition, effect = _plan(signer=signer)
    grant_authority.store.commit_transition_grant(
        _envelope(effect),
        recorded_at=1_000.0,
    )
    assert grant_authority.engine is not None
    with grant_authority.engine.begin() as connection:
        connection.execute(
            sa.update(WorkflowAuthorizationGrantDB.__table__)
            .where(WorkflowAuthorizationGrantDB.envelope_id == _envelope(effect).envelope_id)
            .values(revision=2)
        )
    observed = _observer(grant_authority.store, signer).observe_or_adopt(
        _observation(_claimed(transition, generation=1), effect),
        heartbeat=_Heartbeat(),
    )
    assert type(observed) is EffectQuarantine


def test_authorization_grant_effect_is_imported_only_by_the_cutover_composition() -> None:
    root = Path(__file__).resolve().parents[1]
    # The Native cutover composition is the single sanctioned importer, and it
    # registers the grant only when a deployment supplies both verifiers.  Any
    # other production import would reach the permissive historical verifier
    # without the revocation-aware one beside it.
    approved = {
        root / "agent/services/workflow_transition_authorization_grant.py",
        root / "agent/services/workflow_authorization_grant_service.py",
        root / "agent/services/workflow_transition_native_composition.py",
    }
    violations: list[str] = []
    for path in (root / "agent").rglob("*.py"):
        if path in approved:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == (
                "agent.services.workflow_transition_authorization_grant"
            ):
                violations.append(str(path.relative_to(root)))
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == ("agent.services.workflow_transition_authorization_grant"):
                        violations.append(str(path.relative_to(root)))
    assert violations == []
