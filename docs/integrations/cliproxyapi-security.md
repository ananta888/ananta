# CLIProxyAPI Security-Grenze (cliproxyapi-008)

CLIProxyAPI ist ein externer HTTP-Proxy der mehrere CLI-Tools und
Accounts unter einer OpenAI-compatible API bündelt. Ananta behandelt
CLIProxyAPI als einen ganz normalen OpenAI-compatible provider —
aber die konsequenzen für die **vertrauensgrenze** muss der nutzer
klar verstehen, bevor er CLIProxyAPI produktiv einsetzt.

Diese seite benennt die neue vertrauensgrenze und gibt
deployment-empfehlungen. Siehe auch:
`docs/integrations/cliproxyapi.md#secrets`.

## Vertrauensgrenze im überblick

```
+----------------------+        +----------------------+        +------------------+
|                      |        |                      |        |                  |
|        Ananta        |  HTTP  |      CLIProxyAPI      | intern |  CLI-Tools /     |
|                      |-------->|                      |------->|  Accounts        |
|  (Hub/Worker/Policy/ | /v1    |  (externer Proxy)     |        |                  |
|   Audit)             |        |                      |        |                  |
+----------------------+        +----------------------+        +------------------+
        |                                  |
        | Ananta kontrolliert:            | CLIProxyAPI kontrolliert:
        | - hub/worker/task-queue         | - welche CLI-tools darunter laufen
        | - policy-gates, audit-events    | - welche accounts/oauth-tokens
        | - tool-loop & schemata          | - rate-limiting / retry-policy
        | - geheimnisse in profiles/vault | - secrets fuer diese CLIs/accounts
        |                                  | - logging innerhalb von CLIProxyAPI
```

## Was Ananta kontrolliert

* **Hub/Worker-Authentifizierung und -Isolation** via
  ananta-internem Auth-Modell (JWT, OIDC, Hub-Policy-Service).
* **Policy-gates** für tool-calls: `agent/services/policy/*` entscheidet
  welche operations erlaubt sind.
* **Audit-trail** über `agent.services.audit.*` — jeder tool-call
  und jedes file-access-event wird geloggt.
* **Workspace-bound path-checks** (DD-013) — wenn CLIProxyAPI indirekt
  code-ausführung triggert, läuft das immer noch durch anantas
  trusted-worker-container-checks.
* **Secret-profile-resolution** (`_resolve_profile_api_key`) —
  ananta kennt die profiles, nicht aber die inhalte der secrets.

## Was CLIProxyAPI kontrolliert

CLIProxyAPI ist eine **externe komponente**, die Ananta *nicht*
kontrolliert:

* Welche CLI-tools darunter laufen (z.B. `codex`, `claude`, `gemini`).
* Welche accounts/OAuth-refresh-tokens die CLI-tools nutzen.
* Wie oft rate-limit-retry-strategien gefeuert werden.
* Welche modelle pro account verfügbar sind.
* Ob CLIProxyAPI selbst lokal/remote/offen ist.

Wichtig: Ananta sieht nur HTTP-requests und -responses. Es hat
**keine** sicht auf die CLI-aufrufe *innerhalb* von CLIProxyAPI.

## Risiken

### 1. OAuth-/Account-credentials

CLIProxyAPI speichert account-OAuth-tokens und refresh-tokens für
die darunterliegenden CLIs. Diese tokens sind *nicht* in
ananta-secret-profiles, sondern in CLIProxyAPI's eigenem storage.

Wenn CLIProxyAPI kompromittiert wird, sind diese tokens weg. Wenn
ein angreifer zugriff auf den CLIProxyAPI-host hat, kann er die
tokens auslesen.

**Mitigation**: CLIProxyAPI's secret-storage verschlüsseln (file-
oder OS-keyring), CLIProxyAPI niemals unverschlüsselt ins netzwerk
stellen.

### 2. Tool-call-injection

Wenn CLIProxyAPI selbst kompromittiert ist (oder ein bösartiger
CLI-tool drunter läuft), können tool-call-responses an Ananta
geliefert werden die *nicht* vom gewünschten modell kommen.

