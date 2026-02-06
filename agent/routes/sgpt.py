from flask import Blueprint, request, jsonify
import logging
from agent.sgpt.app import main as sgpt_main
from agent.auth import check_auth
import sys
import io
from contextlib import redirect_stdout, redirect_stderr

sgpt_bp = Blueprint("sgpt", __name__, url_prefix="/api/sgpt")

@sgpt_bp.route("/execute", methods=["POST"])
@check_auth
def execute_sgpt():
    """
    Führt einen SGPT-Befehl aus.
    Erwartet JSON: {"prompt": "...", "options": ["--shell", "--model", "..."]}
    """
    data = request.json
    prompt = data.get("prompt")
    options = data.get("options", [])
    
    if not prompt:
        return jsonify({"error": "Missing prompt"}), 400
    
    # Baue Argument-Liste für Click (sgpt nutzt Typer/Click)
    args = options + [prompt]
    
    logging.info(f"SGPT CLI Proxy: sgpt {' '.join(args)}")
    
    f_out = io.StringIO()
    f_err = io.StringIO()
    
    try:
        # Wir müssen sys.argv patchen, da sgpt.app.main() typer.run nutzt, 
        # welches sys.argv ausliest wenn keine Argumente übergeben werden.
        # Alternativ rufen wir die internen Handler direkt auf, aber main() ist einfacher für volle CLI-Kompatibilität.
        
        orig_argv = sys.argv
        sys.argv = ["sgpt"] + args
        
        with redirect_stdout(f_out), redirect_stderr(f_err):
            try:
                sgpt_main()
            except SystemExit as e:
                # Typer/Click rufen oft sys.exit() auf
                logging.debug(f"SGPT Exit mit Code {e.code}")
        
        sys.argv = orig_argv
        
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
