from flask import current_app

from agent.tools import registry


@registry.register(
    name="shell_execute",
    description="Fuehrt ein Shell-Kommando aus (Hochrisiko, standardmaessig deaktiviert).",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Das auszufuehrende Kommando"},
            "timeout": {"type": "integer", "description": "Timeout in Sekunden", "default": 30},
        },
        "required": ["command"],
    },
)
def shell_execute_tool(command: str, timeout: int = 30):
    from agent.services.platform_governance_service import get_platform_governance_service
    gov = get_platform_governance_service()
    cfg = current_app.config.get("AGENT_CONFIG")

    if not gov.evaluate_action_pack_access("shell", cfg):
        return {"error": "Action Pack 'shell' ist deaktiviert. Shell-Zugriff muss explizit freigeschaltet werden."}

    blacklist = ["rm -rf", "sudo ", "chmod 777", "mkfs", "dd ", "shutdown", "reboot"]
    cmd_lower = command.lower()
    if any(b in cmd_lower for b in blacklist):
        return {"error": f"Kommando enthaelt verbotene Sequenzen (Security Policy)."}

    import subprocess
    try:
        from agent.common.audit import log_audit
        log_audit("shell_execute", {"command": command})

        res = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
        return {
            "stdout": res.stdout,
            "stderr": res.stderr,
            "exit_code": res.returncode
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Kommando-Timeout nach {timeout}s"}
    except Exception as e:
        return {"error": f"Fehler bei Ausfuehrung: {e}"}
