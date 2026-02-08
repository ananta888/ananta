import logging
import time
import json
import concurrent.futures
from typing import Optional
from flask import Blueprint, jsonify, current_app, request, g
from agent.common.errors import api_response
from agent.utils import (
    validate_request, _extract_command, _extract_reason,
    _extract_tool_calls, _log_terminal_entry
)
from agent.llm_integration import _call_llm
from agent.auth import check_auth
from agent.repository import task_repo, role_repo, template_repo, team_member_repo
from agent.routes.tasks.utils import _update_local_task_status, _forward_to_worker
from agent.models import (
    TaskStepProposeRequest, TaskStepProposeResponse, 
    TaskStepExecuteRequest, TaskStepExecuteResponse
)
from agent.metrics import TASK_COMPLETED, TASK_FAILED, RETRIES_TOTAL
from agent.shell import get_shell
from agent.tools import registry as tool_registry

execution_bp = Blueprint("tasks_execution", __name__)

def _get_system_prompt_for_task(tid: str) -> Optional[str]:
    from agent.repository import team_repo
    task = task_repo.get_by_id(tid)
    if not task:
        return None
    
    role_id = task.assigned_role_id
    template_id = None
    
    # Falls keine Rolle direkt zugewiesen, versuchen wir sie über den Agenten und das Team zu finden
    if task.team_id and task.assigned_agent_url:
        members = team_member_repo.get_by_team(task.team_id)
        for m in members:
            if m.agent_url == task.assigned_agent_url:
                if not role_id:
                    role_id = m.role_id
                template_id = getattr(m, "custom_template_id", None)
                break
                
    if role_id and not template_id:
        role = role_repo.get_by_id(role_id)
        if role:
            template_id = role.default_template_id
            
    if template_id:
        template = template_repo.get_by_id(template_id)
        if template:
            prompt = template.prompt_template
            
            # Variablen ersetzen
            variables = {
                "agent_name": current_app.config.get("AGENT_NAME", "Unbekannter Agent"),
                "task_title": task.title or "Kein Titel",
                "task_description": task.description or "Keine Beschreibung"
            }
            
            if task.team_id:
                team = team_repo.get_by_id(task.team_id)
                if team:
                    variables["team_name"] = team.name
            
            if role_id:
                role = role_repo.get_by_id(role_id)
                if role:
                    variables["role_name"] = role.name
            
            for k, v in variables.items():
                prompt = prompt.replace("{{" + k + "}}", str(v))
                
            return prompt
    
    return None

def _run_async_propose(app_instance, tid: str, provider: str, model: str, prompt: str, urls: dict, api_key: str, history: list, agent_name: str):
    with app_instance.app_context():
        try:
            raw_res = _call_llm(
                provider=provider,
                model=model,
                prompt=prompt,
                urls=urls,
                api_key=api_key,
                history=history
            )
            
            if not raw_res:
                raise RuntimeError("LLM-Aufruf lieferte kein Ergebnis (Timeout oder Fehler).")
            
            reason = _extract_reason(raw_res)
            command = _extract_command(raw_res)
            tool_calls = _extract_tool_calls(raw_res)
            
            proposal = {"reason": reason}
            if command and command != raw_res.strip():
                proposal["command"] = command
            if tool_calls:
                proposal["tool_calls"] = tool_calls

            _update_local_task_status(tid, "proposing", last_proposal=proposal)
            
            _log_terminal_entry(agent_name, 0, "in", prompt=prompt, task_id=tid)
            _log_terminal_entry(agent_name, 0, "out", reason=reason, command=command, tool_calls=tool_calls, task_id=tid)
            
            logging.info(f"Asynchroner Vorschlag für Task {tid} abgeschlossen.")
        except Exception as e:
            logging.error(f"Fehler bei asynchronem Vorschlag für Task {tid}: {e}")
            try:
                _update_local_task_status(tid, "failed", error=str(e))
            except Exception as e2:
                logging.error(f"Fehler beim Setzen des Fehlerstatus für Task {tid}: {e2}")

