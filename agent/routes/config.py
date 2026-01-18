import uuid
from flask import Blueprint, jsonify, current_app, request, g
from agent.utils import validate_request, read_json, write_json
from agent.auth import check_auth, admin_required
from agent.common.audit import log_audit
from agent.llm_integration import generate_text
from agent.repository import template_repo, config_repo
from agent.db_models import TemplateDB, ConfigDB
import json

config_bp = Blueprint("config", __name__)

@config_bp.route("/config", methods=["GET"])
@check_auth
def get_config():
    """
    Aktuelle Konfiguration abrufen
    ---
    security:
      - Bearer: []
    responses:
      200:
        description: Aktuelle Agenten-Konfiguration
    """
    return jsonify(current_app.config.get("AGENT_CONFIG", {}))

@config_bp.route("/config", methods=["POST"])
@admin_required
def set_config():
    """
    Konfiguration aktualisieren
    ---
    security:
      - Bearer: []
    responses:
      200:
        description: Konfiguration erfolgreich aktualisiert
    """
    new_cfg = request.get_json()
    if not isinstance(new_cfg, dict):
        return jsonify({"error": "invalid_json"}), 400
    
    current_cfg = current_app.config.get("AGENT_CONFIG", {})
    current_cfg.update(new_cfg)
    current_app.config["AGENT_CONFIG"] = current_cfg
    
    # LLM Config in App-Context synchronisieren falls vorhanden
    if "llm_config" in current_cfg:
        lc = current_cfg["llm_config"]
        prov = lc.get("provider")
        if prov and lc.get("base_url"):
            urls = current_app.config.get("PROVIDER_URLS", {}).copy()
            urls[prov] = lc.get("base_url")
            current_app.config["PROVIDER_URLS"] = urls
        if lc.get("api_key"):
            if prov == "openai":
                current_app.config["OPENAI_API_KEY"] = lc.get("api_key")
            elif prov == "anthropic":
                current_app.config["ANTHROPIC_API_KEY"] = lc.get("api_key")

    # Weitere URL-Synchronisation für globale Provider-URLs
    urls = current_app.config.get("PROVIDER_URLS", {}).copy()
    for p in ["ollama", "lmstudio", "openai", "anthropic"]:
        key = f"{p}_url"
        if key in current_cfg:
            urls[p] = current_cfg[key]
    current_app.config["PROVIDER_URLS"] = urls

    # API Keys synchronisieren falls global gesetzt
    if "openai_api_key" in current_cfg:
        current_app.config["OPENAI_API_KEY"] = current_cfg["openai_api_key"]
    if "anthropic_api_key" in current_cfg:
        current_app.config["ANTHROPIC_API_KEY"] = current_cfg["anthropic_api_key"]

    # Synchronisiere mit globaler settings-Instanz
    from agent.config import settings
    for key, value in new_cfg.items():
        if hasattr(settings, key):
            try:
                setattr(settings, key, value)
            except Exception as e:
                current_app.logger.warning(f"Konnte settings.{key} nicht aktualisieren: {e}")
    
    # In DB persistieren
    try:
        for k, v in new_cfg.items():
            config_repo.save(ConfigDB(key=k, value_json=json.dumps(v)))
    except Exception as e:
        current_app.logger.error(f"Fehler beim Speichern der Konfiguration in DB: {e}")

    log_audit("config_updated", {"keys": list(new_cfg.keys())})
    return jsonify({"status": "updated", "config": current_cfg})

@config_bp.route("/templates", methods=["GET"])
@check_auth
def list_templates():
    tpls = template_repo.get_all()
    return jsonify([t.dict() for t in tpls])

@config_bp.route("/templates", methods=["POST"])
@admin_required
def create_template():
    data = request.get_json()
    new_tpl = TemplateDB(
        name=data.get("name"),
        description=data.get("description"),
        prompt_template=data.get("prompt_template", "")
    )
    template_repo.save(new_tpl)
    log_audit("template_created", {"template_id": new_tpl.id, "name": new_tpl.name})
    return jsonify(new_tpl.dict()), 201

@config_bp.route("/templates/<tpl_id>", methods=["PUT", "PATCH"])
@admin_required
def update_template(tpl_id):
    data = request.get_json()
    tpl = template_repo.get_by_id(tpl_id)
    if not tpl:
        return jsonify({"error": "not_found"}), 404
    
    if "name" in data: tpl.name = data["name"]
    if "description" in data: tpl.description = data["description"]
    if "prompt_template" in data: tpl.prompt_template = data["prompt_template"]
    
    template_repo.save(tpl)
    log_audit("template_updated", {"template_id": tpl_id, "name": tpl.name})
    return jsonify(tpl.dict())

@config_bp.route("/templates/<tpl_id>", methods=["DELETE"])
@admin_required
def delete_template(tpl_id):
    if template_repo.delete(tpl_id):
        log_audit("template_deleted", {"template_id": tpl_id})
        return jsonify({"status": "deleted"})
    return jsonify({"error": "not_found"}), 404

from agent.tools import registry as tool_registry