**Mitigation**: Anantas policy-layer (`agent.services.policy.*`)
filtert alle tool-calls unabhängig von der quelle. Anantas
graph-evidence-policy (siehe
`docs/architecture/codecompass-import-trust.md`) prüft evidence-pfade.

### 3. Audit-Lücke zwischen Ananta und CLIProxyAPI

Anantas audit-trail sieht **nur** die HTTP-ebene. Was CLIProxyAPI
intern tut (welche CLI, welcher account, welche antwort) ist nicht
in ananta-audit.

**Mitigation**: zusätzlich CLIProxyAPI-eigenes logging aktivieren
und die logs in ein separates log-aggregation-system einspeisen.
Anantas audit + CLIProxyAPI's log ergeben zusammen den vollständigen
trace.

### 4. Remote-CLIProxyAPI

Wenn CLIProxyAPI auf einem remote-host läuft (nicht local-first),
gibt es zusätzliche risiken:

- Netzwerk-sniffing (auch wenn TLS genutzt wird, ist api-key im
  request-header)
- Server-seitige kompromittierung
- Account-credentials auf dem remote-host

**Mitigation**: **local-first empfehlung** — CLIProxyAPI lokal auf
localhost oder in einem privaten netz binden. API-key nur via TLS.
Wenn remote zwingend, dann via vpn/mesh und dediziertem
least-privilege account.

### 5. Provider-id-collision

`local_openai_backends` ist case-insensitive dedupliziert (siehe
`agent/local_llm_backends.py` Z. 106–114). Wenn ein zweiter eintrag
mit gleicher id (z.B. versehentlich `CliproxyAPI` statt
`cliproxyapi`) angelegt wird, wird er stillschweigend
dedupliziert.

**Mitigation**: ananta nutzt immer lower-case (`provider_id`).
Wenn ein nutzer versehentlich großbuchstaben benutzt, sollte eine
warnung im preflight erscheinen. (siehe cliproxyapi-007)

## Was Ananta *nicht* kontrolliert

> "Ananta kontrolliert nicht automatisch die internen Account- und
> OAuth-Flows von CLIProxyAPI."

Das ist explizit so. Anantas aufgabe ist die hub/worker/policy/audit-
schicht; CLIProxyAPI's aufgabe ist die CLI/Account-schicht. Wer
beide in einer komponente haben will, soll eine andere lösung wählen.

## Empfehlung

| Deployment                      | Empfehlung |
|---------------------------------|------------|
| CLIProxyAPI lokal (localhost)   | Standard, empfohlen |
| CLIProxyAPI in container neben Ananta | okay, private docker-network |
| CLIProxyAPI remote, vertrauenswürdiges VPN | möglich mit erhöhter observability |
| CLIProxyAPI öffentlich exponiert | **nicht empfohlen** — weder von Ananta noch von CLIProxyAPI selbst |
| CLIProxyAPI ohne TLS             | **nicht empfohlen** — api-key im klartext |
| Mehrere Accounts über eine CLIProxyAPI | okay, aber audit nur pro provider-id, nicht pro account |

## Checkliste vor produktivem einsatz

- [ ] CLIProxyAPI bindet nur auf localhost oder privatem interface
- [ ] API-key ist nicht im klartext in versionskontrolle
- [ ] CLIProxyAPI's secret-storage ist verschlüsselt
- [ ] CLIProxyAPI's eigenes logging ist aktiv
- [ ] Ananta's audit + CLIProxyAPI's log werden zusammengeführt
- [ ] `local_openai_backends`-eintrag nutzt `api_key_profile` oder
      env-variable für echte keys (nicht klartext `api_key`)
- [ ] `supports_tool_calls` reflektiert die tatsächliche
      CLIProxyAPI-konfiguration
- [ ] Preflight zeigt den eintrag korrekt an

## Siehe auch

* `docs/architecture/codecompass-import-trust.md` — ananta trust-modell
* `docs/architecture/cliproxyapi/ist-zustand.md` — code-mapping
* `docs/integrations/cliproxyapi.md#secrets` — secret-handling