@execution_bp.route("/step/propose", methods=["POST"])
@check_auth
@validate_request(TaskStepProposeRequest)
def propose_step():
    """
    Nächsten Schritt vorschlagen (LLM)
    ---
    responses:
      200:
        description: Vorschlag erhalten
    """
    data: TaskStepProposeRequest = g.validated_data
    cfg = current_app.config["AGENT_CONFIG"]
    
    prompt = data.prompt or "Was soll ich als nächstes tun?"
    
    if data.providers:
        results = {}
        def _call_single(p_name):
            try:
                p_parts = p_name.split(":", 1)
                p = p_parts[0]
                m = p_parts[1] if len(p_parts) > 1 else (data.model or cfg.get("model", "llama3"))
                
                res = _call_llm(
                    provider=p,
                    model=m,
                    prompt=prompt,
                    urls=current_app.config["PROVIDER_URLS"],
                    api_key=current_app.config["OPENAI_API_KEY"]
                )
                return p_name, {
                    "raw": res,
                    "reason": _extract_reason(res),
                    "command": _extract_command(res),
                    "tool_calls": _extract_tool_calls(res)
                }
            except Exception as e:
                return p_name, {"error": str(e)}

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(data.providers)) as executor:
            future_to_provider = {executor.submit(_call_single, p): p for p in data.providers}
            for future in concurrent.futures.as_completed(future_to_provider):
                p_name, res = future.result()
                results[p_name] = res
        
        main_p = data.providers[0]
        main_res = results.get(main_p, {})
        
        return api_response(data=TaskStepProposeResponse(
            reason=main_res.get("reason", "Fehler bei primärem Provider"),
            command=main_res.get("command"),
            tool_calls=main_res.get("tool_calls"),
            raw=main_res.get("raw", ""),
            comparisons=results
        ).model_dump())

    provider = data.provider or cfg.get("provider", "ollama")
    model = data.model or cfg.get("model", "llama3")
    
    raw_res = _call_llm(
        provider=provider,
        model=model,
        prompt=prompt,
        urls=current_app.config["PROVIDER_URLS"],
        api_key=current_app.config["OPENAI_API_KEY"]
    )
    
    reason = _extract_reason(raw_res)
    command = _extract_command(raw_res)
    tool_calls = _extract_tool_calls(raw_res)
    
    if data.task_id:
        proposal = {"reason": reason}
        if command and command != raw_res.strip():
            proposal["command"] = command
        if tool_calls:
            proposal["tool_calls"] = tool_calls
            
        _update_local_task_status(data.task_id, "proposing", last_proposal=proposal)
        _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "in", prompt=prompt, task_id=data.task_id)
        _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "out", reason=reason, command=command, tool_calls=tool_calls, task_id=data.task_id)

    return api_response(data=TaskStepProposeResponse(
        reason=reason,
        command=command if command != raw_res.strip() else None,
        tool_calls=tool_calls,
        raw=raw_res
    ).model_dump())

@execution_bp.route("/step/execute", methods=["POST"])
@check_auth
@validate_request(TaskStepExecuteRequest)
def execute_step():
    """
    Vorgeschlagenen Schritt ausführen
    ---
    responses:
      200:
        description: Schritt ausgeführt
    """
    data: TaskStepExecuteRequest = g.validated_data
    
    output_parts = []
    overall_exit_code = 0
    
    if data.tool_calls:
        for tc in data.tool_calls:
            name = tc.get("name")
            args = tc.get("args", {})
            tool_res = tool_registry.execute(name, args)
            res_str = f"Tool '{name}': {'Erfolg' if tool_res.success else 'Fehler'}"
            if tool_res.output: res_str += f"\nOutput: {tool_res.output}"
            if tool_res.error:
                res_str += f"\nError: {tool_res.error}"
                overall_exit_code = 1
            output_parts.append(res_str)

    if data.command:
        shell = get_shell()
        output, exit_code = shell.execute(data.command, timeout=data.timeout or 60)
        output_parts.append(output)
        if exit_code != 0:
            overall_exit_code = exit_code
    
    final_output = "\n---\n".join(output_parts)
    final_exit_code = overall_exit_code

    if data.task_id:
        status = "completed" if final_exit_code == 0 else "failed"
        _update_local_task_status(data.task_id, status, last_output=final_output, last_exit_code=final_exit_code)
        if status == "completed": TASK_COMPLETED.inc()
        else: TASK_FAILED.inc()

    _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "out", command=data.command, tool_calls=data.tool_calls, task_id=data.task_id)
    _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "in", output=final_output, exit_code=final_exit_code, task_id=data.task_id)
    
    return api_response(data=TaskStepExecuteResponse(
        output=final_output,
        exit_code=final_exit_code,
        task_id=data.task_id,
        status="completed" if final_exit_code == 0 else "failed"
    ).model_dump())

