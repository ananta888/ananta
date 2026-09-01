from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
from email.message import Message

import pytest

from agent.services.spreadsheet_worker_port import (
    HttpSpreadsheetExecutionAdapter,
    SpreadsheetWorkerTransportError,
    normalize_spreadsheet_worker_endpoint,
)
from ananta_contracts.spreadsheet_studio import canonical_digest, validate_action
from tests.spreadsheet_studio.helpers import snapshot
from worker.spreadsheet.libreoffice_executor import LibreOfficeSpreadsheetExecutor


class _Response:
    def __init__(self, payload: dict) -> None:
        self._body = io.BytesIO(json.dumps(payload).encode())
        self.headers = Message()
        self.headers["Content-Type"] = "application/json"

    def read(self, limit: int = -1) -> bytes:
        return self._body.read(limit)


class _Opener:
    def __init__(self) -> None:
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        if request.full_url.endswith("/capabilities"):
            return _Response(
                {
                    "contract": "ananta.spreadsheet-worker.v1",
                    "state": "available",
                    "engine": "libreoffice-calc",
                    "production_fidelity": True,
                }
            )
        body = json.loads(request.data)
        return _Response(
            {
                "contract": "ananta.spreadsheet-worker.v1",
                "request_digest": body["request_digest"],
                "status": "succeeded",
                "result": {"schema": "ananta.spreadsheet-execution-result.v1"},
            }
        )


def _adapter(opener: _Opener) -> HttpSpreadsheetExecutionAdapter:
    endpoint = "http://spreadsheet-worker:8097/internal/v1/spreadsheet"
    return HttpSpreadsheetExecutionAdapter(
        endpoint=endpoint,
        allowed_endpoints=(endpoint,),
        bearer_token="spreadsheet-test-token-00000000",
        resolver=lambda _host, _port: ("172.28.0.7",),
        opener=opener,
    )


def test_worker_port_binds_endpoint_request_and_result() -> None:
    opener = _Opener()
    adapter = _adapter(opener)
    assert adapter.capability["production_fidelity"] is True
    result = adapter.dry_run(
        snapshot=snapshot(),
        actions=(
            {
                "action_id": "action-one",
                "kind": "set_value",
                "sheet_id": "sheet-one",
                "cell": "A1",
                "value": 42,
                "formula": None,
            },
        ),
    )
    assert result["schema"] == "ananta.spreadsheet-execution-result.v1"
    assert opener.requests[-1].get_header("Authorization") == "Bearer spreadsheet-test-token-00000000"
    assert opener.requests[-1].host == "172.28.0.7:8097"
    assert opener.requests[-1].get_header("Host") == "spreadsheet-worker:8097"


def test_worker_port_digest_binds_the_immutable_source_copy() -> None:
    opener = _Opener()
    adapter = _adapter(opener)
    content = b"source-workbook"

    adapter.dry_run(
        snapshot=snapshot(),
        actions=(
            {
                "action_id": "action-one",
                "kind": "set_value",
                "sheet_id": "sheet-one",
                "cell": "A1",
                "value": 42,
                "formula": None,
            },
        ),
        source_artifact={
            "content": content,
            "filename": "source.xlsx",
            "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "sha256": hashlib.sha256(content).hexdigest(),
        },
    )

    body = json.loads(opener.requests[-1].data)
    assert base64.b64decode(body["source_artifact"]["content_base64"]) == content
    unsigned = {key: value for key, value in body.items() if key != "request_digest"}
    assert body["request_digest"] == canonical_digest(unsigned)


def test_worker_port_rejects_non_private_resolution_and_url_ambiguity() -> None:
    endpoint = "http://spreadsheet-worker:8097/internal/v1/spreadsheet"
    adapter = HttpSpreadsheetExecutionAdapter(
        endpoint=endpoint,
        allowed_endpoints=(endpoint,),
        bearer_token="spreadsheet-test-token-00000000",
        resolver=lambda _host, _port: ("203.0.113.7",),
        opener=_Opener(),
    )
    with pytest.raises(SpreadsheetWorkerTransportError, match="worker_address_forbidden"):
        _ = adapter.capability
    with pytest.raises(ValueError, match="endpoint_invalid"):
        normalize_spreadsheet_worker_endpoint(f"{endpoint}?redirect=http://example.test")


@pytest.mark.integration
def test_real_libreoffice_recalculates_and_returns_a_digest_bound_diff() -> None:
    if not (shutil.which("libreoffice") or shutil.which("soffice")):
        pytest.skip("LibreOffice is not installed")
    executor = LibreOfficeSpreadsheetExecutor(network_isolated=True)
    value = snapshot()
    value["sheets"][0]["cells"].append(
        {
            "address": "C1",
            "value": 2,
            "formula": {
                "op": "add",
                "left": {"op": "cell", "sheet_id": "sheet-one", "cell": "A1"},
                "right": {"op": "literal", "value": 1},
            },
            "style_ref": None,
        }
    )
    result = executor.dry_run(
        snapshot=value,
        actions=(
            {
                "action_id": "action-one",
                "kind": "set_value",
                "sheet_id": "sheet-one",
                "cell": "A1",
                "value": 42,
                "formula": None,
            },
        ),
    )
    cells = {cell["address"]: cell for cell in result["candidate_snapshot"]["sheets"][0]["cells"]}
    assert cells["A1"]["value"] == 42
    assert cells["C1"]["value"] == 43
    assert result["recalculation_performed"] is True
    assert result["production_fidelity"] is True
    assert {item["cell"] for item in result["diff"]} == {"A1", "C1"}
    assert next(item for item in result["diff"] if item["cell"] == "C1")["direct"] is False


@pytest.mark.integration
def test_real_libreoffice_applies_bounded_range_copy_format_and_clear() -> None:
    if not (shutil.which("libreoffice") or shutil.which("soffice")):
        pytest.skip("LibreOffice is not installed")
    executor = LibreOfficeSpreadsheetExecutor(network_isolated=True)
    actions = tuple(
        validate_action(action)
        for action in (
            {
                "action_id": "copy",
                "kind": "copy_range",
                "source_sheet_id": "sheet-one",
                "source_start": "A1",
                "source_end": "B1",
                "target_sheet_id": "sheet-one",
                "target_start": "A2",
            },
            {
                "action_id": "format",
                "kind": "format_range",
                "sheet_id": "sheet-one",
                "start": "A2",
                "end": "B2",
                "style": {"number_format": "0.00", "bold": True, "italic": None, "fill_color": "FFCC00"},
            },
            {
                "action_id": "clear",
                "kind": "clear_range",
                "sheet_id": "sheet-one",
                "start": "B1",
                "end": "B1",
            },
        )
    )

    result = executor.dry_run(snapshot=snapshot(), actions=actions)

    cells = {cell["address"]: cell for cell in result["candidate_snapshot"]["sheets"][0]["cells"]}
    assert cells["A2"]["value"] == 1
    assert cells["B2"]["value"] == "safe"
    assert cells["A2"]["style_ref"].startswith("style-")
    assert "B1" not in cells
    assert {action_id for item in result["diff"] for action_id in item["action_ids"]} == {"copy", "format", "clear"}
