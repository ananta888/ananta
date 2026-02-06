from flask import Blueprint, request, jsonify
import logging
from agent.auth import check_auth
import sys
import io
from contextlib import redirect_stdout, redirect_stderr

import threading

def get_sgpt_main():
    from agent.sgpt.app import main as sgpt_main
    return sgpt_main

sgpt_bp = Blueprint("sgpt", __name__, url_prefix="/api/sgpt")
sgpt_lock = threading.Lock()

ALLOWED_OPTIONS = {
    "--shell", "--model", "--temperature", "--top-p", "--md", "--no-interaction", "--cache", "--no-cache"
}

@sgpt_bp.route("/execute", methods=["POST"])
@check_auth
def execute_sgpt():
    """
    Führt einen SGPT-Befehl aus.
    Erwartet JSON: {"prompt": "...", "options": ["--shell", "..."]}
    """
    data = request.json
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON payload"}), 400

    prompt = data.get("prompt")
    options = data.get("options", [])
    
    if not prompt:
        return jsonify({"error": "Missing prompt"}), 400
    
    if not isinstance(options, list):
        return jsonify({"error": "Options must be a list"}), 400

    # SGPT-2: Validiere Optionen und schränke erlaubte Flags ein
    safe_options = []
    for opt in options:
        if opt in ALLOWED_OPTIONS:
            safe_options.append(opt)
        else:
            logging.warning(f"Rejected unsafe SGPT option: {opt}")

    # SGPT-1: Erzwinge --no-interaction um Blockieren zu verhindern
    if "--no-interaction" not in safe_options:
        safe_options.append("--no-interaction")

    # Baue Argument-Liste für Click
    args = safe_options + [prompt]
    
    logging.info(f"SGPT CLI Proxy: sgpt {' '.join(args)}")
    
    f_out = io.StringIO()
    f_err = io.StringIO()
    
    # SGPT-3: Thread-Sicherheit durch Lock
    with sgpt_lock:
        orig_argv = sys.argv
        try:
            sys.argv = ["sgpt"] + args
            
            with redirect_stdout(f_out), redirect_stderr(f_err):
                try:
                    sgpt_main = get_sgpt_main()
                    sgpt_main()
                except SystemExit as e:
                    logging.debug(f"SGPT Exit mit Code {e.code}")
            
            output = f_out.getvalue()
            errors = f_err.getvalue()
            
            return jsonify({
                "output": output,
                "errors": errors,
                "status": "success"
            })
            
        except Exception as e:
            logging.exception("Fehler beim Ausführen von SGPT")
            return jsonify({"error": str(e), "status": "error"}), 500
        finally:
            # SGPT-3: Immer sys.argv wiederherstellen
            sys.argv = orig_argv
