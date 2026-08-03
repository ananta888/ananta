from agent.common.gateways.http_hub_gateway import HttpHubGateway
from ananta_contracts.general_worker_capabilities import (
    GENERAL_PURPOSE_WORKER_CAPABILITIES,
)


def test_worker_registration_advertises_general_purpose_capabilities() -> None:
    gateway = HttpHubGateway(hub_url="http://hub.test")
    captured: dict[str, object] = {}

    def post(url: str, payload: dict[str, object], **_: object) -> dict[str, object]:
        captured["url"] = url
        captured["payload"] = payload
        return {}

    gateway.client.post = post  # type: ignore[method-assign]

    assert gateway.register("worker-alpha", 5000, "worker-token", silent=True)
    assert captured["url"] == "http://hub.test/register"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["capabilities"] == list(GENERAL_PURPOSE_WORKER_CAPABILITIES)
    assert "source_analysis" in payload["capabilities"]
