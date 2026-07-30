from __future__ import annotations

import ast
from pathlib import Path

from ananta_contracts.context_access_policy import (
    ContextAccessPolicy,
    ContextAccessPolicyEvaluator,
    ContextAccessRule,
    Decision,
    RequestedOperation,
    Sensitivity,
    build_destination_context,
)
from worker.core import context_access_policy as worker_policy


def test_worker_module_is_a_compatibility_facade() -> None:
    assert worker_policy.ContextAccessPolicy is ContextAccessPolicy
    assert worker_policy.ContextAccessPolicyEvaluator is ContextAccessPolicyEvaluator


def test_unknown_source_is_denied_by_default() -> None:
    policy = ContextAccessPolicy(
        policy_id="policy-example",
        version=1,
        scope="project",
        rules=[
            ContextAccessRule(
                id="public-only",
                description="Only public source material is allowed.",
                sensitivity=Sensitivity.public,
                send_allowed=True,
            )
        ],
        defaults={"send_allowed": False},
    )
    destination = build_destination_context(
        worker_id="worker-example",
        worker_kind="llm",
        runtime_target_id="runtime-example",
        runtime_kind="local",
        provider_id="provider-example",
        provider_location="local",
        model_id="model-example",
        requested_operation=RequestedOperation.send_to_llm,
    )

    decision = ContextAccessPolicyEvaluator(policy).get_decision(
        {
            "block_id": "block-example",
            "source_ref": "source-ref-example",
            "sensitivity": Sensitivity.unknown,
            "content_hash": "0" * 64,
        },
        destination,
    )

    assert decision.decision is Decision.deny


def test_agent_production_code_does_not_import_worker_policy() -> None:
    agent_root = Path(__file__).parents[1] / "agent"
    violations: list[str] = []
    for path in agent_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == (
                "worker.core.context_access_policy"
            ):
                violations.append(str(path.relative_to(agent_root.parent)))
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "worker.core.context_access_policy":
                        violations.append(str(path.relative_to(agent_root.parent)))

    assert violations == []
