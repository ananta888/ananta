import controller


def test_default_api_endpoints():
    endpoints = controller.default_config["api_endpoints"]
    lmstudio = [ep for ep in endpoints if ep["type"] == "lmstudio"]
    assert len(lmstudio) == 1
    ollama = [ep for ep in endpoints if ep["type"] == "ollama"]
    assert any(ep["url"].startswith("http://192.168.178.88:11434") for ep in ollama)

