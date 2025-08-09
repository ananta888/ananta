from flask import Blueprint, jsonify, request

blueprint = Blueprint("controller", __name__)

_tasks = []
_blacklist = set()


@blueprint.get("/controller/next-task")
def next_task():
    for task in list(_tasks):
        if task not in _blacklist:
            _tasks.remove(task)
            return jsonify({"task": task})
    return jsonify({"task": None})


@blueprint.route("/controller/blacklist", methods=["GET", "POST"])
def blacklist():
    if request.method == "POST":
        item = request.json.get("task")
        _blacklist.add(item)
        return jsonify({"status": "added"})
    return jsonify(sorted(_blacklist))


@blueprint.route("/controller/status", methods=["GET", "DELETE"])
def status():
    if request.method == "DELETE":
        _tasks.clear()
        _blacklist.clear()
        return jsonify({"status": "cleared"})
    return jsonify({"tasks": list(_tasks), "blacklist": list(_blacklist)})
