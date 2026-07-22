from agent.services.sfu_broadcast_runtime_control_port import (
    SfuRuntimeControlCommand,
    UnsupportedSfuRuntimeControlBoundary,
)


def test_unsupported_boundary_accepts_command_arguments_and_fails_reason_coded():
    command = SfuRuntimeControlCommand("id", "project_flags", "runtime", "tenant", 1, 1, "a" * 64, "nonce", 2, 1.0, 2.0, {})
    result = UnsupportedSfuRuntimeControlBoundary().execute(command, "ignored", retry=True)
    assert result.accepted is False
    assert result.reason_code == "sfu_runtime_control_unavailable"
