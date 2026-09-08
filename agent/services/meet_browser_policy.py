"""Explicit Hub-owned public browser permissions, separate from Meet membership."""

from dataclasses import dataclass
from types import MappingProxyType

from agent.services.browser_policy_service import BrowserPolicyService
from agent.services.browser_task_contract import BrowserTaskContract
from agent.services.meet_contract import MeetError
from ananta_contracts.browser_public_fetch import PublicFetchTarget, validate_fetch_request
from ananta_contracts.meet_browser_workspace import identifier, integer, validate_browser_job


@dataclass(frozen=True)
class PublicBrowserPolicy:
    policy_id: str
    revision: int
    allowed_origins: tuple[str, ...]
    operations: frozenset[str]


class MeetBrowserPolicy:
    def __init__(self, rows, *, browser_policy=None):
        if type(rows) is not list or len(rows) > 256:
            raise ValueError("meet_browser_policy_invalid")
        policies = {}
        for row in rows:
            if type(row) is not dict or set(row) != {
                "tenant_id",
                "project_id",
                "owner_subject",
                "policy_id",
                "revision",
                "allowed_origins",
                "operations",
            }:
                raise ValueError("meet_browser_policy_invalid")
            scope = tuple(identifier(row[name]) for name in ("tenant_id", "project_id", "owner_subject"))
            policy_id = identifier(row["policy_id"])
            if scope in policies or policy_id.startswith(("SRC_", "RUN_")):
                raise ValueError("meet_browser_policy_invalid")
            origins, operations = row["allowed_origins"], row["operations"]
            if type(origins) is not list or not origins:
                raise ValueError("meet_browser_policy_invalid")
            validate_fetch_request(
                {"schema": "ananta.browser-public-fetch.v1", "url": origins[0], "allowed_origins": origins}
            )
            if type(operations) is not list or not operations or any(type(op) is not str for op in operations):
                raise ValueError("meet_browser_policy_invalid")
            if len(set(operations)) != len(operations) or not set(operations) <= {"navigate", "present"}:
                raise ValueError("meet_browser_policy_invalid")
            policies[scope] = PublicBrowserPolicy(
                policy_id, integer(row["revision"], 2**31 - 1), tuple(origins), frozenset(operations)
            )
        self.policies = MappingProxyType(policies)
        self.browser_policy = browser_policy if browser_policy is not None else BrowserPolicyService()

    def require(self, scope, operation):
        policy = self.policies.get((scope.tenant_id, scope.project_id, scope.owner_subject))
        if (
            policy is None
            or operation not in policy.operations
            or getattr(scope, "browser_workspace", False) is not True
            or "screen.publish" not in scope.capabilities
        ):
            raise MeetError("meet_browser_policy_denied", 403)
        return policy

    def navigation(self, scope, url):
        policy = self.require(scope, "navigate")
        try:
            request = validate_fetch_request(
                {
                    "schema": "ananta.browser-public-fetch.v1",
                    "url": url,
                    "allowed_origins": list(policy.allowed_origins),
                }
            )
            contract = BrowserTaskContract.from_payload(
                {
                    "allowed_domains": [PublicFetchTarget.parse(origin).hostname for origin in policy.allowed_origins],
                    "max_actions": 1,
                    "timeout_seconds": 30,
                    "download_policy": "deny",
                    "auth_policy": "none",
                    "screenshot_policy": "none",
                    "persist_session": False,
                }
            )
            if not all(
                (
                    self.browser_policy.enforce_domain(url=url, contract=contract).allow,
                    self.browser_policy.enforce_action_budget(action_count=1, contract=contract).allow,
                    self.browser_policy.enforce_auth_usage(requested=False, contract=contract).allow,
                    self.browser_policy.enforce_session_persistence(requested=False, contract=contract).allow,
                )
            ):
                raise ValueError()
        except (ValueError, TypeError):
            raise MeetError("meet_browser_navigation_denied", 403) from None
        return policy, request

    def current_job(self, scope, job, *, present=False):
        try:
            job = validate_browser_job(job)
            policy, request = self.navigation(scope, job["fetch"]["url"])
            if present:
                self.require(scope, "present")
            if (job["policy_id"], job["policy_revision"], job["fetch"]) != (policy.policy_id, policy.revision, request):
                raise ValueError()
            if (
                job["tenant_id"],
                job["project_id"],
                job["parent_task_id"],
                job["parent_lease_id"],
                job["runtime_id"],
                job["session_id"],
            ) != (scope.tenant_id, scope.project_id, scope.task_id, scope.lease_id, scope.runtime_id, scope.session_id):
                raise ValueError()
        except (ValueError, TypeError):
            raise MeetError("meet_browser_policy_denied", 403) from None
        return policy
