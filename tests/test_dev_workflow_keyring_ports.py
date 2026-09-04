from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts.dev_workflow_keyring_contract import _ALL_DOCUMENTS, WorkerRegistrationSpec
from scripts.dev_workflow_keyring_orchestrator import bootstrap
from scripts.dev_workflow_keyring_ports import KeyringBootstrapDependencies


def test_bootstrap_orchestrator_accepts_in_memory_ports_without_root_access() -> None:
    paths = {"root": Path("/not-accessed")}
    paths.update({name: Path("/not-accessed") / name for name in _ALL_DOCUMENTS})
    calls: list[tuple[str, object]] = []
    specs = (
        WorkerRegistrationSpec("alpha", "alpha-id", "http://alpha"),
        WorkerRegistrationSpec("beta", "beta-id", "http://beta"),
    )

    filesystem = SimpleNamespace(
        paths=lambda root: calls.append(("paths", root)) or paths,
        prepare=lambda value: calls.append(("prepare", value)),
        assert_safe=lambda value: calls.append(("assert_safe", value)),
        existing=lambda _paths, _names: frozenset(),
        read_identity_secrets=lambda _paths: {},
        write_json=lambda *_args, **_kwargs: None,
    )
    material = SimpleNamespace(
        worker_specs=lambda **_ids: specs,
        all_documents=lambda **_kwargs: {name: name for name in _ALL_DOCUMENTS},
        identity_documents=lambda **_kwargs: {},
        source_access_documents=lambda: {},
        registration_document=lambda *_args, **_kwargs: {},
    )
    transaction = SimpleNamespace(
        recover=lambda value, **_kwargs: calls.append(("recover", value)),
        publish=lambda value, **kwargs: calls.append(("publish", (value, kwargs))),
    )
    validation = SimpleNamespace(validate=lambda *_args, **_kwargs: False)

    result = bootstrap(
        Path("/virtual-root"),
        dependencies=KeyringBootstrapDependencies(
            filesystem=filesystem,
            material=material,
            validation=validation,
            transaction=transaction,
        ),
    )

    assert result == "created"
    assert [name for name, _payload in calls] == ["paths", "prepare", "recover", "assert_safe", "publish"]
    publish = calls[-1][1][1]
    assert publish["target_names"] == _ALL_DOCUMENTS
    assert publish["mode"] == "create"
    assert publish["worker_specs"] == specs
