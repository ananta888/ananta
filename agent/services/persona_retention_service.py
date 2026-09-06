"""User-authorized scheduling of exact, already-retired image bundles."""

import hashlib
import time

from agent.services.persona_retention_ports import RetentionGrantStorePort, RetentionPolicyPort, RetiredAssetPort
from agent.services.source_control_access_policy import HubSourcePrincipal


def asset_digest(asset):
    return hashlib.sha256(asset.model_dump_json().encode()).hexdigest()


def automation_principal(tenant, project, actor):
    # A durable grant does not preserve an old admin token or grant admin
    # privilege to the scheduler. Current project membership is always required.
    return HubSourcePrincipal(actor, tenant, project, frozenset({"user"}))


def key(principal, project, artifact_id):
    return dict(tenant_id=principal.tenant_id, project_id=project, artifact_id=artifact_id)


def public_status(record):
    return {name: record[name] for name in ("revision", "asset_revision", "due_at_ms", "state", "attempts")}


class PersonaRetentionService:
    def __init__(
        self, *, policy: RetentionPolicyPort, catalog: RetiredAssetPort, store: RetentionGrantStorePort, clock=time.time
    ):
        self.policy, self.catalog, self.store, self.clock = policy, catalog, store, clock

    def schedule(self, principal, project, artifact_id, *, asset_revision, expected_revision, delete_after_seconds):
        self.policy.require_revoke(principal, project, artifact_id)
        if (
            type(expected_revision) is not int
            or not 0 <= expected_revision < 2**53 - 1
            or type(asset_revision) is not int
            or not 1 <= asset_revision < 2**53 - 1
            or type(delete_after_seconds) is not int
            or not 60 <= delete_after_seconds <= 365 * 86400
        ):
            raise ValueError("persona_retention_request_invalid")
        automated = automation_principal(principal.tenant_id, project, principal.subject_id)
        self.policy.require_revoke(automated, project, artifact_id)
        asset, revision, state = self.catalog.get_retired(principal.tenant_id, project, artifact_id)
        if revision != asset_revision or state == "purged":
            raise ValueError("persona_retention_asset_changed")
        due = int(self.clock() * 1000) + delete_after_seconds * 1000
        record = key(principal, project, artifact_id) | dict(
            revision=expected_revision + 1,
            asset_revision=asset_revision,
            asset_digest=asset_digest(asset),
            actor=principal.subject_id,
            due_at_ms=due,
            next_attempt_ms=due,
            state="scheduled",
            attempts=0,
            task_id=None,
            lease_id=None,
            lease_until_ms=0,
        )
        self.policy.require_revoke(automated, project, artifact_id)
        self.store.install(record, expected_revision=expected_revision)
        return public_status(record)

    def status(self, principal, project, artifact_id):
        self.policy.require_revoke(principal, project, artifact_id)
        return public_status(self.store.get(key(principal, project, artifact_id)))

    def cancel(self, principal, project, artifact_id, *, expected_revision):
        self.policy.require_revoke(principal, project, artifact_id)
        if type(expected_revision) is not int or not 1 <= expected_revision < 2**53 - 1:
            raise ValueError("persona_retention_revision_invalid")
        return self.store.cancel(
            key(principal, project, artifact_id), expected_revision=expected_revision, actor=principal.subject_id
        )
