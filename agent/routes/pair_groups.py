from __future__ import annotations

import time
import uuid
from typing import Any

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from agent.auth import check_user_auth, get_request_auth_context
from agent.db_models import PairGroupDB, PairGroupMemberDB
from agent.services.share_session_service import get_share_session_service

pair_groups_bp = Blueprint("pair_groups", __name__)


def _user_id() -> str:
    auth = dict(get_request_auth_context() or {})
    return str(auth.get("sub") or auth.get("username") or "").strip()


def _group_to_dict(row: PairGroupDB) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "owner_user_id": str(row.owner_user_id),
        "name": str(row.name),
        "description": str(row.description or ""),
        "default_permissions": dict(row.default_permissions or {}),
        "created_at": float(row.created_at),
        "updated_at": float(row.updated_at),
    }


def _member_to_dict(row: PairGroupMemberDB) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "group_id": str(row.group_id),
        "user_id": str(row.user_id),
        "display_name": str(row.display_name or ""),
        "added_at": float(row.added_at),
    }


def _get_engine():
    from agent.services.share_session_service import engine
    return engine


@pair_groups_bp.route("/pair-groups", methods=["GET"])
@check_user_auth
def list_pair_groups():
    uid = _user_id()
    if not uid:
        return jsonify({"error": "not_authenticated"}), 401
    try:
        engine = _get_engine()
        with Session(engine) as session:
            rows = session.exec(
                select(PairGroupDB).where(PairGroupDB.owner_user_id == uid)
            ).all()
            groups = [_group_to_dict(r) for r in rows]
    except SQLAlchemyError:
        groups = []
    return jsonify({"ok": True, "groups": groups}), 200


