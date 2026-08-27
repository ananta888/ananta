from worker.training.backends.axolotl import AxolotlTrainingBackend


def test_axolotl_pin_and_identity() -> None:
    backend = AxolotlTrainingBackend(
        package_version=lambda _name: "0.18.0", executable_resolver=lambda _name: "/bin/true"
    )
    assert backend.name == "axolotl"
    assert backend.capability().backend_version == "0.18.0"
