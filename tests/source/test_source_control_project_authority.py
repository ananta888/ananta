from __future__ import annotations

import pytest
from flask import Flask

from agent.routes.source_control_access import (
    SourceControlProjectScopeError,
    bind_source_control_project_selector,
)
from agent.services.project_access_authority import (
    AuthorizedProjectScope,
    ProjectAccessError,
    ProjectCapability,
)
from agent.services.source_control_access_policy import HubSourcePrincipal


class _ProjectAuthority:
    def __init__(self, status_code: int | None = None) -> None:
        self.status_code = status_code

    def require(
        self,
        *,
        tenant_id: str,
        project_id: str,
        subject_id: str,
        capability: ProjectCapability,
        tenant_admin: bool = False,
        include_archived: bool = False,
    ) -> AuthorizedProjectScope:
        del tenant_admin, include_archived
        if self.status_code is not None:
            reason = {
                403: "project_access_denied",
                404: "project_not_found",
                409: "project_archived",
            }[self.status_code]
            raise ProjectAccessError(
                reason_code=reason,
                public_status=self.status_code,
                tenant_id=tenant_id,
                project_id=project_id,
            )
        return AuthorizedProjectScope(
            tenant_id=tenant_id,
            project_id=project_id,
            team_id=project_id,
            subject_id=subject_id,
            role="maintainer",
            status="active",
            capability=capability,
            lock_version=1,
        )


def _principal() -> HubSourcePrincipal:
    return HubSourcePrincipal(
        subject_id="user-a",
        tenant_id="tenant-a",
        project_id=None,
        roles=frozenset({"user"}),
    )


def test_projectless_session_principal_can_bind_authorized_selector():
    app = Flask(__name__)
    app.extensions["project_access_authority"] = _ProjectAuthority()
    with app.test_request_context("/sources?project_id=project-a"):
        scoped = bind_source_control_project_selector(
            "project-a",
            principal=_principal(),
        )

    assert scoped.project_id == "project-a"
    assert "project_maintainer" in scoped.roles
    assert "project_owner" not in scoped.roles


@pytest.mark.parametrize("status_code", [403, 404, 409])
def test_authority_status_is_preserved_at_selector_boundary(status_code: int):
    app = Flask(__name__)
    app.extensions["project_access_authority"] = _ProjectAuthority(status_code)
    with app.test_request_context("/sources?project_id=project-a"):
        with pytest.raises(SourceControlProjectScopeError) as captured:
            bind_source_control_project_selector(
                "project-a",
                principal=_principal(),
            )

    assert captured.value.status_code == status_code
