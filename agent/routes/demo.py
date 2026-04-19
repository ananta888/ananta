from flask import Blueprint

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.services.demo_mode_service import get_demo_mode_service

demo_bp = Blueprint("demo", __name__)


@demo_bp.route("/api/demo/preview", methods=["GET"])
@check_auth
def demo_preview():
    return api_response(data=get_demo_mode_service().preview())
