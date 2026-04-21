# Offizielle Kern-Use-Cases (Ananta)

Diese Seite definiert die offiziell priorisierten Kern-Use-Cases fuer Ananta. Sie dient als gemeinsame Produktbasis fuer:

- Schnellstart und First-Run
- UI- und CLI-Golden-Paths
- Demo-Presets und reproduzierbare Demo-Flows
- Benchmarking-Aufgaben
- Produktprofile (z.B. Demo vs. Team-Controlled)

Die Use-Cases sind bewusst klein und klar geschnitten, damit Ananta nicht "alles gleichzeitig" sein muss. UC6 und UC7 erweitern diese Basis um die zwei primaeren Softwarepfade: ein neues Projekt starten und ein bestehendes Projekt kontrolliert weiterentwickeln.

Reproduzierbare Demo-Flows: `docs/demo-flows.md`.

## UC1: Repository verstehen (Goal -> Plan -> Tasks -> Ergebnis)

**Fuer wen:** neue Maintainer, Reviewer, technische Stakeholder.

**Einstieg:** Web UI Dashboard -> "Planen" (Quick Goal) oder Preset "Repository verstehen".

**Guter Input:** `Analysiere dieses Repository und schlage die wichtigsten naechsten Schritte vor.`

**Erwartetes Ergebnis:** ein kurzer, nachvollziehbarer Bericht mit Hotspots, Risiken und naechsten Schritten.

**Naechster Schritt:** Goal-Detail oeffnen, Hotspots pruefen, danach konkrete Tasks starten.

**Scope:** Lesen/Analysieren, keine automatischen Codeaenderungen ohne explizite Freigabe.

## UC2: Bugfix von Bericht zu kleinem Fix (planbar und testbar)

**Fuer wen:** Entwickler, QA, Maintainer.

**Einstieg:** Web UI Dashboard -> "Planen" (Goal: Bug beschreiben) oder Preset "Bugfix planen".

**Guter Input:** `Login bricht bei leerem Passwort ab; reproduziere den Fehler und plane einen kleinen Fix.`

**Erwartetes Ergebnis:** Reproduktionspfad, Ursache, Fix-Vorschlag, Regressionstest-Plan.

**Naechster Schritt:** Reproduktion bestaetigen, Regressionstest priorisieren, dann kleine Korrektur planen.

**Scope:** kleine, testbare Aenderung; keine grossen Refactors.

## UC3: Start-/Deploy-Diagnose (Compose, Health, Logs)

**Fuer wen:** Betreiber, Entwickler, Reviewer.

**Einstieg:** Dashboard -> "Diagnostizieren" (Shortcut) oder Goal mit konkreter Fehlermeldung.

**Guter Input:** `Frontend erreicht den Hub nicht; pruefe Compose, Ports und Health-Checks.`

**Erwartetes Ergebnis:** klare Diagnosekette (was pruefen, wo schauen, naechster Befehl) und ein stabiler Startpfad.

**Naechster Schritt:** vorgeschlagene Diagnosebefehle ausfuehren und blockierte Checks sichtbar halten.

**Scope:** Operative Klarheit und Reproduzierbarkeit, kein "Trial-and-Error" ohne Audit-Spur.

## UC4: Change Review (Risiken, Tests, Governance)

**Fuer wen:** Reviewer, Team-Admins.

**Einstieg:** Dashboard -> "Reviewen" (Shortcut) oder Goal mit PR/Commit/Dateiliste.

**Guter Input:** `Pruefe diese Aenderung auf Risiken, fehlende Tests und moegliche Regressionen.`

**Erwartetes Ergebnis:** Risikoanalyse, benoetigte Tests, ggf. Governance- oder Policy-Checks als nachvollziehbare Punkte.

**Naechster Schritt:** Findings nach Schweregrad abarbeiten und fehlende Tests nachziehen.

**Scope:** bewertend und verifizierend, nicht automatisch mergeend.

## UC5: Gefuehrte Goal-Erstellung (Wizard) fuer Erstnutzer

**Fuer wen:** neue Nutzer, nicht tief technisch.

**Einstieg:** Dashboard -> Guided Goal Wizard.

**Guter Input:** Ziel, Kontext, gewuenschte Ausfuehrungstiefe und Sicherheitsniveau im Wizard ausfuellen.

**Erwartetes Ergebnis:** ein sinnvoll parametrisiertes Goal (inkl. Safety/Review Einstellungen), das zu einem sichtbaren Zwischen- oder Endergebnis fuehrt.

**Naechster Schritt:** erzeugtes Goal pruefen, danach Aufgaben oder Artefakte oeffnen.

**Scope:** "One obvious way in" fuer den First Run.

## UC6: Neues Softwareprojekt anlegen

**Fuer wen:** Erstnutzer, Gruender, Teams vor dem ersten Repository oder Maintainer, die aus einer Idee ein neues Projekt strukturieren wollen.

**Einstieg:** Dashboard -> Guided Goal Wizard -> "Neues Softwareprojekt anlegen", Quick-Goal-Preset "Neues Projekt anlegen" oder CLI-Shortcut `new-project`.

**Guter Input:** Projektidee, Zielgruppe, Plattform, bevorzugter Stack, Sicherheitsniveau, Ausfuehrungstiefe und klare Nicht-Ziele.

**Erwartetes Ergebnis:** ein parametrisiertes Goal mit Scope, Architekturvorschlag, initialem Backlog, sichtbaren Review-Schritten und naechsten umsetzbaren Tasks.

**Naechster Schritt:** erzeugtes Goal pruefen, Blueprint und Initial-Tasks reviewen, danach kleine Startaufgaben priorisieren.

**Scope:** Projektstrukturierung und planbarer Start; keine unkontrollierte Vollautomatik und keine Schreibpfade ohne sichtbare Governance.

## UC7: Existierendes Softwareprojekt weiterentwickeln

**Fuer wen:** Entwickler, Maintainer und Teams, die ein bestehendes Repository mit kleinen, pruefbaren Aenderungen weiterentwickeln wollen.

**Einstieg:** Dashboard -> Guided Goal Wizard -> "Existierendes Projekt weiterentwickeln", Quick-Goal-Preset "Projekt weiterentwickeln" oder CLI-Shortcut `evolve-project`.

**Guter Input:** gewuenschte Zielaenderung, betroffene Bereiche, Restriktionen, Risikoniveau und Art der Weiterentwicklung.

**Erwartetes Ergebnis:** ein strukturierter Aenderungsplan mit Ist-Kontext, Risiken, betroffenen Tests, Review-Punkten und kleinen Folge-Tasks.

**Naechster Schritt:** betroffene Bereiche und Risiken pruefen, dann die kleinste verifizierbare Aenderung starten.

**Scope:** aktive Weiterentwicklung statt reiner Repository-Beschreibung; grosse oder riskante Aenderungen werden in reviewbare Schritte zerlegt.