@execution_bp.route("/tasks/<tid>/step/propose", methods=["POST"])
@check_auth
@validate_request(TaskStepProposeRequest)
def task_propose(tid):
    """
    Vorschlag für einen spezifischen Task (v2)
    ---
    parameters:
      - name: tid
        in: path
        type: string
        required: true
    responses:
      200:
        description: Vorschlag erhalten
    """
    from agent.config import settings
    data: TaskStepProposeRequest = g.validated_data
    from agent.routes.tasks.utils import _get_local_task_status
    task = _get_local_task_status(tid)
    if not task:
        return api_response(status="error", message="not_found", code=404)
    
    worker_url = task.get("assigned_agent_url")
    if worker_url:
        my_url = settings.agent_url or f"http://localhost:{settings.port}"
        if worker_url.rstrip("/") != my_url.rstrip("/"):
            try:
                res = _forward_to_worker(
                    worker_url, 
                    f"/tasks/{tid}/step/propose", 
                    data.model_dump(), 
                    token=task.get("assigned_agent_token")
                )
                if isinstance(res, dict) and "command" in res:
                     _update_local_task_status(tid, "proposing", last_proposal=res)
                return api_response(data=res)
            except Exception as e:
                logging.error(f"Forwarding an {worker_url} fehlgeschlagen: {e}")
                return api_response(status="error", message="forwarding_failed", data={"details": str(e)}, code=502)

    cfg = current_app.config["AGENT_CONFIG"]
    base_prompt = data.prompt or task.get("description") or task.get("prompt") or "Bearbeite Task " + tid
    tools_desc = json.dumps(tool_registry.get_tool_definitions(), indent=2, ensure_ascii=False)

    system_prompt = _get_system_prompt_for_task(tid)
    if system_prompt:
        prompt = (
            f"{system_prompt}\n\n"
            f"Aktueller Auftrag: {base_prompt}\n\n"
            f"Dir stehen folgende Werkzeuge zur Verfügung:\n{tools_desc}\n\n"
            "Antworte IMMER im JSON-Format mit folgenden Feldern:\n"
            "{\n"
            "  \"reason\": \"Kurze Begründung\",\n"
            "  \"command\": \"Shell-Befehl (optional)\",\n"
            "  \"tool_calls\": [ { \"name\": \"tool_name\", \"args\": { \"arg1\": \"val1\" } } ] (optional)\n"
            "}"
        )
    else:
        prompt = (
            f"{base_prompt}\n\n"
            f"Dir stehen folgende Werkzeuge zur Verfügung:\n{tools_desc}\n\n"
            "Antworte IMMER im JSON-Format mit folgenden Feldern:\n"
            "{\n"
            "  \"reason\": \"Kurze Begründung\",\n"
            "  \"command\": \"Shell-Befehl (optional)\",\n"
            "  \"tool_calls\": [ { \"name\": \"tool_name\", \"args\": { \"arg1\": \"val1\" } } ] (optional)\n"
            "}"
        )
    
    if data.providers:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = {
                executor.submit(
                    _call_llm,
                    provider=p.split(":")[0],
                    model=p.split(":")[1] if ":" in p else cfg.get("model", "llama3"),
                    prompt=prompt,
                    urls=current_app.config["PROVIDER_URLS"],
                    api_key=current_app.config["OPENAI_API_KEY"],
                    history=task.get("history", [])
                ): p for p in data.providers
            }
            results = {}
            for future in concurrent.futures.as_completed(futures):
                p_name = futures[future]
                try:
                    res = future.result()
                    if res:
                        results[p_name] = {
                            "reason": _extract_reason(res),
                            "command": _extract_command(res),
                            "tool_calls": _extract_tool_calls(res),
                            "raw": res
                        }
                except Exception as e:
                    logging.error(f"Multi-Provider Call for {p_name} failed: {e}")
            
            if not results:
                return api_response(status="error", message="all_llm_failed", code=502)
            
            best_p = list(results.keys())[0]
            main_res = results[best_p]
            
            proposal = {"reason": main_res["reason"]}
            if main_res["command"] and main_res["command"] != main_res["raw"].strip():
                proposal["command"] = main_res["command"]
            if main_res["tool_calls"]:
                proposal["tool_calls"] = main_res["tool_calls"]
            proposal["comparisons"] = results

            _update_local_task_status(tid, "proposing", last_proposal=proposal)
            
            return api_response(data={
                "status": "proposing",
                "reason": main_res["reason"],
                "command": main_res["command"] if main_res["command"] != main_res["raw"].strip() else None,
                "tool_calls": main_res["tool_calls"],
                "raw": main_res["raw"],
                "comparisons": results
            })

    raw_res = _call_llm(
        provider=data.provider or cfg.get("provider", "ollama"),
        model=data.model or cfg.get("model", "llama3"),
        prompt=prompt,
        urls=current_app.config["PROVIDER_URLS"],
        api_key=current_app.config["OPENAI_API_KEY"],
        history=task.get("history", [])
    )
    
    if not raw_res:
        return api_response(status="error", message="llm_failed", code=502)

    reason = _extract_reason(raw_res)
    command = _extract_command(raw_res)
    tool_calls = _extract_tool_calls(raw_res)
    
    proposal = {"reason": reason}
    if command and command != raw_res.strip():
        proposal["command"] = command
    if tool_calls:
        proposal["tool_calls"] = tool_calls

    _update_local_task_status(tid, "proposing", last_proposal=proposal)
    
    _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "in", prompt=prompt, task_id=tid)
    _log_terminal_entry(current_app.config["AGENT_NAME"], 0, "out", reason=reason, command=command, tool_calls=tool_calls, task_id=tid)
    
    return api_response(data={
        "status": "proposing",
        "reason": reason,
        "command": command if command != raw_res.strip() else None,
        "tool_calls": tool_calls,
        "raw": raw_res
    })

