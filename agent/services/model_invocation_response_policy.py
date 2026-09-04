"""Apply local-runtime response policy at the model invocation boundary."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent.services.local_runtime_response_policy import (
    LocalRuntimeResponsePolicy,
    LocalRuntimeResponsePolicyError,
    configured_response_policy,
)


@dataclass(frozen=True)
class ResponsePolicyFailureProjector:
    """Project parser failure through the existing provider and trace ports."""

    middleware: Any
    prepared: Any
    provider: str
    model: str
    prompt_trace: Any
    trace_service: Any
    finalize_trace_error: Callable[..., None]

    def __call__(self, reason_code: str) -> None:
        self.middleware.fail(
            self.prepared,
            provider=self.provider,
            model=self.model,
            reason_code=reason_code,
        )
        self.finalize_trace_error(
            self.prompt_trace,
            self.trace_service,
            reason_code,
            reason_code,
        )


def apply_local_response_policy(
    payload: dict[str, Any],
    *,
    profile: object,
    tools_requested: bool,
    on_failure: Any = None,
    raise_contract_error: Any,
) -> dict[str, Any]:
    """Apply response parsing and project bounded provider failures."""

    try:
        return LocalRuntimeResponsePolicy().apply(
            payload,
            policy_id=configured_response_policy(profile),
            tools_requested=tools_requested,
        )
    except LocalRuntimeResponsePolicyError as exc:
        if on_failure is not None:
            on_failure(str(exc))
        raise_contract_error(payload, error_type=str(exc), detail=str(exc))
        raise AssertionError("raise_contract_error must terminate")


__all__ = ["ResponsePolicyFailureProjector", "apply_local_response_policy"]
