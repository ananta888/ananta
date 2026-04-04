#!/usr/bin/env python3
import json
import time
from urllib import request

BASE = "http://127.0.0.1:4444/wd/hub"


def wd(method: str, path: str, payload=None):
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = request.Request(BASE + path, data=data, headers=headers, method=method)
    with request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def js(session_id: str, script: str, args=None):
    return wd(
        "POST",
        f"/session/{session_id}/execute/sync",
        {"script": script, "args": args or []},
    )


def wait_for(session_id: str, script: str, timeout: int = 25) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            out = js(session_id, script)
            if out.get("value"):
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def main():
    caps = {
        "capabilities": {
            "alwaysMatch": {"browserName": "firefox", "acceptInsecureCerts": True}
        }
    }
    created = wd("POST", "/session", caps)
    session_id = created.get("sessionId") or (created.get("value") or {}).get("sessionId")
    if not session_id:
        raise RuntimeError(f"No session id returned: {created}")

    print("session", session_id, flush=True)
    try:
        wd("POST", f"/session/{session_id}/url", {"url": "http://angular-frontend:4200/login"})
        wait_for(session_id, "return !!document.querySelector('input[name=\"username\"]')")
        js(
            session_id,
            """
            const u=document.querySelector('input[name="username"]');
            const p=document.querySelector('input[name="password"]');
            if(u){u.value='admin';u.dispatchEvent(new Event('input',{bubbles:true}));}
            if(p){p.value='AnantaAdminPassword123!';p.dispatchEvent(new Event('input',{bubbles:true}));}
            return !!(u&&p);
            """,
        )
        time.sleep(0.6)
        js(
            session_id,
            """
            const b=[...document.querySelectorAll('button')]
              .find(x=>/Anmelden|Verifizieren/i.test((x.textContent||'').trim()));
            if(b){b.click(); return true;}
            return false;
            """,
        )
        ok = wait_for(
            session_id,
            "return location.pathname.includes('/dashboard') || !!document.querySelector('h2')",
        )
        print("login_navigate", ok, flush=True)
        time.sleep(1.0)

        routes = [
            "/dashboard",
            "/agents",
            "/board",
            "/artifacts",
            "/auto-planner",
            "/webhooks",
            "/templates",
            "/teams",
            "/settings",
        ]
        for route in routes:
            clicked = js(
                session_id,
                """
                const href = arguments[0];
                const a = [...document.querySelectorAll('a[href]')]
                  .find(x => x.getAttribute('href') === href);
                if (a) { a.click(); return true; }
                location.href = href;
                return false;
                """,
                [route],
            ).get("value")
            time.sleep(2.2)
            title = js(
                session_id,
                "const h=document.querySelector('h1,h2,h3'); return h ? h.textContent.trim() : document.title;",
            ).get("value")
            print("route", route, "clicked", bool(clicked), "title", title, flush=True)

        js(session_id, "window.scrollTo({top: document.body.scrollHeight, behavior:'smooth'}); return true;")
        time.sleep(1.2)
        js(session_id, "window.scrollTo({top: 0, behavior:'smooth'}); return true;")
        time.sleep(1.0)
    finally:
        try:
            wd("DELETE", f"/session/{session_id}")
        except Exception as e:
            print("quit_error", str(e), flush=True)


if __name__ == "__main__":
    main()
