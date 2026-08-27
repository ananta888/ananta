from __future__ import annotations

import importlib.metadata
import json

import pytest

from worker.training import backend_installation


def test_isolated_backend_identity_is_closed_and_version_bound(tmp_path, monkeypatch) -> None:
    inventory = tmp_path / "training-backend-identity.json"
    inventory.write_text(json.dumps({"package": "axolotl", "version": "0.18.0"}), encoding="utf-8")
    monkeypatch.setattr(backend_installation, "_INVENTORY_PATH", inventory)
    monkeypatch.setattr(
        backend_installation.importlib.metadata,
        "version",
        lambda _name: (_ for _ in ()).throw(importlib.metadata.PackageNotFoundError()),
    )

    assert backend_installation.installed_package_version("axolotl") == "0.18.0"
    with pytest.raises(importlib.metadata.PackageNotFoundError):
        backend_installation.installed_package_version("torchtune")


def test_isolated_backend_identity_rejects_unknown_fields(tmp_path, monkeypatch) -> None:
    inventory = tmp_path / "training-backend-identity.json"
    inventory.write_text(
        json.dumps({"package": "axolotl", "version": "0.18.0", "command": "unsafe"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(backend_installation, "_INVENTORY_PATH", inventory)
    monkeypatch.setattr(
        backend_installation.importlib.metadata,
        "version",
        lambda _name: (_ for _ in ()).throw(importlib.metadata.PackageNotFoundError()),
    )

    with pytest.raises(importlib.metadata.PackageNotFoundError):
        backend_installation.installed_package_version("axolotl")