@pair_groups_bp.route("/pair-groups", methods=["POST"])
@check_user_auth
def create_pair_group():
    uid = _user_id()
    if not uid:
        return jsonify({"error": "not_authenticated"}), 401
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    name = str(body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name_required"}), 400
    now = time.time()
    try:
        engine = _get_engine()
        with Session(engine) as session:
            row = PairGroupDB(
                id=str(uuid.uuid4()),
                owner_user_id=uid,
                name=name,
                description=str(body.get("description") or ""),
                default_permissions=dict(body.get("default_permissions") or {"chat": True}),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return jsonify({"ok": True, "group": _group_to_dict(row)}), 201
    except SQLAlchemyError as exc:
        return jsonify({"error": "db_error", "detail": str(exc)}), 500


@pair_groups_bp.route("/pair-groups/<group_id>", methods=["GET"])
@check_user_auth
def get_pair_group(group_id: str):
    uid = _user_id()
    if not uid:
        return jsonify({"error": "not_authenticated"}), 401
    try:
        engine = _get_engine()
        with Session(engine) as session:
            row = session.get(PairGroupDB, group_id)
            if not row:
                return jsonify({"error": "not_found"}), 404
            if str(row.owner_user_id) != uid:
                # Members can also see the group
                member = session.exec(
                    select(PairGroupMemberDB).where(
                        PairGroupMemberDB.group_id == group_id,
                        PairGroupMemberDB.user_id == uid,
                    )
                ).first()
                if not member:
                    return jsonify({"error": "forbidden"}), 403
            members = session.exec(
                select(PairGroupMemberDB).where(PairGroupMemberDB.group_id == group_id)
            ).all()
            return jsonify({
                "ok": True,
                "group": _group_to_dict(row),
                "members": [_member_to_dict(m) for m in members],
            }), 200
    except SQLAlchemyError as exc:
        return jsonify({"error": "db_error", "detail": str(exc)}), 500


@pair_groups_bp.route("/pair-groups/<group_id>", methods=["PATCH"])
@check_user_auth
def update_pair_group(group_id: str):
    uid = _user_id()
    if not uid:
        return jsonify({"error": "not_authenticated"}), 401
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    try:
        engine = _get_engine()
        with Session(engine) as session:
            row = session.get(PairGroupDB, group_id)
            if not row:
                return jsonify({"error": "not_found"}), 404
            if str(row.owner_user_id) != uid:
                return jsonify({"error": "forbidden"}), 403
            if "name" in body and str(body["name"]).strip():
                row.name = str(body["name"]).strip()
            if "description" in body:
                row.description = str(body["description"] or "")
            if "default_permissions" in body and isinstance(body["default_permissions"], dict):
                row.default_permissions = body["default_permissions"]
            row.updated_at = time.time()
            session.add(row)
            session.commit()
            session.refresh(row)
            return jsonify({"ok": True, "group": _group_to_dict(row)}), 200
    except SQLAlchemyError as exc:
        return jsonify({"error": "db_error", "detail": str(exc)}), 500


@pair_groups_bp.route("/pair-groups/<group_id>", methods=["DELETE"])
@check_user_auth
def delete_pair_group(group_id: str):
    uid = _user_id()
    if not uid:
        return jsonify({"error": "not_authenticated"}), 401
    try:
        engine = _get_engine()
        with Session(engine) as session:
            row = session.get(PairGroupDB, group_id)
            if not row:
                return jsonify({"error": "not_found"}), 404
            if str(row.owner_user_id) != uid:
                return jsonify({"error": "forbidden"}), 403
            members = session.exec(
                select(PairGroupMemberDB).where(PairGroupMemberDB.group_id == group_id)
            ).all()
            for m in members:
                session.delete(m)
            session.delete(row)
            session.commit()
            return jsonify({"ok": True}), 200
    except SQLAlchemyError as exc:
        return jsonify({"error": "db_error", "detail": str(exc)}), 500


@pair_groups_bp.route("/pair-groups/<group_id>/members", methods=["POST"])
@check_user_auth
def add_pair_group_member(group_id: str):
    uid = _user_id()
    if not uid:
        return jsonify({"error": "not_authenticated"}), 401
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    target_user_id = str(body.get("user_id") or "").strip()
    if not target_user_id:
        return jsonify({"error": "user_id_required"}), 400
    try:
        engine = _get_engine()
        with Session(engine) as session:
            row = session.get(PairGroupDB, group_id)
            if not row:
                return jsonify({"error": "not_found"}), 404
            if str(row.owner_user_id) != uid:
                return jsonify({"error": "forbidden"}), 403
            existing = session.exec(
                select(PairGroupMemberDB).where(
                    PairGroupMemberDB.group_id == group_id,
                    PairGroupMemberDB.user_id == target_user_id,
                )
            ).first()
            if existing:
                return jsonify({"ok": True, "member": _member_to_dict(existing)}), 200
            member = PairGroupMemberDB(
                id=str(uuid.uuid4()),
                group_id=group_id,
                user_id=target_user_id,
                display_name=str(body.get("display_name") or target_user_id),
                added_at=time.time(),
            )
            session.add(member)
            session.commit()
            session.refresh(member)
            return jsonify({"ok": True, "member": _member_to_dict(member)}), 201
    except SQLAlchemyError as exc:
        return jsonify({"error": "db_error", "detail": str(exc)}), 500


@pair_groups_bp.route("/pair-groups/<group_id>/members/<member_user_id>", methods=["DELETE"])
@check_user_auth
def remove_pair_group_member(group_id: str, member_user_id: str):
    uid = _user_id()
    if not uid:
        return jsonify({"error": "not_authenticated"}), 401
    try:
        engine = _get_engine()
        with Session(engine) as session:
            row = session.get(PairGroupDB, group_id)
            if not row:
                return jsonify({"error": "not_found"}), 404
            if str(row.owner_user_id) != uid:
                return jsonify({"error": "forbidden"}), 403
            member = session.exec(
                select(PairGroupMemberDB).where(
                    PairGroupMemberDB.group_id == group_id,
                    PairGroupMemberDB.user_id == member_user_id,
                )
            ).first()
            if not member:
                return jsonify({"error": "not_found"}), 404
            session.delete(member)
            session.commit()
            return jsonify({"ok": True}), 200
    except SQLAlchemyError as exc:
        return jsonify({"error": "db_error", "detail": str(exc)}), 500


@pair_groups_bp.route("/pair-groups/<group_id>/invite", methods=["POST"])
@check_user_auth
def create_group_session(group_id: str):
    """Erstellt eine Share-Session für alle Mitglieder der Gruppe."""
    uid = _user_id()
    if not uid:
        return jsonify({"error": "not_authenticated"}), 401
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    try:
        engine = _get_engine()
        with Session(engine) as session:
            row = session.get(PairGroupDB, group_id)
            if not row:
                return jsonify({"error": "not_found"}), 404
            if str(row.owner_user_id) != uid:
                return jsonify({"error": "forbidden"}), 403
            members = session.exec(
                select(PairGroupMemberDB).where(PairGroupMemberDB.group_id == group_id)
            ).all()
        service = get_share_session_service()
        permissions = dict(body.get("permissions") or row.default_permissions or {"chat": True})
        expires_in = float(body.get("expires_in_seconds") or 86400)
        session_item = service.create_session(
            owner_user_id=uid,
            owner_device_id=f"web-{uid[:16]}",
            title=str(body.get("title") or f"{row.name} – Session"),
            mode="relay",
            transport="hub_relay",
            permissions=permissions,
            expires_at=time.time() + expires_in,
        )
        return jsonify({
            "ok": True,
            "session": session_item,
            "member_count": len(members),
            "invite_code": session_item.get("invite_code"),
        }), 201
    except SQLAlchemyError as exc:
        return jsonify({"error": "db_error", "detail": str(exc)}), 500