@execution_bp.route("/tasks/<tid>/step/execute", methods=["POST"])
@check_auth
@validate_request(TaskStepExecuteRequest)
def task_execute(tid):
    """
    Ausführung für einen spezifischen Task (v2)
    ---
    parameters:
      - name: tid
        in: path
        type: string
        required: true
    responses:
      200:
        description: Schritt ausgeführt
    """
    from agent.config import settings
    data: TaskStepExecuteRequest = g.validated_data
    from agent.routes.tasks.utils import _get_local_task_status
    task = _get_local_task_status(tid)
    if not task:
        return api_response(status="error", message="not_found", code=404)
    
    worker_url = task.get("assigned_agent_url")
    if worker_url:
        my_url = settings.agent_url or f"http://localhost:{settings.port}"
        if worker_url.rstrip("/") != my_url.rstrip("/"):
            try:
                res = _forward_to_worker(
                    worker_url, 
                    f"/tasks/{tid}/step/execute", 
                    data.model_dump(), 
                    token=task.get("assigned_agent_token")
                )
                
                if isinstance(res, dict) and "status" in res:
                    history = task.get("history", [])
                    history.append({
                        "prompt": task.get("description"),
                        "reason": "Forwarded to " + worker_url,
                        "command": data.command or task.get("last_proposal", {}).get("command"),
                        "output": res.get("output"),
                        "exit_code": res.get("exit_code"),
                        "timestamp": time.time()
                    })
                    _update_local_task_status(tid, res["status"], history=history)
                
                return api_response(data=res)
            except Exception as e:
                logging.error(f"Forwarding (Execute) an {worker_url} fehlgeschlagen: {e}")
                return api_response(status="error", message="forwarding_failed", data={"details": str(e)}, code=502)

    command = data.command
    tool_calls = data.tool_calls
    reason = "Direkte Ausführung"
    
    if not command and not tool_calls:
        proposal = task.get("last_proposal")
        if not proposal:
            return api_response(status="error", message="no_proposal", code=400)
        command = proposal.get("command")
        tool_calls = proposal.get("tool_calls")
        reason = proposal.get("reason", "Vorschlag ausgeführt")
    
    output_parts = []
    overall_exit_code = 0
    
    if tool_calls:
        for tc in tool_calls:
            name = tc.get("name")
            args = tc.get("args", {})
            current_app.logger.info(f"Task {tid} führt Tool aus: {name} mit {args}")
            tool_res = tool_registry.execute(name, args)
            
            res_str = f"Tool '{name}': {'Erfolg' if tool_res.success else 'Fehler'}"
            if tool_res.output:
                res_str += f"\nOutput: {tool_res.output}"
            if tool_res.error:
                res_str += f"\nError: {tool_res.error}"
                overall_exit_code = 1
            
            output_parts.append(res_str)

    if command:
        shell = get_shell()
        retries_left = data.retries or 0
        cmd_output, cmd_exit_code = "", -1
        
        while True:
            cmd_output, cmd_exit_code = shell.execute(command, timeout=data.timeout or 60)
            if cmd_exit_code == 0 or retries_left <= 0:
                break
            
            retries_left -= 1
            RETRIES_TOTAL.inc()
            logging.info(f"Task {tid} Shell-Fehler (exit_code {cmd_exit_code}). Wiederholung... ({retries_left} übrig)")
            time.sleep(data.retry_delay or 1)
        
        output_parts.append(cmd_output)
        if cmd_exit_code != 0:
            overall_exit_code = cmd_exit_code

    output = "\n---\n".join(output_parts)
    exit_code = overall_exit_code

    history = task.get("history", [])
    history.append({
        "prompt": task.get("description"),
        "reason": reason,
        "command": command,
        "tool_calls": tool_calls,
        "output": output,
        "exit_code": exit_code,
        "timestamp": time.time()
    })
    
    status = "completed" if exit_code == 0 else "failed"
    if status == "completed": TASK_COMPLETED.inc()
    else: TASK_FAILED.inc()

    _update_local_task_status(tid, status, history=history)

    _log_terminal_entry(current_app.config["AGENT_NAME"], len(history), "out", command=command, task_id=tid)
    _log_terminal_entry(current_app.config["AGENT_NAME"], len(history), "in", output=output, exit_code=exit_code, task_id=tid)

    res = TaskStepExecuteResponse(output=output, exit_code=exit_code, task_id=tid, status=status)
    return api_response(data=res.model_dump())