@config_bp.route("/llm/generate", methods=["POST"])
@check_auth
def llm_generate():
    """
    LLM-Generierung mit Tool-Calling Unterstützung
    """
    data = request.get_json()
    user_prompt = data.get("prompt")
    if not user_prompt:
        return jsonify({"error": "missing_prompt"}), 400
    
    # LLM-Konfiguration und Tool-Allowlist
    agent_cfg = current_app.config.get("AGENT_CONFIG", {})
    llm_cfg = agent_cfg.get("llm_config", {})
    
    is_admin = getattr(g, "is_admin", False)
    allowlist_cfg = agent_cfg.get("llm_tool_allowlist")
    denylist_cfg = agent_cfg.get("llm_tool_denylist", [])
    default_allowlist = {"list_teams", "list_roles", "list_agents", "analyze_logs", "read_agent_logs"}
    
    if allowlist_cfg is None:
        allowed_tools = default_allowlist
    else:
        allowed_tools = allowlist_cfg

    # Wenn kein Admin, sensitive Tools entfernen (Sicherheits-Check)
    if not is_admin:
        sensitive_tools = {"analyze_logs", "read_agent_logs", "update_config", "create_template", "delete_template", "create_team"}
        if allowed_tools == "*":
            allowed_tools = [name for name in tool_registry.tools.keys() if name not in sensitive_tools]
        elif isinstance(allowed_tools, (list, set, dict)):
            allowed_tools = [t for t in allowed_tools if t not in sensitive_tools]

    # Tool-Definitionen für den Prompt (gefiltert)
    tools_desc = json.dumps(tool_registry.get_tool_definitions(allowlist=allowed_tools, denylist=denylist_cfg), indent=2, ensure_ascii=False)
    
    system_instruction = f"""Du bist ein hilfreicher KI-Assistent für das 'Ananta' Framework.
Dir stehen folgende Werkzeuge zur Verfügung:
{tools_desc}

Wenn du eine Aktion ausführen möchtest, antworte AUSSCHLIESSLICH im folgenden JSON-Format:
{{
  "thought": "Deine Überlegung, warum du dieses Tool wählst",
  "tool_calls": [
    {{ "name": "tool_name", "args": {{ "arg1": "value1" }} }}
  ],
  "answer": "Eine kurze Bestätigung für den Nutzer, was du tust"
}}

Falls keine Aktion nötig ist, antworte normal als Text oder ebenfalls im JSON-Format (dann mit leerem tool_calls).
"""

    history = data.get("history", [])
    # System-Instruction als erste Nachricht in der Historie mitgeben
    full_history = [{"role": "system", "content": system_instruction}] + history

    # LLM-Parameter auflösen
    cfg = data.get("config") or {}
    provider = cfg.get("provider") or llm_cfg.get("provider")
    model = cfg.get("model") or llm_cfg.get("model")
    base_url = cfg.get("base_url") or llm_cfg.get("base_url")
    api_key = cfg.get("api_key") or llm_cfg.get("api_key")

    response_text = generate_text(
        prompt=user_prompt,
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        history=full_history
    )

    def _extract_json(text: str) -> dict | None:
        clean_text = text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text.split("```json")[1].split("```")[0].strip()
        elif clean_text.startswith("```"):
            clean_text = clean_text.split("```")[1].split("```")[0].strip()
        try:
            return json.loads(clean_text)
        except Exception:
            return None

    res_json = _extract_json(response_text)

    if res_json is None:
        repair_prompt = (
            f"Assistant (invalid JSON): {response_text}\n\n"
            "System: Antworte AUSSCHLIESSLICH mit gueltigem JSON im oben beschriebenen Format. "
            "Kein Freitext, keine Markdown-Bloecke."
        )
        response_text = generate_text(
            prompt=repair_prompt,
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            history=full_history
        )
        res_json = _extract_json(response_text)

    # Versuchen, JSON zu parsen
    try:
        if res_json is None:
            return jsonify({"response": response_text})
        tool_calls = res_json.get("tool_calls", [])

        if tool_calls:
            allow_all = allowed_tools == "*" or (isinstance(allowed_tools, list) and "*" in allowed_tools)
            denylist_set = set(denylist_cfg)
            
            blocked_tools = []
            for tc in tool_calls:
                name = tc.get("name")
                if not name:
                    blocked_tools.append("<missing>")
                    continue
                if name in denylist_set:
                    blocked_tools.append(name)
                elif not allow_all and name not in allowed_tools:
                    blocked_tools.append(name)

            if blocked_tools:
                log_audit("tool_calls_blocked", {"tools": blocked_tools})
                blocked_results = [
                    {"tool": name, "success": False, "output": None, "error": "tool_not_allowed"}
                    for name in blocked_tools
                ]
                return jsonify({
                    "response": f"Tool calls blocked: {', '.join(blocked_tools)}",
                    "tool_results": blocked_results,
                    "blocked_tools": blocked_tools
                })

            results = []
            for tc in tool_calls:
                name = tc.get("name")
                args = tc.get("args", {})
                current_app.logger.info(f"KI ruft Tool auf: {name} mit {args}")
                tool_res = tool_registry.execute(name, args)
                results.append({
                    "tool": name,
                    "success": tool_res.success,
                    "output": tool_res.output,
                    "error": tool_res.error
                })
            
            # Finalen Antwort-Prompt erstellen mit Tool-Ergebnissen
            tool_history = full_history + [
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": json.dumps(res_json)}, # Original Assistant-Antwort mit tool_calls
                {"role": "system", "content": f"Tool Results: {json.dumps(results)}"}
            ]
            
            final_response = generate_text(
                prompt="Bitte gib eine finale Antwort an den Nutzer basierend auf diesen Ergebnissen.",
                provider=provider,
                model=model,
                base_url=base_url,
                api_key=api_key,
                history=tool_history
            )
            return jsonify({"response": final_response, "tool_results": results})
            
        return jsonify({"response": res_json.get("answer", response_text)})

    except Exception as e:
        # Falls kein JSON oder Fehler beim Parsen, einfach Text zurückgeben
        current_app.logger.debug(f"Konnte LLM-Antwort nicht als JSON parsen (normal bei reinem Text): {e}")
        return jsonify({"response": response_text})
