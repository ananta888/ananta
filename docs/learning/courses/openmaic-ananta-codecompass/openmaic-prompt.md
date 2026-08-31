# OpenMAIC-Workbench-Prompt

Erzeuge einen vollständig deutschsprachigen, 60-minütigen Kurs aus den beigefügten Dateien `openmaic-content.json`, `source-audit.json` und `demo/codecompass-snapshot.json`.

Verbindliche Regeln:

1. Stelle Ananta als technische Hub–Worker-Control-Plane dar. Der Hub besitzt Task Queue, Delegation, Policy, Budgets, Audit und Workflow-Entscheidungen. Worker führen nur delegierte Arbeit aus und orchestrieren keine Worker.
2. Stelle OpenMAIC ausschließlich als getrennte Lern- und Demonstrationsoberfläche dar. Es ist weder Hub noch Worker und erhält keinen Zugriff auf Ananta.
3. Zeige den Weg Kontextproblem → Control Plane → CodeCompass-Projektgedächtnis → belegte Anfrage → delegierte Änderung → automatischer Test/Audit.
4. Trenne Graph-, Symbol-, Volltext- und Vektorhinweise. Behaupte niemals, GraphRAG verhindere Halluzinationen vollständig.
5. Zeige bei jeder technischen Evidenz Repository-Revision, Pfad und den Status `unverified_missing_SRC_ids`. Erzeuge keine `SRC_*`- oder `RUN_*`-Kennung.
6. Verwende die zwei vorgegebenen Interaktionen unverändert in ihrer fachlichen Aussage. Ein falscher Pfad muss erklären, warum direkte Worker-Delegation die Hub-Grenze verletzt.
7. Nutze keine Websuche, produktiven Systeme, API-Schlüssel, privaten URLs, lokalen Benutzerpfade, personenbezogenen Daten oder schreibenden Tools.
8. Pair-Dev, AI-Snake und Quellgraph dürfen als optionale Beispiele erscheinen, sind aber keine Voraussetzung.
9. Der Abschluss enthält das definierte Quiz und ein Artefakt mit Trigger → Hub → Task Queue → Worker → Verifikation.

Das bereits versionierte `.maic.zip`-Archiv ist die freigegebene Referenzausgabe. Eine neue KI-generierte Variante darf es erst ersetzen, wenn die automatischen Preflight- und Kursakzeptanztests vollständig grün sind.
