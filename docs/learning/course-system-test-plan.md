# Course System Test Plan

## Ziel

Deterministische Tests fuer Kursvoraussetzungen, Freischaltungen, Sandbox-Grenzen und Auditierbarkeit.

## 1) Voraussetzungen und Unlocks

- deny, wenn `prerequisites` nicht erfuellt sind
- allow, wenn notwendige Lessons/Assessments erfolgreich abgeschlossen sind
- Unlock-Regeln werden nur bei passenden Conditions angewendet

## 2) Grant-Eskalationsschutz

- deny fuer Grant-Eskalation ohne bestandenen Check
- deny fuer implizites `remote_llm_allowed` aus `use_worker`
- deny fuer abgelaufene oder widerrufene Grants

## 3) Sandbox- und Artefaktzugriff

- Uebungen bekommen nur explizit freigegebene Artefakte
- Tool-/Shell-Rechte folgen dem Risikoprofil der Uebung
- Zugriff auf verbotene Datenquellen fuehrt zu deterministischem deny

## 4) Progress und Mentor-Override

- Progress-Statuswechsel sind auditierbar
- Mentor/Admin-Override erzeugt eigenen Audit-Event mit Reason
- Overrides koennen nur definierte Unlocks ausloesen

## 5) Nicht-Funktionsgrenzen

- Keine echten Secrets
- Keine echten Cloud-LLMs erforderlich
- Keine Production-Daten
- Reproduzierbare Fixtures statt externer Zufallsabhaengigkeit

## 6) CoursePreview Read-only

- Tests pruefen read-only CoursePreview analog zu `tests/test_demo_mode.py`.
- CoursePreview darf keine produktiven Goals/Tasks veraendern.
- CoursePreview bleibt isoliert und ohne Seiteneffekte auf produktiven Zustand.
