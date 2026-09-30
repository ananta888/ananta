"""Worker-side admission and Worker identity checks for Recovery dispatch.

A Worker never decides admission itself: it asks the Hub, which consumes its
authoritative lease.  On the Hub role the injected local admission callable is
used directly.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from agent.common.recovery_dispatch_contract import (
    RecoveryDispatchGateDecision,
    _value,
)
from agent.services.recovery_dispatch_gate_policy import (
    is_recovery_child,
    normalize_dispatch_phase,
)


class LocalDispatchAdmission(Protocol):
    """Hub-local lease admission (``RecoveryDispatchGateService.admit_dispatch_lease``)."""

    def __call__(
        self,
        task_id: str,
        *,
        token: str | None,
        phase: str,
        worker_url: str | None,
        request_fingerprint: str | None = None,
        trusted_local: bool = False,
    ) -> RecoveryDispatchGateDecision: ...


def admit_incoming_recovery_dispatch(
    *,
    task: Any,
    token: str | None,
    phase: str,
    admit_local: LocalDispatchAdmission,
    request_fingerprint: str | None = None,
    timeout_seconds: float = 3.0,
) -> RecoveryDispatchGateDecision:
    """Worker-side admission backed by the Hub's authoritative lease."""

    recovery_child = is_recovery_child(task)
    if token and not recovery_child:
        return RecoveryDispatchGateDecision(
            False,
            "recovery_dispatch_lease_unexpected",
        )
    if not recovery_child:
        return RecoveryDispatchGateDecision(
            True,
            "not_recovery_child",
        )
    task_id = str(_value(task, "id") or "").strip()
    if not task_id or not token:
        return RecoveryDispatchGateDecision(
            False,
            "recovery_dispatch_lease_missing",
        )
    from agent.config import settings

    if str(settings.role or "").strip().lower() == "hub":
        local_url = str(
            settings.agent_url
            or f"http://localhost:{settings.port}"
        ).strip().rstrip("/")
        return admit_local(
            task_id,
            token=token,
            phase=phase,
            worker_url=local_url,
            request_fingerprint=request_fingerprint,
            trusted_local=True,
        )

    hub_url = str(settings.hub_url or "").strip().rstrip("/")
    worker_url = str(
        settings.agent_url
        or f"http://localhost:{settings.port}"
    ).strip().rstrip("/")
    if not hub_url:
        return RecoveryDispatchGateDecision(
            False,
            "recovery_dispatch_hub_unavailable",
        )
    try:
        import requests

        from agent.auth import resolve_configured_agent_token

        worker_token = resolve_configured_agent_token()
        if not worker_token:
            return RecoveryDispatchGateDecision(
                False,
                "recovery_dispatch_worker_identity_denied",
            )
        response = requests.post(
            (
                f"{hub_url}/internal/tasks/{task_id}"
                "/recovery-dispatch-admission"
            ),
            json={
                "phase": normalize_dispatch_phase(phase),
                "request_fingerprint": str(
                    request_fingerprint or ""
                ),
            },
            headers={
                "Authorization": f"Bearer {worker_token}",
                "X-Ananta-Recovery-Dispatch-Lease": str(token),
                "X-Ananta-Worker-Url": worker_url,
            },
            timeout=max(0.5, min(float(timeout_seconds), 10.0)),
        )
        if int(response.status_code) >= 400:
            return RecoveryDispatchGateDecision(
                False,
                "recovery_dispatch_hub_rejected",
            )
        body = response.json()
        payload = (
            body.get("data")
            if isinstance(body, Mapping)
            else None
        )
        if not isinstance(payload, Mapping) or not bool(
            payload.get("allowed")
        ):
            return RecoveryDispatchGateDecision(
                False,
                str(
                    (payload or {}).get("reason_code")
                    or "recovery_dispatch_hub_rejected"
                ),
            )
        return RecoveryDispatchGateDecision(
            True,
            str(
                payload.get("reason_code")
                or "recovery_dispatch_lease_valid"
            ),
            source_task_id=(
                str(payload.get("source_task_id") or "") or None
            ),
            plan_id=str(payload.get("plan_id") or "") or None,
            release_epoch=(
                str(payload.get("release_epoch") or "") or None
            ),
        )
    except Exception:
        return RecoveryDispatchGateDecision(
            False,
            "recovery_dispatch_hub_unavailable",
        )


def recovery_worker_identity_valid(
    repos: Any,
    *,
    task: Any,
    worker_url: str,
    worker_token: str | None,
    app: Any | None = None,
) -> bool:
    """Authenticate a registered Worker and check its capabilities."""

    if not worker_url or not worker_token:
        return False
    try:
        worker = repos.agent_repo.get_by_url(worker_url)
        agents = tuple(repos.agent_repo.get_all() or ())
    except Exception:
        return False
    if worker is None:
        return False
    try:
        from flask import current_app, has_app_context

        from agent.auth import resolve_configured_agent_token
        from agent.services.workflow_worker_service_auth import (
            RECOVERY_TASK_DISPATCH_SCOPE,
            authenticate_registered_workflow_worker,
        )

        config = (
            getattr(app, "config", None)
            if app is not None
            else current_app.config if has_app_context() else None
        )
        hub_service_token = resolve_configured_agent_token(
            config
        )
        user_session_secret = (
            getattr(app, "secret_key", None)
            if app is not None
            else current_app.secret_key
            if has_app_context()
            else None
        )
        identity = authenticate_registered_workflow_worker(
            str(worker_token),
            required_scope=RECOVERY_TASK_DISPATCH_SCOPE,
            claimed_worker_id=str(
                _value(worker, "name") or ""
            ),
            claimed_worker_url=str(worker_url),
            registered_agents=agents,
            hub_service_token=hub_service_token,
            user_session_secret=user_session_secret,
            config=config,
        )
    except Exception:
        return False
    required_capabilities = {
        str(value).strip()
        for value in (
            _value(task, "required_capabilities") or ()
        )
        if str(value).strip()
    }
    return required_capabilities.issubset(
        set(identity.capabilities)
    )
