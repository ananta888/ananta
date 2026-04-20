# Offizielle Kern-Use-Cases (Ananta)

Diese Seite definiert die offiziell priorisierten Kern-Use-Cases fuer Ananta. Sie dient als gemeinsame Produktbasis fuer:

- Schnellstart und First-Run
- UI- und CLI-Golden-Paths
- Demo-Presets und reproduzierbare Demo-Flows
- Benchmarking-Aufgaben
- Produktprofile (z.B. Demo vs. Team-Controlled)

Die Use-Cases sind bewusst klein und klar geschnitten, damit Ananta nicht "alles gleichzeitig" sein muss.

Reproduzierbare Demo-Flows: `docs/demo-flows.md`.

## UC1: Repository verstehen (Goal -> Plan -> Tasks -> Ergebnis)

**Fuer wen:** neue Maintainer, Reviewer, technische Stakeholder.

**Einstieg:** Web UI Dashboard -> "Planen" (Quick Goal) oder Preset "Repository verstehen".

**Erwartetes Ergebnis:** ein kurzer, nachvollziehbarer Bericht mit Hotspots, Risiken und naechsten Schritten.

**Scope:** Lesen/Analysieren, keine automatischen Codeaenderungen ohne explizite Freigabe.

## UC2: Bugfix von Bericht zu kleinem Fix (planbar und testbar)

**Fuer wen:** Entwickler, QA, Maintainer.

**Einstieg:** Web UI Dashboard -> "Planen" (Goal: Bug beschreiben) oder Preset "Bugfix planen".

**Erwartetes Ergebnis:** Reproduktionspfad, Ursache, Fix-Vorschlag, Regressionstest-Plan.

**Scope:** kleine, testbare Aenderung; keine grossen Refactors.

## UC3: Start-/Deploy-Diagnose (Compose, Health, Logs)

**Fuer wen:** Betreiber, Entwickler, Reviewer.

**Einstieg:** Dashboard -> "Diagnostizieren" (Shortcut) oder Goal mit konkreter Fehlermeldung.

**Erwartetes Ergebnis:** klare Diagnosekette (was pruefen, wo schauen, naechster Befehl) und ein stabiler Startpfad.

**Scope:** Operative Klarheit und Reproduzierbarkeit, kein "Trial-and-Error" ohne Audit-Spur.

## UC4: Change Review (Risiken, Tests, Governance)

**Fuer wen:** Reviewer, Team-Admins.

**Einstieg:** Dashboard -> "Reviewen" (Shortcut) oder Goal mit PR/Commit/Dateiliste.

**Erwartetes Ergebnis:** Risikoanalyse, benoetigte Tests, ggf. Governance- oder Policy-Checks als nachvollziehbare Punkte.

**Scope:** bewertend und verifizierend, nicht automatisch mergeend.

## UC5: Gefuehrte Goal-Erstellung (Wizard) fuer Erstnutzer

**Fuer wen:** neue Nutzer, nicht tief technisch.

**Einstieg:** Dashboard -> Guided Goal Wizard.

**Erwartetes Ergebnis:** ein sinnvoll parametrisiertes Goal (inkl. Safety/Review Einstellungen), das zu einem sichtbaren Zwischen- oder Endergebnis fuehrt.

**Scope:** "One obvious way in" fuer den First Run.
