from flask import Blueprint, jsonify, request
from sqlalchemy import select, outerjoin
from sqlalchemy.exc import IntegrityError

from src.db.sa import session_scope, ControllerTask, ControllerBlacklist

blueprint = Blueprint("controller", __name__)


@blueprint.get("/controller/next-task")
def next_task():
    """Return and remove the next task that is not blacklisted.
    Response shape: {"task": <str or None>}
    """
    try:
        with session_scope() as s:
            t = ControllerTask
            b = ControllerBlacklist
            stmt = (
                select(t)
                .select_from(outerjoin(t, b, t.task == b.cmd))
                .where(b.id.is_(None))
                .order_by(t.id.asc())
                .limit(1)
            )
            row = s.execute(stmt).scalars().first()
            if not row:
                return jsonify({"task": None})
            # delete the returned task atomically
            s.delete(row)
            return jsonify({"task": row.task})
    except Exception as e:
        return jsonify({"error": "internal_error", "detail": str(e)}), 500


@blueprint.route("/controller/blacklist", methods=["GET", "POST"])
def blacklist():
    """GET: return a sorted list of blacklisted commands.
       POST: body {"task": str} adds to blacklist.
    """
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        item = data.get("task")
        if not isinstance(item, str) or not item.strip():
            return jsonify({"error": "invalid_task"}), 400
        if len(item) > 4096:
            return jsonify({"error": "too_long"}), 400
        try:
            with session_scope() as s:
                bl = ControllerBlacklist(cmd=item.strip())
                s.add(bl)
            return jsonify({"status": "added"})
        except IntegrityError:
            # already exists
            return jsonify({"status": "exists"})
        except Exception as e:
            return jsonify({"error": "internal_error", "detail": str(e)}), 500

    # GET
    try:
        with session_scope() as s:
            cmds = [c.cmd for c in s.query(ControllerBlacklist).order_by(ControllerBlacklist.cmd.asc()).all()]
            return jsonify(cmds)
    except Exception as e:
        return jsonify({"error": "internal_error", "detail": str(e)}), 500


@blueprint.route("/controller/status", methods=["GET", "DELETE"])
def status():
    if request.method == "DELETE":
        try:
            with session_scope() as s:
                s.query(ControllerTask).delete()
                s.query(ControllerBlacklist).delete()
            return jsonify({"status": "cleared"})
        except Exception as e:
            return jsonify({"error": "internal_error", "detail": str(e)}), 500

    # GET
    try:
        with session_scope() as s:
            tasks = [t.task for t in s.query(ControllerTask).order_by(ControllerTask.id.asc()).all()]
            bl = [c.cmd for c in s.query(ControllerBlacklist).order_by(ControllerBlacklist.cmd.asc()).all()]
            return jsonify({"tasks": tasks, "blacklist": bl})
    except Exception as e:
        return jsonify({"error": "internal_error", "detail": str(e)}), 500
