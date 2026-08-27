from worker.training.backends.llamafactory import LlamaFactoryTrainingBackend


def test_llamafactory_pin_and_no_webui() -> None:
    backend = LlamaFactoryTrainingBackend(
        package_version=lambda _name: "0.9.5", executable_resolver=lambda _name: "/bin/true"
    )
    assert backend.name == "llamafactory"
    assert backend.capability().backend_version == "0.9.5"
