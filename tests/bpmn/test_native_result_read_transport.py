"""Retrieval-only transport rejects unbounded bodies and always closes responses."""

from types import SimpleNamespace

import pytest

from agent.services.native_result_read_transport import NativeResultReadTransport


@pytest.mark.parametrize("status,chunks,accepted", [(200, [b'{"status":"completed"}'], True),
                                                    (302, [b'{}'], False), (503, [b'{}'], False),
                                                    (200, [b'x' * 262145], False), (200, [b'[]'], False)])
def test_retrieval_is_bounded_and_closes_every_response(status, chunks, accepted):
    observations = []
    response = SimpleNamespace(status_code=status, iter_content=lambda **kw: iter(chunks),
                               close=lambda: observations.append("closed"))
    client = SimpleNamespace(post=lambda *args, **kw: observations.append(kw) or response)
    transport = NativeResultReadTransport(client=client)
    def invoke():
        return transport.forward_task("http://synthetic-worker", "/api/internal/native-tasks/result", {},
                                      token="synthetic", timeout=5)
    if accepted:
        assert invoke()["status"] == "completed"
    else:
        with pytest.raises(ValueError):
            invoke()
    assert observations[-1] == "closed"
    assert observations[0]["allow_redirects"] is False and observations[0]["stream"] is True


def test_retrieval_transport_cannot_execute_and_has_no_automatic_retries():
    transport = NativeResultReadTransport()
    with pytest.raises(ValueError, match="read_only"):
        transport.forward_task("http://synthetic-worker", "/api/internal/native-tasks/execute", {},
                               token="synthetic", timeout=5)
    assert transport._client.session.get_adapter("http://").max_retries.total == 0
    transport._client.session.close()
