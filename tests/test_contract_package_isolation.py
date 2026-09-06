"""Worker wire imports stay dependency-free; legacy root exports keep identity."""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout(20)


def test_closed_worker_contracts_import_without_site_packages():
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            (
                "import sys; import ananta_contracts.hub_evidence; "
                "assert 'jsonschema' not in sys.modules; assert 'PIL' not in sys.modules; "
                "assert 'ananta_contracts.file_type_support' not in sys.modules"
            ),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr


def test_every_legacy_root_export_is_the_original_domain_object():
    import ananta_contracts

    assert set(ananta_contracts.__all__) == set(ananta_contracts._EXPORT_MODULE)
    for name in ananta_contracts.__all__:
        module = importlib.import_module(f"ananta_contracts.{ananta_contracts._EXPORT_MODULE[name]}")
        assert getattr(ananta_contracts, name) is getattr(module, name)
        assert name in dir(ananta_contracts)
    with pytest.raises(AttributeError):
        getattr(ananta_contracts, "not_an_export")
