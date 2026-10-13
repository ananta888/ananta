"""Real Hub test signing and provider selection can target an owned SDK fixture endpoint."""

from tests.pi.test_pi_native_node import native_setup
from worker.runtime.native_graph.contracts import NativeNodeCommand


def test_selected_loopback_endpoint_is_bound_consistently_before_sdk_execution(tmp_path):
    endpoint = 'http://127.0.0.1:43123/v1/chat/completions'
    adapter, task, runner, _, _ = native_setup(tmp_path, endpoint_identity=endpoint)
    command = NativeNodeCommand.from_mapping(task['worker_execution_context']['native_node_command'])
    assert command.provider_binding.endpoint_identity == endpoint
    assert command.provider_context['provider_endpoint_identity'] == endpoint
    assert command.provider_profile_bindings[0].binding.endpoint_identity == endpoint
    result = adapter.execute_task(task)
    assert result.status == 'completed'
    assert len(runner.calls) == 1
