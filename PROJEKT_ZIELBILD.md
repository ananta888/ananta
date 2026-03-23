# Ananta: Zielbild und wesentliche Erkenntnisse

## 1. Projektziel
Ananta ist als modulares Multi-Agent-System fuer AI-gestuetzte Softwareentwicklung aufgebaut.  
Das Kernziel ist, aus fachlichen Zielen (Goals) reproduzierbar umsetzbare Arbeit zu machen: planen, orchestrieren, ausfuehren, beobachten.

Kurz gesagt:
- **Vom Goal zur Umsetzung** (Auto-Planner, Task-Orchestrierung, Team-/Rollenmodell)
- **Transparente Steuerung** (Dashboard, Operations, Logs, Audit)
- **Sicherer Betrieb** (Auth, Rollen, MFA, nachvollziehbare Aktionen)
- **Erweiterbarkeit** (Hub/Worker-Architektur, Templates, Trigger/Webhooks)

## 2. Produktlogik in einem Satz
Der Hub organisiert Arbeit, Worker fuehren aus, das Frontend macht Zustand und Eingriffe fuer Menschen steuerbar.

## 3. Architektur (essentiell)
- **Frontend (Angular)**: UI fuer Dashboard, Agenten, Teams, Templates, Settings, Webhooks, Auto-Planner.
- **Hub-Agent**: zentrale API, Task- und Team-Orchestrierung, Trigger/Webhooks, Konfiguration.
- **Worker-Agenten**: Ausfuehrung von Aufgaben/Commands mit LLM-Unterstuetzung.
- **Datenhaltung**: primär PostgreSQL/Redis im Compose-Betrieb.
- **Betrieb**: Compose-Layer (`docker-compose.base.yml` + `docker-compose-lite.yml`) fuer lokalen Standard.

## 4. Worin der eigentliche Wert steckt
- **Automatisierungsgrad**: nicht nur Chat, sondern planbare und delegierbare Arbeitsschritte.
- **Kontrollierbarkeit**: menschliches Oversight ueber Tabs/Controls statt Blackbox-Autonomie.
- **Teamfokus**: Rollen, Teamtypen, Templates und Autopilot als Organisationsmodell.
- **Betriebsfaehigkeit**: Healthchecks, Logs, E2E-Abdeckung, Dokumentation fuer Reproduzierbarkeit.

## 5. Aktueller Stand (wesentlich)
- Compose-Stack ist stabil startbar und gesund.
- Kerntests fuer Auto-Planner/LLM-Config laufen nach Stabilisierung.
- UI wurde in mehreren Bereichen testbarer gemacht (`data-testid`, robustere Zustandsbehandlung).
- Lokale OpenAI-kompatible Backends koennen jetzt neben LM Studio zentral angebunden und im Provider-Katalog sichtbar gemacht werden.
- SGPT- und Task-Execution laufen jetzt mit expliziten Pipeline-Stages und `trace_id`-Metadaten.
- Der globale Assistant-Dock ist in praesentationsnahe Teilkomponenten plus Storage-Service zerlegt.
- Es gibt weiterhin einzelne E2E-Flakes ausserhalb des Kernziels (z. B. Cleanup-/Netzwerkpfade in Auth-Tests).

## 6. Hauptrisiken
- **E2E-Flakes durch Umweltkopplung**: laufende Services, Timeouts, Datenzustand, Port/Host-Mismatch.
- **UI-Textaenderungen vs. Test-Locators**: fragile textbasierte Selektoren brechen schnell.
- **Betriebsdetails am Host**: Redis/Kernel-Settings (`vm.overcommit_memory`) koennen lokal stoeren.

## 7. Strategische Leitlinien (empfohlen)
- **Stabile Testoberflaeche**: konsequent `data-testid` fuer kritische Flows.
- **Compose als Standardpfad fuer E2E**: einheitliche Ports/Env, kompakte Reporter.
- **Explizite Trennung von UI-Statusflags**: z. B. `busy` nicht fuer alle Teilbereiche gemeinsam.
- **Fehler transparent machen**: UI sollte bei API-Fehlern immer sichtbaren Ergebniszustand haben.
- **Doku als Betriebsvertrag**: Setup, Known Issues, Recovery-Schritte klar und kurz halten.

## 8. Naechste sinnvolle Prioritaeten
1. Vollstaendige Entflake-Runde fuer verbleibende E2E-Ausreisser (v. a. Auth/Cleanup-Pfade).
2. Konsolidierte CI-Route: `test:e2e:compose` als Haupt-Job mit Artefakt-Summary.
3. Settings-UI fuer neue lokale OpenAI-Backends und Runtime-Profile nachziehen.
4. Child-Komponenten des Assistant-Docks separat absichern (Unit/E2E).

## 9. Fazit
Ananta ist kein reines UI-Projekt und kein reiner Agenten-Prototyp, sondern eine **Steuerungsplattform fuer agentische Entwicklungsarbeit**.  
Der Hebel liegt darin, Automatisierung, Transparenz und Teamprozess in ein robust testbares System zu bringen.
