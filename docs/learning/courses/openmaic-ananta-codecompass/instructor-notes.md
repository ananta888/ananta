# Dozentenhinweise

## Rahmen

- Zielgruppe: Entwicklung, technische Produktverantwortung sowie Security und Plattformbetrieb.
- Dauer: 60 Minuten; bei 45 Minuten die optionale Vertiefung zu Pair-Dev und AI-Snake auslassen.
- Vorwissen: Git-Repositories, APIs und automatisierte Tests auf Grundlageniveau.
- Betrieb: OpenMAIC ist eine getrennte Lernoberfläche. Der Kurs verbindet sich nicht mit Hub, Worker, Datenbank oder produktiven CodeCompass-Diensten.

## Zeitplan und erwartete Antworten

| Minute | Abschnitt | Erwartete Kernaussage |
|---:|---|---|
| 0–8 | Kontextproblem | Eine plausible Antwort ist keine revisionsgebundene Evidenz. |
| 8–20 | Control Plane | Nur der Hub besitzt Queue, Delegation, Policy, Budget und Audit. |
| 20–35 | Evidenzreise | Graph, Symbol, Volltext und Vektor ergänzen sich und bleiben begrenzt. |
| 35–50 | Planspiel | Das Modell schlägt vor; Policy und Hub-Autorität erlauben oder blockieren. |
| 50–60 | Quiz/Artefakt | Teilnehmende können Ananta, CodeCompass und normales RAG abgrenzen. |

## Typische Missverständnisse

- „Der Hub ist der Scrum-Master“ ist falsch: Der Hub ist eine technische Control Plane, keine menschliche Scrum-Rolle.
- „GraphRAG verhindert Halluzinationen“ ist falsch: Es liefert strukturierte Hinweise, garantiert aber keine fehlerfreie Antwort.
- „Mehr Kontext ist immer besser“ ist falsch: Range-, Evidence- und Tokenbudgets begrenzen unnötige oder nicht freigegebene Inhalte.
- „Ein Worker kann bei Bedarf selbst delegieren“ ist falsch: Folgetasks bleiben Hub-Entscheidungen.
- „Ein LLM darf Änderungen ausführen, weil es sie begründen kann“ ist falsch: Begründung ist keine Autorität.

## Automatisierter Ablauf- und Sicherheitscheck

Vor jeder Vorführung aus dem Repository-Root ausführen:

```bash
python scripts/build_openmaic_course.py --check
python scripts/validate_openmaic_course.py
python scripts/run_openmaic_course_demo.py
```

Beide Befehle laufen ohne Netzwerk, Modell, produktive Credentials oder menschliche Freigabe. Der Test simuliert richtige und typische falsche Quizentscheidungen automatisch.

## Fallback

1. Wenn OpenMAIC nicht erreichbar ist, `offline/index.html` lokal im Browser öffnen.
2. `python scripts/run_openmaic_course_demo.py` ausführen; eine explizit über `--snapshot` bereitgestellte lokale Datei wird streng validiert, andernfalls wird deterministisch der versionierte Snapshot verwendet.
3. Stets den sichtbaren Hinweis „OFFLINE SNAPSHOT — unverified_missing_SRC_ids“ stehen lassen.
4. Keine spontane Live-Abfrage gegen ein produktives Ananta-System starten.

## Abschlussartefakt

Die lernende Person erstellt eine kurze Skizze mit fünf Stationen: Trigger, Hub, Task Queue, Worker und Verifikation/Artefakt. Zu einer technischen Behauptung werden Repository-Revision, Pfad und Evidenzstatus ergänzt. Im automatisierten Akzeptanztest wird dieselbe Struktur als deterministischer Datensatz validiert.
