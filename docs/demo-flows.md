# Demo-Flows (reproduzierbar)

Diese Seite liefert reproduzierbare Demo-Flows fuer die offiziellen Kern-Use-Cases. Jeder Flow trennt Demo-Pfad und Realpfad, nennt UI-, CLI- und API-Einstieg und beschreibt ein sichtbares Erfolgssignal.

## Flow A: Repository verstehen (UC1)

Demo-Pfad:
1. Dashboard oeffnen.
2. `Demo ansehen` waehlen.
3. Beispiel `Repository verstehen` lesen oder als Goal starten.

Realpfad UI:
1. Dashboard -> `Ziel planen`.
2. Goal: `Analysiere dieses Repository und schlage die wichtigsten naechsten Schritte vor.`
3. Erfolgssignal: Plan steht bereit, Tasks wurden angelegt, naechste Schritte zeigen Zielpruefung und Aufgabenverfolgung.

CLI:

```bash
python -m agent.cli_goals analyze "Analysiere dieses Repository und schlage die wichtigsten naechsten Schritte vor"
```

API:

```http
POST /goals
{"goal":"Analysiere dieses Repository und schlage die wichtigsten naechsten Schritte vor.","create_tasks":true}
```

Erwartetes Ergebnis: Hotspots, Risiken, offene Fragen und ein kurzer Arbeitsplan.

## Flow B: Bugfix planen (UC2)

Demo-Pfad:
1. Dashboard -> `Demo ansehen`.
2. Beispiel `Bugfix vorbereiten` lesen oder als Goal starten.

Realpfad UI:
1. Dashboard -> Preset `Bugfix planen`.
2. Fehlerbild in einem Satz ergaenzen.
3. Erfolgssignal: Tasks fuer Reproduktion, Ursachenanalyse und Regressionstest sind sichtbar.

CLI:

```bash
python -m agent.cli_goals patch "Login bricht bei leerem Passwort ab; reproduziere und plane einen kleinen Fix"
```

API:

```http
POST /goals
{"goal":"Login bricht bei leerem Passwort ab; reproduziere und plane einen kleinen Fix.","mode":"code_fix","mode_data":{"shortcut":"patch"},"create_tasks":true}
```

Erwartetes Ergebnis: Reproduktionspfad, Ursache, kleine Korrektur und Regressionstest.

## Flow C: Start/Deploy diagnostizieren (UC3)

Demo-Pfad:
1. Dashboard -> `Demo ansehen`.
2. Beispiel `Lokalen Start reparieren` lesen oder als Goal starten.

Realpfad UI:
1. Dashboard -> `Diagnostizieren`.
2. Problemtext mit Log- oder Fehlermeldung eintragen.
3. Erfolgssignal: Diagnose-Tasks und naechste sichere Befehle sind sichtbar.

CLI:

```bash
python -m agent.cli_goals diagnose "Docker frontend cannot reach hub (connection refused)"
```

API:

```http
POST /goals
{"goal":"Docker frontend cannot reach hub (connection refused)","mode":"docker_compose_repair","mode_data":{"shortcut":"diagnose"},"create_tasks":true}
```

Erwartetes Ergebnis: Compose-/Port-/Health-Check-Reihenfolge und stabiler Startpfad.

## Flow D: Change Review (UC4)

Demo-Pfad:
1. Dashboard -> `Demo ansehen`.
2. Beispiel `Change Review` lesen oder als Goal starten.

Realpfad UI:
1. Dashboard -> `Reviewen`.
2. PR, Commit, Dateiliste oder Diff-Kontext angeben.
3. Erfolgssignal: Findings, Testbedarf und Governance-Hinweise sind priorisiert sichtbar.

CLI:

```bash
python -m agent.cli_goals review "Pruefe die Login-Aenderungen auf Risiken, fehlende Tests und Regressionen"
```

API:

```http
POST /goals
{"goal":"Pruefe die Login-Aenderungen auf Risiken, fehlende Tests und Regressionen","mode":"code_review","mode_data":{"shortcut":"review"},"create_tasks":true}
```

Erwartetes Ergebnis: priorisierte Review-Findings mit konkreten naechsten Checks.

## Flow E: Gefuehrte Goal-Erstellung (UC5)

Demo-Pfad:
1. Dashboard -> `Erster Lauf in drei Schritten`.
2. `Eigenes Ziel planen` waehlen.
3. Gefuehrten Ziel-Assistenten oeffnen.

Realpfad UI:
1. Dashboard -> `Assistent`.
2. Goal-Modus waehlen.
3. Ziel, Kontext, Ausfuehrungstiefe und Sicherheitsniveau ausfuellen.
4. Erfolgssignal: Goal ist geplant; Safety-/Review-Entscheidungen bleiben sichtbar.

