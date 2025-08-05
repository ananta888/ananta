# Dashboard-Architektur und API-Übersicht

Dieses Dokument fasst die Gesamtarchitektur des Ananta-Dashboards zusammen und listet die wichtigsten HTTP-Endpunkte auf. Die Plattform besteht aus drei Hauptkomponenten:

- **Controller** – Flask-Server, der Konfigurationen verwaltet und Endpunkte bereitstellt.
- **AI-Agent** – Python-Skript, das Aufgaben pollt, Prompts generiert und Kommandos ausführt.
- **Vue-Dashboard** – Browseroberfläche zur Anzeige von Logs und Steuerung der Agenten.

## Architektur

1. Der AI-Agent fragt den Controller periodisch über `/next-config` nach neuer Konfiguration.
2. Basierend auf dieser Konfiguration erstellt der Agent Prompts und sendet Ergebnisse über `/approve` zurück.
3. Das Vue-Dashboard ruft Controller-Endpunkte wie `/config` oder `/agent/<name>/log` auf, um Statusinformationen anzuzeigen.
4. Der Controller stellt nach `npm run build` das gebaute Dashboard unter `/ui` bereit.

## Wichtige API-Endpunkte

| Endpoint | Methode | Beschreibung |
| -------- | ------- | ------------ |
| `/next-config` | GET | Liefert die nächste Agenten-Konfiguration inkl. Aufgaben & Templates. |
| `/config` | GET | Gibt die vollständige Controller-Konfiguration als JSON zurück. |
| `/approve` | POST | Validiert und führt Agenten-Vorschläge aus. |
| `/issues` | GET | Holt GitHub-Issues und reiht Aufgaben ein. |
| `/set_theme` | POST | Speichert das Dashboard-Theme im Cookie. |
| `/agent/<name>/toggle_active` | POST | Schaltet `controller_active` eines Agents um. |
| `/agent/<name>/log` | GET | Liefert die Logdatei eines Agents. |
| `/stop`, `/restart` | POST | Legt `stop.flag` an bzw. entfernt ihn. |
| `/export` | GET | Exportiert Logs und Konfigurationen als ZIP. |
| `/ui`, `/ui/<pfad>` | GET | Serviert das gebaute Vue-Frontend. |

## Entwicklungsbefehle

```bash
npm run dev    # Entwicklungsserver starten
npm run build  # Produktions-Bundle erstellen
```

Nach dem Build werden die Dateien in `dist/` erzeugt und vom Controller unter `/ui` ausgeliefert.
