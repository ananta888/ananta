from __future__ import annotations

from agent.services.business_controlling_import_api_adapter import (
    BusinessControllingImportApiAdapter,
)
from agent.services.business_controlling_import_service import (
    BusinessControllingImportService,
)
from agent.services.business_controlling_import_workflow import (
    BusinessControllingImportWorkflow,
)
from agent.services.business_controlling_tabular_extractor import (
    BusinessControllingTabularExtractor,
)


class _Admission:
    def is_admitted(self, **_: object) -> bool:
        return True


class _Artifacts:
    def read_bytes(self, **_: object) -> bytes:
        return b"amount,currency\n12.30,EUR\n"


class _Profiles:
    def __init__(self) -> None:
        self.profile = None

    def append_profile(self, **kwargs: object):
        self.profile = kwargs["profile"]
        return self.profile

    def append_mapping(self, **kwargs: object):
        return kwargs["confirmation"]

    def get_profile(self, **_: object):
        return self.profile


def test_api_adapter_uses_real_admitted_import_workflow_without_raw_response() -> None:
    profiles = _Profiles()
    adapter = BusinessControllingImportApiAdapter(
        workflow=BusinessControllingImportWorkflow(
            extractor=BusinessControllingTabularExtractor(_Artifacts()),
            profiler=BusinessControllingImportService(_Admission()),
            profiles=profiles,
        ),
        profiles=profiles,
    )
    scope = {
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "actor_id": "automation",
    }

    profile = adapter.profile_import(
        **scope,
        request_payload={
            "project_id": "project-a",
            "source_revision_id": "source-revision-a",
            "revision_digest": "a" * 64,
            "source_format": "csv",
        },
    )
    mapping = adapter.confirm_mapping(
        **scope,
        request_payload={
            "project_id": "project-a",
            "profile_digest": profile["profile_digest"],
            "column_mapping": {"amount": "amount", "currency": "currency"},
        },
    )

    assert profile["row_count"] == 1
    assert "rows" not in profile
    assert mapping["column_mapping"] == {
        "amount": "amount",
        "currency": "currency",
    }
