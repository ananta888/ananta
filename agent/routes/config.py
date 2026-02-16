import uuid
from flask import Blueprint, current_app, request, g, Response, stream_with_context
from agent.common.errors import api_response
from agent.utils import log_llm_entry, rate_limit, validate_request
from agent.auth import check_auth, admin_required
from agent.common.audit import log_audit
from agent.models import TemplateCreateRequest
from agent.llm_integration import generate_text, _load_lmstudio_history
from agent.repository import template_repo, config_repo
from agent.tools import registry as tool_registry
from agent.tool_guardrails import evaluate_tool_call_guardrails, estimate_text_tokens, estimate_tool_calls_tokens
from agent.db_models import TemplateDB, ConfigDB, RoleDB, TeamMemberDB, TeamTypeRoleLink, TeamDB
from agent.database import engine
from sqlmodel import Session, select
import json
import re

ALLOWED_TEMPLATE_VARIABLES = {
    "agent_name",
    "task_title",
    "task_description",
    "team_name",
    "role_name",
    "team_goal",
    "anforderungen",
    "funktion",
    "feature_name",
    "title",
    "description",
    "task",
    "endpoint_name",
    "beschreibung",
    "sprache",
    "api_details",
}


def _get_template_allowlist() -> set:
    cfg = current_app.config.get("AGENT_CONFIG", {})
    allowlist_cfg = cfg.get("template_variables_allowlist")
    if isinstance(allowlist_cfg, list) and allowlist_cfg:
        return set(allowlist_cfg)
    return ALLOWED_TEMPLATE_VARIABLES


def validate_template_variables(template_text: str) -> list[str]:
    """Extrahiert {{variablen}} und prueft sie gegen die Whitelist."""
    if not template_text:
        return []
    found_vars = re.findall(r"\{\{([a-zA-Z0-9_]+)\}\}", template_text)
    allowlist = _get_template_allowlist()
    unknown_vars = [v for v in found_vars if v not in allowlist]
    return unknown_vars


config_bp = Blueprint("config", __name__)


@config_bp.route("/llm/history", methods=["GET"])
@check_auth
def get_llm_history():
    """
    Gibt den Verlauf der genutzten LLM-Modelle zurück (aktuell LMStudio Fokus).
    """
    history = _load_lmstudio_history()
    return api_response(data=history)


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
    return api_response(data=current_app.config.get("AGENT_CONFIG", {}))


def unwrap_config(data):
    """Rekursives Entpacken von API-Response-Wrappern in der Config."""
    if not isinstance(data, dict):
        return data

    # Falls es ein Wrapper ist {"status": "success", "data": {...}}
    if "data" in data and ("status" in data or "code" in data):
        return unwrap_config(data["data"])

    # Rekursiv für alle Keys anwenden
    return {k: unwrap_config(v) for k, v in data.items()}


