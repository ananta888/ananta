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
    with request.urlopen(req, timeout=40) as resp:
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


def step_nav(session_id: str, route: str):
    js(
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
    )
    time.sleep(1.5)


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
        # Login
        wd("POST", f"/session/{session_id}/url", {"url": "http://angular-frontend:4200/login"})
        wait_for(session_id, "return !!document.querySelector('input[name=\"username\"]')", 25)
        js(
            session_id,
            """
            const u=document.querySelector('input[name="username"]');
            const p=document.querySelector('input[name="password"]');
            if(u){u.value='admin';u.dispatchEvent(new Event('input',{bubbles:true}));}
            if(p){p.value='AnantaAdminPassword123!';p.dispatchEvent(new Event('input',{bubbles:true}));}
            const b=[...document.querySelectorAll('button')]
              .find(x=>/Anmelden|Verifizieren|Login/i.test((x.textContent||'').trim()));
            if(b){b.click(); return true;}
            return false;
            """,
        )
        ok_login = wait_for(
            session_id,
            "return location.pathname.includes('/dashboard') || location.pathname==='/'",
            15,
        )
        print("login_ok", ok_login, flush=True)

        # Goal flow
        step_nav(session_id, "/auto-planner")
        goal_name = f"Live Goal {int(time.time())}"
        goal_result = js(
            session_id,
            """
            const goal=arguments[0];
            const input=document.querySelector('[data-testid="auto-planner-goal-input"]');
            const btn=document.querySelector('[data-testid="auto-planner-goal-plan"]');
            if(input){
              input.focus();
              input.value=goal;
              input.dispatchEvent(new Event('input',{bubbles:true}));
            }
            if(btn){ btn.click(); return true; }
            return false;
            """,
            [goal_name],
        ).get("value")
        wait_for(
            session_id,
            "return !!document.querySelector('[data-testid=\"goal-submit-result\"], [data-testid=\"goal-list\"]')",
            20,
        )
        print("goal_submit_clicked", bool(goal_result), "goal", goal_name, flush=True)

        # Templates flow
        step_nav(session_id, "/templates")
        wait_for(
            session_id,
            "return !![...document.querySelectorAll('h1,h2,h3')].find(x=>/Templates/i.test((x.textContent||'')))",
            20,
        )
        template_name = f"Live UI Template {int(time.time())}"
        template_created = js(
            session_id,
            """
            const name=arguments[0];
            const desc='Template aus erweitertem Live-Klicktest';
            const prompt='Du bist {{agent_name}} und bearbeitest {{task_title}}.';
            const nameInput=document.querySelector('input[placeholder="Name"]');
            const descInput=document.querySelector('input[placeholder="Beschreibung"]');
            const promptArea=document.querySelector('textarea[placeholder*="Platzhalter"]');
            const save=[...document.querySelectorAll('button')]
              .find(x=>/Anlegen\\s*\\/\\s*Speichern|Anlegen|Speichern/i.test((x.textContent||'').trim()));
            if(nameInput){
              nameInput.focus();
              nameInput.value=name;
              nameInput.dispatchEvent(new Event('input',{bubbles:true}));
            }
            if(descInput){
              descInput.focus();
              descInput.value=desc;
              descInput.dispatchEvent(new Event('input',{bubbles:true}));
            }
            if(promptArea){
              promptArea.focus();
              promptArea.value=prompt;
              promptArea.dispatchEvent(new Event('input',{bubbles:true}));
            }
            if(save){ save.click(); return true; }
            return false;
            """,
            [template_name],
        ).get("value")
        time.sleep(2.0)
        print("template_create_clicked", bool(template_created), "template", template_name, flush=True)

        # Teams / Blueprint flow
        step_nav(session_id, "/teams")
        wait_for(
            session_id,
            "return !![...document.querySelectorAll('h1,h2,h3')].find(x=>/Blueprint|Teams/i.test((x.textContent||'')))",
            25,
        )
        blueprint_name = f"Live UI Blueprint {int(time.time())}"
        blueprint_created = js(
            session_id,
            """
            const blueprint=arguments[0];
            const panel=document.querySelector('.teams-editor-panel') || document;
            const labels=[...panel.querySelectorAll('label')];
            function inputByLabel(rx){
              const l=labels.find(x=>rx.test((x.textContent||'').trim()));
              if(!l) return null;
              const id=l.getAttribute('for');
              if(id) return panel.querySelector('#'+CSS.escape(id));
              return l.querySelector('input,textarea,select');
            }
            const nameInput=inputByLabel(/^Name$/i) || panel.querySelector('input[placeholder="Name"]');
            const descInput=inputByLabel(/Beschreibung/i) || panel.querySelector('input[placeholder="Beschreibung"]');
            const addRole=[...panel.querySelectorAll('button')].find(x=>/Rolle hinzuf/i.test((x.textContent||'')));
            if(nameInput){
              nameInput.focus();
              nameInput.value=blueprint;
              nameInput.dispatchEvent(new Event('input',{bubbles:true}));
            }
            if(descInput){
              descInput.focus();
              descInput.value='Blueprint aus erweitertem Live-Klicktest';
              descInput.dispatchEvent(new Event('input',{bubbles:true}));
            }
            if(addRole){ addRole.click(); }
            const roleInput=[...panel.querySelectorAll('label')]
              .find(x=>/Rollenname/i.test((x.textContent||'')));
            if(roleInput){
              const id=roleInput.getAttribute('for');
              const el=id ? panel.querySelector('#'+CSS.escape(id)) : roleInput.querySelector('input');
              if(el){
                el.focus();
                el.value='Implementer';
                el.dispatchEvent(new Event('input',{bubbles:true}));
              }
            }
            const selects=[...panel.querySelectorAll('select')];
            for(const s of selects){
              const valid=[...s.options].find(o=>o.value && !o.disabled);
              if(valid){
                s.value=valid.value;
                s.dispatchEvent(new Event('change',{bubbles:true}));
              }
            }
            const create=[...panel.querySelectorAll('button')]
              .find(x=>/^Erstellen$/i.test((x.textContent||'').trim()));
            if(create){ create.click(); return true; }
            return false;
            """,
            [blueprint_name],
        ).get("value")
        time.sleep(2.5)
        print("blueprint_create_clicked", bool(blueprint_created), "blueprint", blueprint_name, flush=True)

        # Team instantiate flow
        team_name = f"Live UI Team {int(time.time())}"
        team_created = js(
            session_id,
            """
            const teamName=arguments[0];
            const fromBlueprint=[...document.querySelectorAll('button')]
              .find(x=>/^Teams aus Blueprint$/i.test((x.textContent||'').trim()));
            if(fromBlueprint) fromBlueprint.click();
            const card=document.querySelector('.card.card-success') || document;
            const selects=[...card.querySelectorAll('select')];
            for(const s of selects){
              const valid=[...s.options].find(o=>o.value && !o.disabled);
              if(valid){
                s.value=valid.value;
                s.dispatchEvent(new Event('change',{bubbles:true}));
              }
            }
            const teamInput=[...card.querySelectorAll('input')]
              .find(x=>/teamname|team name/i.test((x.getAttribute('aria-label')||'') + ' ' + (x.placeholder||'')));
            const byLabel=[...card.querySelectorAll('label')].find(x=>/Teamname/i.test((x.textContent||'')));
            let finalInput=teamInput;
            if(!finalInput && byLabel){
              const id=byLabel.getAttribute('for');
              if(id) finalInput=card.querySelector('#'+CSS.escape(id));
            }
            if(finalInput){
              finalInput.focus();
              finalInput.value=teamName;
              finalInput.dispatchEvent(new Event('input',{bubbles:true}));
            }
            const create=[...card.querySelectorAll('button')]
              .find(x=>/^Team erstellen$/i.test((x.textContent||'').trim()));
            if(create){ create.click(); return true; }
            return false;
            """,
            [team_name],
        ).get("value")
        time.sleep(2.5)
        print("team_create_clicked", bool(team_created), "team", team_name, flush=True)

        # Basic smoke navigation at the end
        for route in ["/dashboard", "/templates", "/teams", "/settings"]:
            step_nav(session_id, route)
            title = js(
                session_id,
                "const h=document.querySelector('h1,h2,h3'); return h ? h.textContent.trim() : document.title;",
            ).get("value")
            print("route", route, "title", title, flush=True)
            time.sleep(0.8)

        visible_error_texts = js(
            session_id,
            """
            const nodes=[...document.querySelectorAll('.notification.error,.toast.toast-error,[role="alert"]')];
            const texts=nodes
              .filter(n => {
                const style = window.getComputedStyle(n);
                return style.display !== 'none' && style.visibility !== 'hidden' && n.getBoundingClientRect().height > 0;
              })
              .map(n=>(n.textContent||'').trim())
              .filter(Boolean)
              .slice(0,10);
            return texts;
            """,
        ).get("value") or []
        has_401 = any("401" in str(t) for t in visible_error_texts)
        print("visible_errors", len(visible_error_texts), "contains_401", has_401, flush=True)
        if visible_error_texts:
            print("visible_error_texts", json.dumps(visible_error_texts, ensure_ascii=True), flush=True)
    finally:
        try:
            wd("DELETE", f"/session/{session_id}")
        except Exception as e:
            print("quit_error", str(e), flush=True)


if __name__ == "__main__":
    main()