CLI:

```bash
python -m agent.cli_goals --modes
python -m agent.cli_goals --goal "Container restart-loop" --mode docker_compose_repair --mode-data '{"service":"hub"}'
```

API:

```http
GET /goals/modes
POST /goals
{"goal":"Container restart-loop","mode":"docker_compose_repair","mode_data":{"service":"hub"},"create_tasks":true}
```

Erwartetes Ergebnis: ein parametrisiertes Goal mit nachvollziehbaren Planungs-, Safety- und Review-Signalen.

## Flow F: Neues Softwareprojekt anlegen (UC6)

Demo-Pfad:
1. Dashboard -> `Demo ansehen`.
2. Beispiel `Neues Projekt anlegen` lesen oder als Goal starten.

Realpfad UI:
1. Dashboard -> `Assistent`.
2. Modus `Neues Softwareprojekt anlegen` waehlen.
3. Projektidee, Zielgruppe, Plattform, bevorzugten Stack und Nicht-Ziele ausfuellen.
4. Erfolgssignal: Projekt-Blueprint, initiales Backlog und Review-Schritte sind sichtbar.

CLI:

```bash
python -m agent.cli_goals new-project "Baue ein kleines Tool fuer teaminterne Release-Checks"
```

API:

```http
POST /goals
{"mode":"new_software_project","mode_data":{"project_idea":"Baue ein kleines Tool fuer teaminterne Release-Checks","target_users":"Maintainer","platform":"Web","preferred_stack":"Python + Angular","non_goals":"Keine Vollautomatik ohne Review"},"create_tasks":true}
```

Erwartetes Ergebnis: Scope, Architekturvorschlag, erste Artefakte und kleine Initial-Tasks.

Review-Check:
- Goal-Detail zeigt geplante Artefakte wie Zielzusammenfassung, Projekt-Blueprint, initiales Backlog und naechste Schritte.
- Tasks bleiben klein und sequenziert; der Hub besitzt Planung und Queue, Worker fuehren nur delegierte Schritte aus.
- Governance-Hinweise zeigen Review-Bedarf und sichere Defaults, bevor daraus Umsetzung wird.

Reviewer-Schnellpruefung:
1. Demo- oder CLI-Flow starten.
2. Goal-Detail oeffnen und `planned_artifacts` pruefen.
3. Board oeffnen und kontrollieren, dass die Initial-Tasks nicht als monolithische "mach alles"-Aufgabe erzeugt wurden.

## Flow G: Existierendes Softwareprojekt weiterentwickeln (UC7)

Demo-Pfad:
1. Dashboard -> `Demo ansehen`.
2. Beispiel `Projekt weiterentwickeln` lesen oder als Goal starten.

Realpfad UI:
1. Dashboard -> `Assistent`.
2. Modus `Existierendes Projekt weiterentwickeln` waehlen.
3. Zielaenderung, betroffene Bereiche, Restriktionen, Risikoniveau und Weiterentwicklungsart angeben.
4. Erfolgssignal: Aenderungsplan, Risiko-/Testsicht und naechste reviewbare Tasks sind sichtbar.

CLI:

```bash
python -m agent.cli_goals evolve-project "Erweitere den Dashboard-Flow um einen Projektstartmodus"
```

API:

```http
POST /goals
{"mode":"project_evolution","mode_data":{"change_goal":"Erweitere den Dashboard-Flow um einen Projektstartmodus","change_type":"feature_ausbau","affected_areas":"frontend-angular, agent/services","risk_level":"mittel","constraints":"Keine Worker-zu-Worker-Orchestrierung"},"create_tasks":true}
```

Erwartetes Ergebnis: kleine Aenderungsschritte mit betroffenen Bereichen, Risiken, Testbedarf und Review-Plan.

Review-Check:
- Goal-Detail zeigt Ist-Analyse, Aenderungsplan, Risiko-/Test-/Review-Plan und naechste Schritte.
- Tasks nennen betroffene Bereiche, Risiken und Pruefhinweise.
- Der Flow fuehrt zu aktiver Weiterentwicklung und bleibt klar von reinem Repository-Verstehen getrennt.

Reviewer-Schnellpruefung:
1. Demo- oder CLI-Flow starten.
2. Goal-Detail oeffnen und geplante Artefakte fuer Aenderungsplan und Risiko-/Testsicht pruefen.
3. Board oeffnen und kontrollieren, dass zuerst kleine, reviewbare Aenderungsschritte entstehen.