def _infer_tool_calls_from_prompt(prompt: str, context: dict | None = None) -> list[dict]:
    """
    Deterministischer Fallback fuer haeufige Intent-Muster, wenn das LLM
    keine gueltigen tool_calls liefert (z.B. wegen Thinking-Output).
    """
    p = (prompt or "").strip().lower()
    if not p:
        return []

    wants_templates = any(k in p for k in ["template", "templates", "vorlage", "vorlagen"])
    wants_role_links = any(
        k in p
        for k in [
            "rolle verkn",
            "rollen verkn",
            "role link",
            "role links",
            "rollen zuordnen",
            "roles zuordnen",
        ]
    )

    team_types: list[str] = []
    if "scrum" in p:
        team_types.append("Scrum")
    if "kanban" in p:
        team_types.append("Kanban")

    if wants_templates or wants_role_links:
        if not team_types:
            return []
        return [{"name": "ensure_team_templates", "args": {"team_types": team_types}}]

    wants_create_team = any(
        k in p
        for k in [
            "team erstellen",
            "team anlegen",
            "create team",
            "neues team",
            "new team",
        ]
    )
    if wants_create_team:
        if "scrum" in p:
            inferred_type = "Scrum"
        elif "kanban" in p:
            inferred_type = "Kanban"
        else:
            return []

        # Safeguard: nur ausfuehren, wenn ein expliziter Team-Name erkennbar ist.
        team_name = ""
        quoted = re.search(r"['\"]([^'\"]{2,80})['\"]", prompt or "")
        if quoted:
            team_name = quoted.group(1).strip()
        if not team_name:
            m = re.search(r"(?:team(?:name)?\s*[:=]\s*)([a-zA-Z0-9 _-]{2,80})", prompt or "", flags=re.IGNORECASE)
            if m:
                team_name = m.group(1).strip(" .,:;")
        if not team_name:
            return []

        return [{"name": "create_team", "args": {"name": team_name, "team_type": inferred_type}}]

    wants_assign_role = any(
        k in p
        for k in [
            "rolle zuweisen",
            "assign role",
            "agent zuordnen",
            "agent zuweisen",
            "mitglied zuordnen",
        ]
    )
    if wants_assign_role:
        # Safeguard: Tool erwartet IDs/URL. Nur inferieren, wenn alles explizit im Prompt steht.
        team_id_match = re.search(r"team_id\s*[:=]\s*([a-zA-Z0-9._:-]+)", prompt or "", flags=re.IGNORECASE)
        role_id_match = re.search(r"role_id\s*[:=]\s*([a-zA-Z0-9._:-]+)", prompt or "", flags=re.IGNORECASE)
        agent_url_match = re.search(r"agent_url\s*[:=]\s*(https?://\S+)", prompt or "", flags=re.IGNORECASE)
        if not (team_id_match and role_id_match and agent_url_match):
            return []
        return [
            {
                "name": "assign_role",
                "args": {
                    "team_id": team_id_match.group(1).strip(),
                    "role_id": role_id_match.group(1).strip(),
                    "agent_url": agent_url_match.group(1).strip().rstrip(".,;"),
                },
            }
        ]

    return []


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
        return api_response(status="error", message="invalid_json", code=400)

    # Robustes Entpacken
    new_cfg = unwrap_config(new_cfg)

    current_cfg = current_app.config.get("AGENT_CONFIG", {})

    # Ensure nested llm_config fields merge instead of replacing the whole block,
    # so mode toggles such as lmstudio_api_mode are not dropped.
    if "llm_config" in new_cfg and isinstance(new_cfg["llm_config"], dict):
        merged_llm = (current_cfg.get("llm_config", {}) or {}).copy()
        merged_llm.update(new_cfg["llm_config"])
        new_cfg = {**new_cfg, "llm_config": merged_llm}
    current_cfg.update(new_cfg)
    current_app.config["AGENT_CONFIG"] = current_cfg

    # Synchronisiere mit globaler settings-Instanz (Pydantic)
    # Dies stellt sicher, dass Pydantic-basierte Logik (z.B. in llm_integration.py)
    # die aktualisierten Werte sieht.
    from agent.config import settings

    # Flache Keys in settings synchronisieren
    for key, value in new_cfg.items():
        if hasattr(settings, key):
            try:
                setattr(settings, key, value)
                # Auch in app.config synchronisieren für Legacy-Kompatibilität
                if key.upper() in current_app.config:
                    current_app.config[key.upper()] = value
            except Exception as e:
                current_app.logger.warning(f"Konnte settings.{key} nicht aktualisieren: {e}")

    # Spezialfall: verschachtelte llm_config in Pydantic settings spiegeln falls möglich
    if "llm_config" in new_cfg and isinstance(new_cfg["llm_config"], dict):
        lc = new_cfg["llm_config"]
        # Mapping von llm_config Keys auf flache Settings-Attribute
        mapping = {
            "provider": "default_provider",
            "model": "default_model",
            "base_url": None,  # Wird unten über PROVIDER_URLS gehandhabt
            "api_key": None,  # Wird unten über Provider-spezifische Keys gehandhabt
            "lmstudio_api_mode": "lmstudio_api_mode",
        }

        prov = lc.get("provider")
        for k, target in mapping.items():
            val = lc.get(k)
            if val is not None:
                if target and hasattr(settings, target):
                    try:
                        setattr(settings, target, val)
                    except Exception as e:
                        current_app.logger.warning(f"Failed to set settings.{target}={val}: {e}")

                # Provider-spezifische Keys/URLs
                if prov:
                    if k == "base_url":
                        url_attr = f"{prov}_url"
                        if hasattr(settings, url_attr):
                            setattr(settings, url_attr, val)
                        urls = current_app.config.get("PROVIDER_URLS", {}).copy()
                        urls[prov] = val
                        current_app.config["PROVIDER_URLS"] = urls
                    elif k == "api_key":
                        key_attr = f"{prov}_api_key"
                        if hasattr(settings, key_attr):
                            setattr(settings, key_attr, val)
                        if prov == "openai":
                            current_app.config["OPENAI_API_KEY"] = val
                        elif prov == "anthropic":
                            current_app.config["ANTHROPIC_API_KEY"] = val

    # In DB persistieren (nur valide Config-Keys, keine Response-Wrapper)
    try:
        # Reservierte API-Response-Keys ignorieren um Korruption zu vermeiden
        reserved_keys = {"data", "status", "message", "error", "code"}

        config_to_save = new_cfg

        for k, v in config_to_save.items():
            if k not in reserved_keys:
                config_repo.save(ConfigDB(key=k, value_json=json.dumps(v)))
    except Exception as e:
        current_app.logger.error(f"Fehler beim Speichern der Konfiguration in DB: {e}")

    log_audit("config_updated", {"keys": list(new_cfg.keys())})
    return api_response(data={"status": "updated"})


