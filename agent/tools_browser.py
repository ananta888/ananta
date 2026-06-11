from flask import current_app

from agent.tools import registry


@registry.register(
    name="web_fetch",
    description="Ruft den Textinhalt einer Webseite ab.",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Die abzurufende URL"},
        },
        "required": ["url"],
    },
)
def web_fetch_tool(url: str):
    from agent.services.platform_governance_service import get_platform_governance_service
    gov = get_platform_governance_service()
    cfg = current_app.config.get("AGENT_CONFIG")

    if not gov.evaluate_action_pack_access("browser", cfg):
        return {"error": "Action Pack 'browser' ist deaktiviert."}

    import requests
    from lxml import html
    try:
        from agent.common.audit import log_audit
        log_audit("web_fetch", {"url": url})

        headers = {"User-Agent": "AnantaAgent/1.0"}
        res = requests.get(url, headers=headers, timeout=15)
        res.raise_for_status()

        tree = html.fromstring(res.content)
        for script in tree.xpath("//script | //style"):
            if script.getparent() is not None:
                script.getparent().remove(script)

        text = tree.text_content()
        cleaned_text = " ".join(text.split())

        return {
            "content": cleaned_text[:12000],
            "url": url,
            "status_code": res.status_code
        }
    except Exception as e:
        return {"error": f"Fehler beim Abrufen von {url}: {e}"}


@registry.register(
    name="web_search",
    description="Sucht im Web nach Informationen (simuliert/mock).",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Suchbegriff"},
        },
        "required": ["query"],
    },
)
def web_search_tool(query: str):
    from agent.services.platform_governance_service import get_platform_governance_service
    gov = get_platform_governance_service()
    cfg = current_app.config.get("AGENT_CONFIG")

    if not gov.evaluate_action_pack_access("browser", cfg):
        return {"error": "Action Pack 'browser' ist deaktiviert."}

    from agent.common.audit import log_audit
    log_audit("web_search", {"query": query})

    return {
        "message": "Websuche ist derzeit im Mock-Modus. Bitte konfigurieren Sie ein Research-Backend (z.B. DeerFlow).",
        "query": query,
        "results": []
    }
