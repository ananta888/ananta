"""Confine delegated node resources to the Hub's signed authorization scope."""

from __future__ import annotations

from worker.runtime.native_graph.contracts import NativeNodeCommand


def assert_command_resource_scope(command: NativeNodeCommand) -> None:
    """Validate the unsigned node projection before it reaches a handler.

    Signature, freshness, identity, budgets and nonce consumption remain the
    authorization verifier's responsibility. A permissive local policy cannot
    broaden the tools/artifacts authorized by the Hub envelope.
    """
    node = command.node
    envelope = command.authorization
    if set(node.allowed_tools) - set(envelope.allowed_tools):
        raise ValueError("native_authorization_tool_scope_mismatch")
    declared_artifacts = set(node.input_artifacts) | set(node.output_artifacts)
    if declared_artifacts - set(envelope.allowed_artifacts):
        raise ValueError("native_authorization_artifact_scope_mismatch")
    if set(command.artifact_refs) - set(node.input_artifacts):
        raise ValueError("native_input_artifact_undeclared")
