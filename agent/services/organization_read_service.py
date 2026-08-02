"""Bounded Organization read models and principal-local layout preferences."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlmodel import Session, select

from agent.common.agent_endpoint import safe_agent_endpoint_for_display
from agent.db_models.agents import AgentInfoDB
from agent.db_models.organizations import (
    OrganizationInstanceDB,
    OrganizationLayoutPreferenceDB,
    OrganizationMembershipDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    OrganizationTopologySnapshotDB,
    OrganizationUnitDB,
)
from agent.services.organization_active_work_service import SqlOrganizationActiveWorkService
from agent.services.organization_assignment_eligibility_service import (
    OrganizationAssignmentEligibilityService,
)
from agent.services.organization_definition_catalog_service import (
    OrganizationDefinitionCatalogService,
)
from agent.services.organization_lifecycle_service import OrganizationActivitySnapshot
from agent.services.organization_projection_service import OrganizationProjectionService
from agent.services.organization_unit_of_work import OrganizationUnitOfWork


class OrganizationReadError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class OrganizationReadService:
    def __init__(
        self,
        *,
        catalog: OrganizationDefinitionCatalogService,
        session_factory: Callable[[], Session] | None = None,
        assignment_eligibility: OrganizationAssignmentEligibilityService | None = None,
        cursor_secret: str | bytes | None = None,
    ) -> None:
        self._catalog = catalog
        self._session_factory = session_factory or self._default_session
        self._assignment_eligibility = assignment_eligibility or OrganizationAssignmentEligibilityService()
        if isinstance(cursor_secret, str):
            cursor_secret = cursor_secret.encode("utf-8")
        self._cursor_secret = bytes(cursor_secret or b"")

    @staticmethod
    def _default_session() -> Session:
        from agent.database import engine

        return Session(engine)

    def list_organizations(
        self,
        *,
        tenant_id: str,
        project_id: str,
        principal_id: str,
        cursor: str | None,
        page_size: int,
    ) -> dict[str, Any]:
        if isinstance(page_size, bool) or not 1 <= page_size <= 100:
            raise OrganizationReadError("organization_page_size_invalid")
        after = self._decode_cursor(
            cursor,
            tenant_id=tenant_id,
            project_id=project_id,
            principal_id=principal_id,
        )
        with self._session_factory() as session:
            statement = (
                select(OrganizationInstanceDB)
                .join(
                    OrganizationMembershipDB,
                    (OrganizationMembershipDB.tenant_id == OrganizationInstanceDB.tenant_id)
                    & (OrganizationMembershipDB.project_id == OrganizationInstanceDB.project_id)
                    & (OrganizationMembershipDB.organization_id == OrganizationInstanceDB.organization_id),
                )
                .where(OrganizationInstanceDB.tenant_id == tenant_id)
                .where(OrganizationInstanceDB.project_id == project_id)
                .where(OrganizationMembershipDB.principal_id == principal_id)
                .where(
                    (OrganizationMembershipDB.expires_at.is_(None))
                    | (OrganizationMembershipDB.expires_at > time.time())
                )
                .order_by(OrganizationInstanceDB.organization_id)
                .limit(page_size + 1)
            )
            if after:
                statement = statement.where(OrganizationInstanceDB.organization_id > after)
            rows = list(session.exec(statement).all())
            has_more = len(rows) > page_size
            rows = rows[:page_size]
            items = self._summaries(session, rows)
        next_cursor = (
            self._encode_cursor(
                rows[-1].organization_id,
                tenant_id=tenant_id,
                project_id=project_id,
                principal_id=principal_id,
            )
            if has_more and rows
            else None
        )
        return {"items": items, "next_cursor": next_cursor}

    def organization_summary(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            organization = session.exec(
                select(OrganizationInstanceDB)
                .where(OrganizationInstanceDB.tenant_id == tenant_id)
                .where(OrganizationInstanceDB.project_id == project_id)
                .where(OrganizationInstanceDB.organization_id == organization_id)
            ).first()
            if organization is None:
                raise OrganizationReadError("organization_not_found")
            return self._summaries(session, [organization])[0]

    def topology(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization: OrganizationInstanceDB,
        include_runtime: bool,
        cursor: str | None,
        page_size: int | None,
        depth: int | None,
        filters: Mapping[str, Any],
    ) -> dict[str, Any]:
        limits = self._catalog.resolve_limit_profile(
            tenant_id=tenant_id,
            project_id=project_id,
            policy_ref=organization.effective_limit_profile_ref,
        )
        # The web client uses depth=0 for its root-only initial query.  The
        # projection contract starts at one because the organization root is
        # always emitted independently of unit depth.
        normalized_depth = 1 if depth == 0 else depth
        with OrganizationUnitOfWork(session_factory=self._session_factory) as uow:
            result = OrganizationProjectionService(topology_reader=uow.topology).project(
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization.organization_id,
                limits=limits,
                include_runtime_overlay=include_runtime,
                cursor=cursor,
                page_size=page_size,
                max_depth=normalized_depth,
                filters=dict(filters),
            )
        if depth == 0:
            # Public projection queries define depth 0 as the organization
            # root.  The core projector's bounded repository depth starts at
            # one, so adapt the result without weakening its invariant.
            result["nodes"] = [node for node in result["nodes"] if node.get("kind") == "organization"]
            result["edges"] = []
            overlay = result.get("runtime_overlay")
            if isinstance(overlay, dict):
                overlay["nodes"] = [
                    node
                    for node in list(overlay.get("nodes") or [])
                    if node.get("node_id") == organization.organization_id
                ]
                overlay["edges"] = []
            result["next_cursor"] = None
            result["truncated"] = False
        return result

    def role_slots(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            slots = list(
                session.exec(
                    select(OrganizationRoleSlotDB)
                    .where(OrganizationRoleSlotDB.tenant_id == tenant_id)
                    .where(OrganizationRoleSlotDB.project_id == project_id)
                    .where(OrganizationRoleSlotDB.organization_id == organization_id)
                    .where(OrganizationRoleSlotDB.lifecycle == "active")
                    .order_by(OrganizationRoleSlotDB.unit_id, OrganizationRoleSlotDB.slot_key)
                ).all()
            )
            assignments = self._assignments(session, tenant_id, project_id, organization_id)
            agent_by_url = {agent.url: agent for agent in session.exec(select(AgentInfoDB)).all()}
            capacity = Counter(
                row.agent_url
                for row in session.exec(
                    select(OrganizationRoleAssignmentDB).where(
                        OrganizationRoleAssignmentDB.lifecycle.in_(("proposed", "active"))
                    )
                ).all()
            )
            return [
                self._role_slot_view(
                    slot,
                    assignments=assignments.get(slot.id, []),
                    agent_by_url=agent_by_url,
                    capacity=capacity,
                )
                for slot in slots
            ]

    def assignment_candidates(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        role_slot_id: str,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            slot = session.exec(
                select(OrganizationRoleSlotDB)
                .where(OrganizationRoleSlotDB.tenant_id == tenant_id)
                .where(OrganizationRoleSlotDB.project_id == project_id)
                .where(OrganizationRoleSlotDB.organization_id == organization_id)
                .where(OrganizationRoleSlotDB.id == role_slot_id)
            ).first()
            if slot is None:
                raise OrganizationReadError("organization_role_slot_not_found")
            template = (
                self._catalog.get_role_template(
                    slot.role_template_key,
                    slot.role_template_version,
                )
                or {}
            )
            required = set(
                dict(slot.assignment_policy or {}).get("required_capabilities")
                or dict(template.get("capability_policy") or {}).get("required")
                or []
            )
            forbidden = set(dict(slot.assignment_policy or {}).get("forbidden_capabilities") or [])
            principal_kind_allowed = "agent" in set(dict(slot.assignment_policy or {}).get("principal_kinds") or [])
            write_access_required = bool(dict(slot.assignment_policy or {}).get("write_access_required"))
            assigned_rows = list(
                session.exec(
                    select(OrganizationRoleAssignmentDB).where(
                        OrganizationRoleAssignmentDB.lifecycle.in_(("proposed", "active"))
                    )
                ).all()
            )
            capacity = Counter(row.agent_url for row in assigned_rows)
            affected_teams = self._affected_teams(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
                unit_id=slot.unit_id,
            )
            agents = list(
                session.exec(
                    select(AgentInfoDB)
                    .where(AgentInfoDB.registration_validated == True)  # noqa: E712
                    .where(AgentInfoDB.status == "online")
                    .order_by(AgentInfoDB.name, AgentInfoDB.url)
                ).all()
            )
            return [
                self._candidate_view(
                    agent,
                    required=required,
                    forbidden=forbidden,
                    capacity_used=capacity[agent.url],
                    affected_teams=affected_teams,
                    principal_kind_allowed=principal_kind_allowed,
                    write_access_required=write_access_required,
                )
                for agent in agents
                if self._safe_assignment_agent_url(agent.url)
            ]

    def layout_preferences(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        principal_id: str,
        projection_mode: str,
    ) -> list[dict[str, Any]]:
        mode = self._projection_mode(projection_mode)
        with self._session_factory() as session:
            row = session.exec(
                select(OrganizationLayoutPreferenceDB)
                .where(OrganizationLayoutPreferenceDB.tenant_id == tenant_id)
                .where(OrganizationLayoutPreferenceDB.project_id == project_id)
                .where(OrganizationLayoutPreferenceDB.organization_id == organization_id)
                .where(OrganizationLayoutPreferenceDB.principal_id == principal_id)
                .where(OrganizationLayoutPreferenceDB.projection_mode == mode)
            ).first()
            preferences = dict(row.layout_json or {}).get("preferences", []) if row else []
            return [dict(value) for value in preferences if isinstance(value, Mapping)]

    def save_layout_preferences(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization: OrganizationInstanceDB,
        principal_id: str,
        projection_mode: str,
        preferences: Sequence[Mapping[str, Any]],
    ) -> dict[str, int]:
        mode = self._projection_mode(projection_mode)
        limits = self._catalog.resolve_limit_profile(
            tenant_id=tenant_id,
            project_id=project_id,
            policy_ref=organization.effective_limit_profile_ref,
        )
        if len(preferences) > limits.canvas_render_node_limit:
            raise OrganizationReadError("organization_layout_limit_exceeded")
        with self._session_factory() as session:
            valid_ids = self._topology_node_ids(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization.organization_id,
            )
            normalized: list[dict[str, Any]] = []
            seen: set[str] = set()
            for raw in preferences:
                if not isinstance(raw, Mapping):
                    raise OrganizationReadError("organization_layout_preference_invalid")
                node_id = str(raw.get("node_id") or "").strip()
                x, y = raw.get("x"), raw.get("y")
                if (
                    node_id not in valid_ids
                    or node_id in seen
                    or isinstance(x, bool)
                    or isinstance(y, bool)
                    or not isinstance(x, (int, float))
                    or not isinstance(y, (int, float))
                    or not math.isfinite(float(x))
                    or not math.isfinite(float(y))
                    or abs(float(x)) > 1_000_000
                    or abs(float(y)) > 1_000_000
                    or ("collapsed" in raw and not isinstance(raw["collapsed"], bool))
                ):
                    raise OrganizationReadError("organization_layout_preference_invalid")
                seen.add(node_id)
                normalized.append(
                    {
                        "node_id": node_id,
                        "x": float(x),
                        "y": float(y),
                        **({"collapsed": raw["collapsed"]} if "collapsed" in raw else {}),
                    }
                )
            row = session.exec(
                select(OrganizationLayoutPreferenceDB)
                .where(OrganizationLayoutPreferenceDB.tenant_id == tenant_id)
                .where(OrganizationLayoutPreferenceDB.project_id == project_id)
                .where(OrganizationLayoutPreferenceDB.organization_id == organization.organization_id)
                .where(OrganizationLayoutPreferenceDB.principal_id == principal_id)
                .where(OrganizationLayoutPreferenceDB.projection_mode == mode)
            ).first()
            if row is None:
                row = OrganizationLayoutPreferenceDB(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    organization_id=organization.organization_id,
                    principal_id=principal_id,
                    projection_mode=mode,
                    definition_revision=organization.definition_revision,
                )
            row.definition_revision = organization.definition_revision
            row.layout_json = {"preferences": normalized}
            row.updated_at = time.time()
            session.add(row)
            session.commit()
        return {"saved": len(normalized)}

    def activity_snapshot(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
    ) -> OrganizationActivitySnapshot:
        with self._session_factory() as session:
            activity = SqlOrganizationActiveWorkService().snapshot(
                session=session,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
            )
            assignments = list(
                session.exec(
                    select(OrganizationRoleAssignmentDB)
                    .where(OrganizationRoleAssignmentDB.tenant_id == tenant_id)
                    .where(OrganizationRoleAssignmentDB.project_id == project_id)
                    .where(OrganizationRoleAssignmentDB.organization_id == organization_id)
                    .where(OrganizationRoleAssignmentDB.lifecycle == "active")
                ).all()
            )
        return OrganizationActivitySnapshot(
            running_task_ids=activity.running_task_ids,
            active_lease_ids=activity.active_lease_ids,
            open_gate_ids=activity.open_gate_ids,
            open_handoff_ids=activity.open_handoff_ids,
            active_assignment_ids=tuple(sorted(row.id for row in assignments)),
        )

    @staticmethod
    def _summaries(session: Session, organizations: Sequence[OrganizationInstanceDB]) -> list[dict[str, Any]]:
        scope_keys = sorted({(row.tenant_id, row.project_id, row.organization_id) for row in organizations})
        if not scope_keys:
            return []
        units = list(
            session.exec(
                select(OrganizationUnitDB).where(
                    sa.tuple_(
                        OrganizationUnitDB.tenant_id,
                        OrganizationUnitDB.project_id,
                        OrganizationUnitDB.organization_id,
                    ).in_(scope_keys)
                )
            ).all()
        )
        links = list(
            session.exec(
                select(OrganizationTeamLinkDB).where(
                    sa.tuple_(
                        OrganizationTeamLinkDB.tenant_id,
                        OrganizationTeamLinkDB.project_id,
                        OrganizationTeamLinkDB.organization_id,
                    ).in_(scope_keys)
                )
            ).all()
        )
        snapshots = list(
            session.exec(
                select(OrganizationTopologySnapshotDB)
                .where(
                    sa.tuple_(
                        OrganizationTopologySnapshotDB.tenant_id,
                        OrganizationTopologySnapshotDB.project_id,
                        OrganizationTopologySnapshotDB.organization_id,
                    ).in_(scope_keys)
                )
                .order_by(
                    OrganizationTopologySnapshotDB.tenant_id,
                    OrganizationTopologySnapshotDB.project_id,
                    OrganizationTopologySnapshotDB.organization_id,
                    OrganizationTopologySnapshotDB.revision.desc(),
                )
            ).all()
        )
        unit_counts = Counter(
            (row.tenant_id, row.project_id, row.organization_id) for row in units if row.lifecycle != "archived"
        )
        team_counts = Counter(
            (row.tenant_id, row.project_id, row.organization_id) for row in links if row.lifecycle != "archived"
        )
        latest_snapshot: dict[tuple[str, str, str], str] = {}
        for row in snapshots:
            latest_snapshot.setdefault(
                (row.tenant_id, row.project_id, row.organization_id),
                row.snapshot_hash,
            )
        return [
            {
                "id": row.organization_id,
                "key": row.definition_key,
                "title": row.name,
                "lifecycle": row.lifecycle,
                "definition_revision": row.definition_revision,
                "snapshot_hash": latest_snapshot.get(
                    (row.tenant_id, row.project_id, row.organization_id),
                    row.plan_digest,
                ),
                "team_count": team_counts[(row.tenant_id, row.project_id, row.organization_id)],
                "unit_count": unit_counts[(row.tenant_id, row.project_id, row.organization_id)],
                "project_id": row.project_id,
                "tenant_id": row.tenant_id,
                "lock_version": row.lock_version,
                "revision": str(row.lock_version),
            }
            for row in organizations
        ]

    @staticmethod
    def _assignments(session, tenant_id, project_id, organization_id):
        grouped: dict[str, list[OrganizationRoleAssignmentDB]] = defaultdict(list)
        for row in session.exec(
            select(OrganizationRoleAssignmentDB)
            .where(OrganizationRoleAssignmentDB.tenant_id == tenant_id)
            .where(OrganizationRoleAssignmentDB.project_id == project_id)
            .where(OrganizationRoleAssignmentDB.organization_id == organization_id)
            .where(OrganizationRoleAssignmentDB.lifecycle.in_(("proposed", "active")))
        ).all():
            grouped[row.role_slot_id].append(row)
        return grouped

    def _role_slot_view(self, slot, *, assignments, agent_by_url, capacity):
        template = (
            self._catalog.get_role_template(
                slot.role_template_key,
                slot.role_template_version,
            )
            or {}
        )
        capability_policy = dict(template.get("capability_policy") or {})
        verification = dict(template.get("verification") or {})
        required = set(
            dict(slot.assignment_policy or {}).get("required_capabilities") or capability_policy.get("required") or []
        )
        forbidden = set(dict(slot.assignment_policy or {}).get("forbidden_capabilities") or [])
        principal_kind_allowed = "agent" in set(dict(slot.assignment_policy or {}).get("principal_kinds") or [])
        write_access_required = bool(dict(slot.assignment_policy or {}).get("write_access_required"))
        separation = dict(slot.separation_of_duties or {})
        independent = bool(
            verification.get("independent_reviewer_required")
            or separation.get("independent_reviewer_required")
            or separation.get("independent_from_slot_ids")
            or separation.get("independent_from_external_duties")
        )
        accountability = template.get("scrum_accountability")
        if accountability == "developer":
            accountability = "developers"
        elif accountability not in {"product_owner", "scrum_master", "developers"}:
            accountability = None
        assigned = []
        for assignment in assignments:
            agent = agent_by_url.get(assignment.agent_url)
            if agent is None:
                safe_agent_id = safe_agent_endpoint_for_display(assignment.agent_url)
                assigned.append(
                    {
                        "agent_id": safe_agent_id,
                        "label": safe_agent_id,
                        "compatible": False,
                        "capacity_used": capacity[assignment.agent_url],
                        "capacity_limit": 0,
                        "affected_teams": [],
                        "reasons": ["agent_not_registered"],
                    }
                )
            else:
                assigned.append(
                    self._candidate_view(
                        agent,
                        required=required,
                        forbidden=forbidden,
                        capacity_used=capacity[agent.url],
                        affected_teams=[],
                        principal_kind_allowed=principal_kind_allowed,
                        write_access_required=write_access_required,
                    )
                )
        maximum = slot.max_count if slot.max_count is not None else slot.default_count
        return {
            "id": slot.id,
            "stable_key": f"{slot.unit_id}:slot:{slot.slot_key}",
            "role_template_key": slot.role_template_key,
            "role_template_version": str(slot.role_template_version),
            "label": template.get("name") or slot.slot_key,
            "scrum_accountability": accountability,
            "specialization": template.get("specialization"),
            "min_count": slot.min_count,
            "default_count": slot.default_count,
            "max_count": maximum,
            "required_capabilities": sorted(required),
            "risk_level": (
                "high"
                if separation.get("enforcement") == "strict" and independent
                else "medium"
                if separation.get("enforcement") == "warn" or independent
                else "normal"
            ),
            "independent_verification_required": independent,
            "assignments": assigned,
        }

    def _candidate_view(
        self,
        agent,
        *,
        required,
        forbidden,
        capacity_used,
        affected_teams,
        principal_kind_allowed,
        write_access_required,
    ):
        eligibility = self._assignment_eligibility.evaluate(
            agent=agent,
            required_capabilities=set(required),
            forbidden_capabilities=set(forbidden),
            capacity_used=capacity_used,
            principal_kind_allowed=principal_kind_allowed,
            write_access_required=write_access_required,
        )
        return {
            "agent_id": safe_agent_endpoint_for_display(agent.url),
            "label": agent.name or "Registered worker",
            "compatible": eligibility.allowed,
            "capacity_used": eligibility.capacity_used,
            "capacity_limit": eligibility.capacity_limit,
            "affected_teams": list(affected_teams),
            "reasons": list(eligibility.reasons),
        }

    @staticmethod
    def _safe_assignment_agent_url(value: object) -> bool:
        raw = str(value or "")
        try:
            parsed = urlsplit(raw)
            _ = parsed.port
        except ValueError:
            return False
        return bool(
            len(raw) <= 2048
            and parsed.scheme in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and not any(character.isspace() for character in raw)
        )

    @staticmethod
    def _affected_teams(session, *, tenant_id, project_id, organization_id, unit_id):
        return sorted(
            row.team_id
            for row in session.exec(
                select(OrganizationTeamLinkDB)
                .where(OrganizationTeamLinkDB.tenant_id == tenant_id)
                .where(OrganizationTeamLinkDB.project_id == project_id)
                .where(OrganizationTeamLinkDB.organization_id == organization_id)
                .where(OrganizationTeamLinkDB.unit_id == unit_id)
            ).all()
        )

    @staticmethod
    def _topology_node_ids(session, *, tenant_id, project_id, organization_id):
        result = {organization_id}
        scoped = (
            (OrganizationUnitDB, OrganizationUnitDB.id),
            (OrganizationRoleSlotDB, OrganizationRoleSlotDB.id),
            (OrganizationRoleAssignmentDB, OrganizationRoleAssignmentDB.id),
        )
        for model, field in scoped:
            result.update(
                str(value)
                for value in session.exec(
                    select(field)
                    .where(model.tenant_id == tenant_id)
                    .where(model.project_id == project_id)
                    .where(model.organization_id == organization_id)
                ).all()
            )
        return result

    @staticmethod
    def _projection_mode(value: str) -> str:
        mode = str(value or "graph").strip().lower()
        if mode not in {"hierarchy", "graph"}:
            raise OrganizationReadError("organization_projection_mode_invalid")
        return mode

    def _encode_cursor(self, value: str, *, tenant_id: str, project_id: str, principal_id: str) -> str:
        self._require_cursor_secret()
        raw = json.dumps(
            {"v": 1, "t": tenant_id, "p": project_id, "s": principal_id, "after": value},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        signature = hmac.new(self._cursor_secret, raw, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(raw + signature).decode().rstrip("=")

    def _decode_cursor(self, cursor, *, tenant_id, project_id, principal_id):
        if not cursor:
            return None
        self._require_cursor_secret()
        try:
            raw = str(cursor)
            decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
            if len(decoded) <= hashlib.sha256().digest_size:
                raise ValueError
            payload, signature = decoded[:-32], decoded[-32:]
            expected = hmac.new(self._cursor_secret, payload, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            value = json.loads(payload.decode())
        except (ValueError, UnicodeError, binascii.Error, json.JSONDecodeError) as exc:
            raise OrganizationReadError("organization_cursor_invalid") from exc
        if not isinstance(value, dict) or value != {
            "v": 1,
            "t": tenant_id,
            "p": project_id,
            "s": principal_id,
            "after": value.get("after") if isinstance(value, dict) else None,
        }:
            raise OrganizationReadError("organization_cursor_scope_invalid")
        after = str(value.get("after") or "")
        if not isinstance(value.get("after"), str) or not after or len(after) > 191:
            raise OrganizationReadError("organization_cursor_invalid")
        return after

    def _require_cursor_secret(self) -> None:
        if not self._cursor_secret:
            raise OrganizationReadError("organization_cursor_secret_invalid")


__all__ = ["OrganizationReadError", "OrganizationReadService"]
