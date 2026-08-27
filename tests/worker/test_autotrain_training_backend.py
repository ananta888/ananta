from worker.training.backends.autotrain import AutoTrainTrainingBackend


def test_autotrain_is_explicitly_unmaintained_and_experimental() -> None:
    backend = AutoTrainTrainingBackend(
        package_version=lambda _name: "0.8.36", executable_resolver=lambda _name: "/bin/true"
    )
    capability = backend.capability()
    assert capability.maintenance == "unmaintained"
    assert capability.maturity == "experimental"
