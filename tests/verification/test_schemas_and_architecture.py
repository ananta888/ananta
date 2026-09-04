from __future__ import annotations

import ast
import json
from pathlib import Path

import jsonschema
import yaml

from ananta_contracts.verification import VerificationStatus
from tests.verification.helpers import assignment

ROOT = Path(__file__).parents[2]


def test_contract_payload_validates_against_assignment_schema() -> None:
    schema = json.loads((ROOT / "schemas/verification/assignment.v1.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(assignment().to_dict())


def test_report_schema_exposes_every_closed_runtime_status() -> None:
    schema = json.loads((ROOT / "schemas/verification/report.v1.schema.json").read_text())
    assert set(schema["properties"]["status"]["enum"]) == {status.value for status in VerificationStatus}


def test_hub_modules_do_not_import_optional_verification_tools() -> None:
    forbidden = {"hypothesis", "crosshair", "hypothesis_crosshair", "z3"}
    for path in [ROOT / "agent", ROOT / "ananta_contracts"]:
        for source in path.rglob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            imports = {
                node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
            }
            imports |= {
                alias.name.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            assert not imports & forbidden, f"{source} imports optional verification runtime"


def test_verification_worker_does_not_import_hub_or_agent_modules() -> None:
    for source in (ROOT / "worker/verification").glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        imports = {
            node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
        }
        imports |= {
            alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
        }
        assert not imports & {"agent", "hub"}, f"{source} crosses Worker execution boundary"


def test_catalog_has_fifteen_assessed_candidates_and_authoritative_properties() -> None:
    catalog = json.loads((ROOT / "config/verification/property-catalog.v1.json").read_text())
    candidates = catalog["candidates"]
    assert len(candidates) >= 15
    required = {"symbol", "class", "property", "origin", "side_effects", "determinism", "typing", "runtime", "eligible"}
    assert all(set(candidate) == required for candidate in candidates)
    authoritative = {"existing_contract", "existing_test", "schema_constraint", "documented_invariant"}
    assert sum(candidate["origin"] in authoritative for candidate in candidates) >= 5


def test_container_profile_has_no_network_secrets_or_host_control_mounts() -> None:
    compose = yaml.safe_load((ROOT / "docker/compose-next/compose.verification-worker.yml").read_text())
    service = compose["services"]["verification-worker"]
    assert service["network_mode"] == "none"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["user"] == "65532:65532"
    assert all("docker.sock" not in volume and ".env" not in volume for volume in service["volumes"])
    assert set(service["environment"]) == {"PYTHONHASHSEED"}