@config_bp.route("/providers", methods=["GET"])
@check_auth
def list_providers():
    """
    Verfügbare LLM-Provider abrufen
    ---
    security:
      - Bearer: []
    responses:
      200:
        description: Liste der verfügbaren LLM-Provider
    """
    providers = []
    urls = current_app.config.get("PROVIDER_URLS", {})

    # Bekannte Provider und ihre Modelle (hier vereinfacht, könnte später noch dynamischer sein)
    # Wenn eine URL konfiguriert ist, betrachten wir den Provider als potenziell verfügbar

    if urls.get("ollama"):
        providers.append({"id": "ollama:llama3", "name": "Ollama (Llama3)", "selected": True})
        providers.append({"id": "ollama:mistral", "name": "Ollama (Mistral)", "selected": False})

    if urls.get("openai") or current_app.config.get("OPENAI_API_KEY"):
        providers.append({"id": "openai:gpt-4o", "name": "OpenAI (GPT-4o)", "selected": False})
        providers.append({"id": "openai:gpt-4-turbo", "name": "OpenAI (GPT-4 Turbo)", "selected": False})

    if urls.get("anthropic") or current_app.config.get("ANTHROPIC_API_KEY"):
        providers.append({"id": "anthropic:claude-3-5-sonnet-20240620", "name": "Claude 3.5 Sonnet", "selected": False})

    if urls.get("lmstudio"):
        providers.append({"id": "lmstudio:model", "name": "LM Studio", "selected": False})

    # Falls gar nichts konfiguriert ist, geben wir die Standard-Liste zurück damit das Frontend nicht leer bleibt
    if not providers:
        providers = [
            {"id": "ollama:llama3", "name": "Ollama (Llama3)", "selected": True},
            {"id": "openai:gpt-4o", "name": "OpenAI (GPT-4o)", "selected": False},
            {"id": "anthropic:claude-3-5-sonnet-20240620", "name": "Claude 3.5 Sonnet", "selected": False},
            {"id": "lmstudio:model", "name": "LM Studio", "selected": False},
        ]

    return api_response(data=providers)


