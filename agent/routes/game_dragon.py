"""Dedicated, local-game credentials expose only NPC decisions and speech."""
from functools import wraps
import json
import os
from pathlib import Path
import secrets
import threading
from flask import Blueprint, current_app, jsonify, request, Response
from agent.services.game_dragon import DragonError, GameDragonService

game_dragon_bp = Blueprint('game_dragon', __name__, url_prefix='/api/game-dragon')
_LIMIT = threading.BoundedSemaphore(2)


def _configuration():
    configured = current_app.config.get('GAME_DRAGON_CONFIG')
    if configured is not None: return configured
    path = Path(os.environ.get('ANANTA_GAME_DRAGON_CONFIG', '/app/data/game-dragon.json'))
    try: return json.loads(path.read_text())
    except (OSError, ValueError): return {}


def guarded(function):
    @wraps(function)
    def route(*args, **kwargs):
        config = _configuration()
        if config.get('enabled') is not True: return jsonify(error='dragon_disabled'), 503
        try: expected = Path(config['token_file']).read_text().strip()
        except (OSError, KeyError): return jsonify(error='dragon_not_configured'), 503
        provided = request.headers.get('Authorization', '').removeprefix('Bearer ')
        if len(expected) < 32 or not secrets.compare_digest(provided.encode(), expected.encode()):
            return jsonify(error='unauthorized'), 401
        request.max_content_length = 2 * 1024 * 1024 if request.endpoint.endswith('transcribe') else 16384
        if not _LIMIT.acquire(blocking=False): return jsonify(error='dragon_busy'), 429
        try:
            service = current_app.extensions.get('game_dragon_service') or GameDragonService(config)
            return function(service, *args, **kwargs)
        except DragonError as error: return jsonify(error=error.code), error.status
        except (OSError, KeyError, ValueError): return jsonify(error='dragon_not_configured'), 503
        finally: _LIMIT.release()
    return route


@game_dragon_bp.post('/decide')
@guarded
def decide(service):
    return jsonify(service.decide(request.get_json(silent=True)))


@game_dragon_bp.post('/design')
@guarded
def design(service):
    return jsonify(service.design(request.get_json(silent=True)))


@game_dragon_bp.post('/transcribe')
@guarded
def transcribe(service):
    return jsonify(service.transcribe(request.get_data(), request.content_type or ''))


@game_dragon_bp.post('/speak')
@guarded
def speak(service):
    data = request.get_json(silent=True)
    return Response(service.speak(data.get('text') if isinstance(data, dict) else None), mimetype='audio/wav', headers={'Cache-Control': 'no-store'})
