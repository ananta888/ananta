# OpenMAIC-Kurs: Ananta und CodeCompass

Dieses Verzeichnis ist ein statisches, revisionsfestes Kursbundle. Es koppelt OpenMAIC nicht an Ananta. Das importierbare `openmaic-ananta-codecompass.maic.zip` basiert auf dem offiziellen OpenMAIC-Classroom-Format v1 und auf OpenMAIC v1.0.0. `offline/index.html` ist der netzfreie Fallback.

## Reproduzieren und prüfen

```bash
python scripts/build_openmaic_course.py
python scripts/build_openmaic_course.py --check
python scripts/validate_openmaic_course.py
python scripts/run_openmaic_course_demo.py
```

Der Builder erzeugt Archiv und HTML deterministisch aus `openmaic-content.json`. Der Validator prüft Quellrevision/Pfade, Quiz, Sicherheitsgrenzen, Secret- und PII-Muster, private URLs, lokale Benutzerpfade sowie die Importstruktur. Es gibt keine manuelle Testfreigabe.

## In OpenMAIC verwenden

1. Die getrennte Demo-Instanz nach `deploy/examples/openmaic-course/README.md` starten.
2. In OpenMAIC das versionierte `.maic.zip`-Archiv importieren.
3. Falls die Instanz nicht verfügbar ist, `offline/index.html` öffnen.

Provider und Schlüssel stehen ausschließlich in der unversionierten `.env.local` der OpenMAIC-Demo. Das Kursarchiv benötigt zur Wiedergabe keinen Provider. Die gespeicherten CodeCompass-Antworten sind sichtbar als Offline-Snapshot und mangels bereitgestellter `SRC_*`/`RUN_*`-IDs als unverified markiert.

## Aktualisierung

Bei Architekturänderungen wird zuerst `source-audit.json` auf eine tatsächlich vorhandene Ananta-Commit-Revision aktualisiert. Danach werden Kursinhalt und Demo-Snapshot angepasst, die Artefakte neu gebaut und alle automatischen Prüfungen ausgeführt. Erst dann darf die Kursversion erhöht werden.