@config_bp.route("/templates", methods=["GET"])
@check_auth
def list_templates():
    tpls = template_repo.get_all()
    return api_response(data=[t.model_dump() for t in tpls])


@config_bp.route("/templates", methods=["POST"])
@admin_required
@validate_request(TemplateCreateRequest)
def create_template():
    data: TemplateCreateRequest = g.validated_data
    prompt_tpl = data.prompt_template

    unknown = validate_template_variables(prompt_tpl)
    warnings = []
    if unknown:
        warnings.append(
            {
                "type": "unknown_variables",
                "details": f"Unknown variables: {', '.join(unknown)}",
                "allowed": list(_get_template_allowlist()),
            }
        )

    new_tpl = TemplateDB(name=data.name, description=data.description, prompt_template=prompt_tpl)
    template_repo.save(new_tpl)
    log_audit("template_created", {"template_id": new_tpl.id, "name": new_tpl.name})
    res = new_tpl.model_dump()
    if warnings:
        res["warnings"] = warnings
    return api_response(data=res, code=201)


@config_bp.route("/templates/<tpl_id>", methods=["PUT", "PATCH"])
@admin_required
def update_template(tpl_id):
    data = request.get_json()
    tpl = template_repo.get_by_id(tpl_id)
    if not tpl:
        return api_response(status="error", message="not_found", code=404)

    warnings = []
    if "prompt_template" in data:
        unknown = validate_template_variables(data["prompt_template"])
        if unknown:
            warnings.append(
                {
                    "type": "unknown_variables",
                    "details": f"Unknown variables: {', '.join(unknown)}",
                    "allowed": list(_get_template_allowlist()),
                }
            )
        tpl.prompt_template = data["prompt_template"]

    if "name" in data:
        tpl.name = data["name"]
    if "description" in data:
        tpl.description = data["description"]

    template_repo.save(tpl)
    log_audit("template_updated", {"template_id": tpl_id, "name": tpl.name})
    res = tpl.model_dump()
    if warnings:
        res["warnings"] = warnings
    return api_response(data=res)


@config_bp.route("/templates/<tpl_id>", methods=["DELETE"])
@admin_required
def delete_template(tpl_id):
    try:
        with Session(engine) as session:
            tpl = session.get(TemplateDB, tpl_id)
            if not tpl:
                return api_response(status="error", message="not_found", code=404)

            roles = session.exec(select(RoleDB).where(RoleDB.default_template_id == tpl_id)).all()
            links = session.exec(select(TeamTypeRoleLink).where(TeamTypeRoleLink.template_id == tpl_id)).all()
            members = session.exec(select(TeamMemberDB).where(TeamMemberDB.custom_template_id == tpl_id)).all()
            teams = session.exec(select(TeamDB)).all()

            cleared = {
                "roles": [r.id for r in roles],
                "team_type_links": [link.role_id for link in links],
                "team_members": [m.id for m in members],
                "teams": [],
            }

            for role in roles:
                role.default_template_id = None
                session.add(role)
            for link in links:
                link.template_id = None
                session.add(link)
            for member in members:
                member.custom_template_id = None
                session.add(member)
            for team in teams:
                if isinstance(team.role_templates, dict) and tpl_id in team.role_templates.values():
                    team.role_templates = {k: v for k, v in team.role_templates.items() if v != tpl_id}
                    cleared["teams"].append(team.id)
                    session.add(team)

            if any(cleared.values()):
                current_app.logger.warning(f"Template delete clearing references: {tpl_id} refs={cleared}")

            session.delete(tpl)
            session.commit()

            log_audit("template_deleted", {"template_id": tpl_id, "cleared_refs": cleared})
            return api_response(data={"status": "deleted", "cleared": cleared})
    except Exception as e:
        current_app.logger.exception(f"Template delete failed for {tpl_id}: {e}")
        return api_response(
            status="error", message="delete_failed", data={"details": "Template delete failed"}, code=500
        )

@config_bp.route("/llm/generate", methods=["POST"])
@check_auth
@rate_limit(limit=30, window=60)
def llm_generate():
    """
    LLM-Generierung mit Tool-Calling Unterstützung
    """
    request_id = str(uuid.uuid4())
    g.llm_request_id = request_id

    def _log(event: str, **kwargs):
        try:
            log_llm_entry(event=event, request_id=request_id, **kwargs)
        except Exception:
            pass

    data = request.get_json() or {}
    if not isinstance(data, dict):
        _log("llm_error", error="invalid_json")
        return api_response(status="error", message="invalid_json", code=400)

    user_prompt = data.get("prompt") or ""
    tool_calls_input = data.get("tool_calls")
    confirm_tool_calls = bool(data.get("confirm_tool_calls") or data.get("confirmed"))
    stream = bool(data.get("stream"))
    if not user_prompt and not tool_calls_input:
        _log("llm_error", error="missing_prompt")
        return api_response(status="error", message="missing_prompt", code=400)

    # LLM-Konfiguration und Tool-Allowlist
    agent_cfg = current_app.config.get("AGENT_CONFIG", {})
    llm_cfg = agent_cfg.get("llm_config", {})

    is_admin = getattr(g, "is_admin", False)
    allowlist_cfg = agent_cfg.get("llm_tool_allowlist")
    denylist_cfg = agent_cfg.get("llm_tool_denylist", [])
    default_allowlist = {
        "list_teams",
        "list_roles",
        "list_agents",
        "create_team",
        "ensure_team_templates",
        "update_config",
        "assign_role",
        "create_template",
        "update_template",
        "delete_template",
    }

    if allowlist_cfg is None:
        allowed_tools = default_allowlist
    else:
        allowed_tools = allowlist_cfg

    if not is_admin:
        allowed_tools = []

    # Tool-Definitionen für den Prompt (gefiltert)
    tools_desc = json.dumps(
        tool_registry.get_tool_definitions(allowlist=allowed_tools, denylist=denylist_cfg), indent=2, ensure_ascii=False
    )

    system_instruction = f"""Du bist ein hilfreicher KI-Assistent für das Ananta Framework.
Dir stehen folgende Werkzeuge zur Verfügung:
{tools_desc}
"""

    context = data.get("context")
    if context:
        system_instruction += (
            f"\nAktueller Kontext (Templates, Rollen, Teams):\n{json.dumps(context, indent=2, ensure_ascii=False)}\n"
        )

    system_instruction += """
Wenn du eine Aktion ausführen möchtest, antworte AUSSCHLIESSLICH im folgenden JSON-Format.
Beginne die Antwort mit '{' und ende mit '}'. Keine Vor- oder Nachtexte, kein Markdown, kein Prefix wie 'Assistant:'.
{
  "thought": "Deine Überlegung, warum du dieses Tool wählst",
  "tool_calls": [
    { "name": "tool_name", "args": { "arg1": "value1" } }
  ],
  "answer": "Eine kurze Bestätigung für den Nutzer, was du tust"
}

Falls keine Aktion nötig ist, antworte ebenfalls als JSON-Objekt mit leerem tool_calls.

"""
    if stream:
        system_instruction += "\nAntworte im Streaming-Modus als Klartext ohne tool_calls oder JSON.\n"

    history = data.get("history", [])
    if not isinstance(history, list):
        history = []
    # System-Instruction als erste Nachricht in der Historie mitgeben
    full_history = [{"role": "system", "content": system_instruction}] + history

    # LLM-Parameter auflösen
    cfg = data.get("config") or {}
    provider = cfg.get("provider") or llm_cfg.get("provider") or agent_cfg.get("default_provider")
    model = cfg.get("model") or llm_cfg.get("model") or agent_cfg.get("default_model")
    base_url = cfg.get("base_url") or llm_cfg.get("base_url")
    api_key = cfg.get("api_key") or llm_cfg.get("api_key")
    timeout_val = cfg.get("timeout")

    if not base_url and provider:
        provider_urls = current_app.config.get("PROVIDER_URLS", {})
        base_url = provider_urls.get(provider) or agent_cfg.get(f"{provider}_url")

    if not provider:
        _log("llm_error", error="llm_not_configured", reason="missing_provider")
        current_app.logger.warning("LLM request blocked: provider missing")
        return api_response(
            status="error", message="llm_not_configured", data={"details": "LLM provider is not configured"}, code=400
        )

    if provider in {"openai", "anthropic"} and not api_key:
        _log("llm_error", error="llm_api_key_missing", provider=provider)
        current_app.logger.warning(f"LLM request blocked: api_key missing for {provider}")
        return api_response(
            status="error", message="llm_api_key_missing", data={"details": f"API key missing for {provider}"}, code=400
        )

    if not base_url:
        _log("llm_error", error="llm_base_url_missing", provider=provider)
        current_app.logger.warning(f"LLM request blocked: base_url missing for {provider}")
        return api_response(
            status="error",
            message="llm_base_url_missing",
            data={"details": f"Base URL missing for {provider}"},
            code=400,
        )

    _log(
        "llm_request",
        prompt=user_prompt,
        stream=stream,
        confirm_tool_calls=confirm_tool_calls,
        tool_calls_input=tool_calls_input,
        history_len=len(history) if isinstance(history, list) else 0,
        provider=provider,
        model=model,
        base_url=base_url,
        is_admin=is_admin,
    )

    def _extract_json(text: str) -> dict | None:
        clean_text = text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text.split("```json")[1].split("```")[0].strip()
        elif clean_text.startswith("```"):
            clean_text = clean_text.split("```")[1].split("```")[0].strip()
        if clean_text.lower().startswith("assistant:"):
            clean_text = clean_text.split(":", 1)[1].strip()
        # Strip leading/trailing chatter around a JSON object/array.
        first_brace = clean_text.find("{")
        first_bracket = clean_text.find("[")
        if first_brace == -1 and first_bracket == -1:
            return None
        if first_brace == -1:
            start = first_bracket
            end = clean_text.rfind("]")
        elif first_bracket == -1:
            start = first_brace
            end = clean_text.rfind("}")
        else:
            start = min(first_brace, first_bracket)
            end = clean_text.rfind("}" if start == first_brace else "]")
        if end == -1:
            return None
        clean_text = clean_text[start : end + 1].strip()
        try:
            return json.loads(clean_text)
        except Exception:
            return None

    response_text = ""
    res_json = None
    tool_calls = []

    if tool_calls_input and confirm_tool_calls:
        if not isinstance(tool_calls_input, list):
            _log("llm_error", error="invalid_tool_calls")
            return api_response(status="error", message="invalid_tool_calls", code=400)
        tool_calls = tool_calls_input
        res_json = {"answer": ""}
    else:
        response_text = generate_text(
            prompt=user_prompt,
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            history=full_history,
            timeout=timeout_val,
        )
        if not response_text or not response_text.strip():
            _log("llm_error", error="llm_empty_response")
            return api_response(data={"response": "LLM returned empty response. Please try again."}, status="ok")

        if stream:
            _log("llm_response", response=response_text, tool_calls=[], status="stream")

            def _event_stream(text: str):
                chunk_size = 80
                for i in range(0, len(text), chunk_size):
                    chunk = text[i : i + chunk_size]
                    yield f"data: {chunk}\\n\\n"
                yield "event: done\\ndata: [DONE]\\n\\n"

            return Response(stream_with_context(_event_stream(response_text)), mimetype="text/event-stream")

        res_json = _extract_json(response_text)
        if res_json is None:
            _log("llm_response", response=response_text, tool_calls=[], status="no_json")
            inferred_tool_calls = _infer_tool_calls_from_prompt(user_prompt, context=context if isinstance(context, dict) else None)
            if inferred_tool_calls:
                res_json = {
                    "answer": "Ich habe passende Admin-Aktionen vorbereitet. Bitte bestaetigen.",
                    "tool_calls": inferred_tool_calls,
                    "thought": "Intent fallback",
                }
            # If it's just plain text and no JSON was expected but LLM gave it anyway, or vice-versa.
            # We try to wrap it if it looks like a simple answer.
            elif response_text and len(response_text.strip()) > 0:
                res_json = {"answer": response_text.strip(), "tool_calls": [], "thought": ""}
            else:
                repair_prompt = (
                    f"Assistant (invalid JSON): {response_text}\n\n"
                    "System: Antworte AUSSCHLIESSLICH mit gueltigem JSON im oben beschriebenen Format. "
                    "Beginne mit '{' und ende mit '}'. Kein Freitext, keine Markdown-Bloecke, "
                    "kein Prefix wie 'Assistant:'."
                )
                response_text = generate_text(
                    prompt=repair_prompt,
                    provider=provider,
                    model=model,
                    base_url=base_url,
                    api_key=api_key,
                    history=full_history,
                    timeout=timeout_val,
                )
                if not response_text or not response_text.strip():
                    _log("llm_error", error="llm_empty_response")
                    return api_response(
                        data={"response": "LLM returned empty response during repair. Please try again."}, status="ok"
                    )
                res_json = _extract_json(response_text)
                if res_json is None:
                    inferred_tool_calls = _infer_tool_calls_from_prompt(
                        user_prompt, context=context if isinstance(context, dict) else None
                    )
                    if inferred_tool_calls:
                        res_json = {
                            "answer": "Ich habe passende Admin-Aktionen vorbereitet. Bitte bestaetigen.",
                            "tool_calls": inferred_tool_calls,
                            "thought": "Intent fallback",
                        }
                if res_json is None and response_text:
                    res_json = {"answer": response_text.strip(), "tool_calls": [], "thought": ""}

        if res_json is None:
            _log("llm_response", response=response_text, tool_calls=[], status="no_json")
            return api_response(data={"response": response_text})

        tool_calls = res_json.get("tool_calls", [])
        if not tool_calls:
            inferred_tool_calls = _infer_tool_calls_from_prompt(user_prompt, context=context if isinstance(context, dict) else None)
            if inferred_tool_calls:
                tool_calls = inferred_tool_calls
                res_json["tool_calls"] = inferred_tool_calls
                if not res_json.get("answer"):
                    res_json["answer"] = "Ich habe passende Admin-Aktionen vorbereitet. Bitte bestaetigen."
        if tool_calls and not confirm_tool_calls:
            if not is_admin:
                _log("llm_blocked", tool_calls=tool_calls, reason="admin_required")
                return api_response(
                    data={
                        "response": res_json.get("answer") or "Tool calls require admin privileges.",
                        "tool_calls": tool_calls,
                        "blocked": True,
                    }
                )
            _log("llm_requires_confirmation", tool_calls=tool_calls)
            return api_response(
                data={
                    "response": res_json.get("answer"),
                    "requires_confirmation": True,
                    "thought": res_json.get("thought"),
                    "tool_calls": tool_calls,
                }
            )

    if tool_calls_input and confirm_tool_calls and not tool_calls:
        _log("llm_no_tool_calls")
        return api_response(data={"response": "No tool calls to execute."})

    if tool_calls and not confirm_tool_calls:
        _log("llm_requires_confirmation", tool_calls=tool_calls)
        return api_response(
            data={
                "response": "Pending actions require confirmation.",
                "requires_confirmation": True,
                "tool_calls": tool_calls,
            }
        )

    if tool_calls:
        if not is_admin:
            return api_response(
                status="error", message="forbidden", data={"details": "Admin privileges required"}, code=403
            )

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
            _log("llm_blocked", tool_calls=blocked_tools, reason="tool_not_allowed")
            blocked_results = [
                {"tool": name, "success": False, "output": None, "error": "tool_not_allowed"} for name in blocked_tools
            ]
            return api_response(
                data={
                    "response": f"Tool calls blocked: {', '.join(blocked_tools)}",
                    "tool_results": blocked_results,
                    "blocked_tools": blocked_tools,
                }
            )

        token_usage = {
            "prompt_tokens": estimate_text_tokens(user_prompt),
            "history_tokens": estimate_text_tokens(json.dumps(full_history, ensure_ascii=False)),
            "completion_tokens": estimate_text_tokens(response_text or json.dumps(res_json or {}, ensure_ascii=False)),
            "tool_calls_tokens": estimate_tool_calls_tokens(tool_calls),
        }
        token_usage["estimated_total_tokens"] = sum(int(token_usage.get(k) or 0) for k in token_usage)
        guardrail_decision = evaluate_tool_call_guardrails(tool_calls, agent_cfg, token_usage=token_usage)
        if not guardrail_decision.allowed:
            details = {"tools": guardrail_decision.blocked_tools, "reasons": guardrail_decision.reasons, **guardrail_decision.details}
            log_audit("tool_calls_guardrail_blocked", details)
            _log("llm_blocked", tool_calls=guardrail_decision.blocked_tools, reason="tool_guardrail_blocked")
            blocked_results = [
                {"tool": name, "success": False, "output": None, "error": "tool_guardrail_blocked"}
                for name in (guardrail_decision.blocked_tools or [tc.get("name", "<missing>") for tc in tool_calls])
            ]
            return api_response(
                data={
                    "response": "Tool calls blocked by guardrails.",
                    "tool_results": blocked_results,
                    "blocked_tools": guardrail_decision.blocked_tools,
                    "blocked_reasons": guardrail_decision.reasons,
                    "guardrails": guardrail_decision.details,
                }
            )

        results = []
        for tc in tool_calls:
            name = tc.get("name")
            args = tc.get("args", {})
            current_app.logger.info(f"KI ruft Tool auf: {name} mit {args}")
            tool_res = tool_registry.execute(name, args)
            results.append(
                {"tool": name, "success": tool_res.success, "output": tool_res.output, "error": tool_res.error}
            )
        _log("llm_tool_results", tool_calls=tool_calls, results=results)

        tool_history = full_history + [
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": json.dumps({"tool_calls": tool_calls})},
            {"role": "system", "content": f"Tool Results: {json.dumps(results)}"},
        ]

        final_response = generate_text(
            prompt="Bitte gib eine finale Antwort an den Nutzer basierend auf diesen Ergebnissen.",
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            history=tool_history,
            timeout=timeout_val,
        )
        if not final_response or not final_response.strip():
            _log("llm_error", error="llm_empty_response")
            return api_response(
                status="error", message="llm_failed", data={"details": "LLM returned empty response"}, code=502
            )

        if stream:
            _log("llm_response", response=final_response, tool_calls=tool_calls, status="stream")

            def _event_stream(text: str):
                chunk_size = 80
                for i in range(0, len(text), chunk_size):
                    chunk = text[i : i + chunk_size]
                    yield f"data: {chunk}\\n\\n"
                yield "event: done\\ndata: [DONE]\\n\\n"

            return Response(stream_with_context(_event_stream(final_response)), mimetype="text/event-stream")
        _log("llm_response", response=final_response, tool_calls=tool_calls, status="tool_results")
        return api_response(data={"response": final_response, "tool_results": results})

    final_text = res_json.get("answer", response_text)
    _log("llm_response", response=final_text, tool_calls=tool_calls, status="ok")
    return api_response(data={"response": final_text})
