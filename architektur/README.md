# Architekturplan für Ananta

Dieses Dokument beschreibt den vollständigen Architekturplan für das Ananta-System. Ananta ist ein modulares Multi-Agenten-System, das sich in mehrere grundlegende Komponenten gliedert. In diesem Dokument werden die primären Komponenten, ihr Zusammenspiel, die Kommunikation untereinander sowie die technischen Details und Erweiterungsmöglichkeiten erläutert.

---

## Inhaltsverzeichnis

1. [Systemübersicht](#system%C3%BCbersicht)
2. [Komponenten](#komponenten)
   - [Controller (Flask-Server)](#controller-flask-server)
   - [AI-Agent](#ai-agent)
   - [Frontend (Vue-Dashboard)](#frontend-vue-dashboard)
3. [Schnittstellen und HTTP-Endpunkte](#schnittstellen-und-http-endpunkte)
4. [Datenflüsse und Abläufe](#datenfl%C3%B6sse-und-abl%C3%A4ufe)
5. [Technologien und Frameworks](#technologien-und-frameworks)
6. [Erweiterbarkeit und Module](#erweiterbarkeit-und-module)
7. [UML-Diagramme und weitere Beschreibungen](#uml-diagramme-und-weitere-beschreibungen)

---

## Systemübersicht

Ananta basiert auf einem modularen Ansatz, um flexibel verschiedene Agentenrollen (wie Architekt, Backend Developer, Frontend Developer und weitere) in einem kooperativen Entwicklungsprozess einzubinden. Hauptziel ist die Automatisierung und Unterstützung bei der Anforderungsanalyse, der Codegenerierung sowie der Überprüfung und Absicherung der implementierten Funktionen.

---

## Komponenten

### Controller (Flask-Server)
- **Aufgaben:**
  - Verwaltung der Agenten-Konfiguration (z. B. `config.json`), Aufgabenliste, Blacklist sowie Log-Export.
  - Bereitstellung zahlreicher HTTP-Endpunkte, die als Schnittstelle für Agenten, das Dashboard und das gebaute Frontend (Vue) dienen.
- **Weitere Details:**
  - Verwendet Blueprint-Routen (siehe `/src/controller/routes.py`) für spezifische Operationen wie das Abrufen der nächsten Aufgabe oder die Blacklist-Verwaltung.
  - Unterstützt Endpunkte zum Steuern von Agenten (z. B. Aktivieren/Deaktivieren, Loganzeige).

### AI-Agent
- **Aufgaben:**
  - Periodisches Abfragen des Controllers (über `/next-config`) zur Ermittlung der Konfiguration und Aufgaben.
  - Erstellen und Rendern von Prompts über Vorlagen (PromptTemplates) zur Ansteuerung von LLMs.
  - Implementierung verschiedener LLM-Provider (Ollama, LM Studio, OpenAI) mit konfigurierbaren Endpunkten.
- **Wichtige Module:**
  - Nutzung der `ModelPool`-Klasse zur Limitierung paralleler Anfragen an LLM-Provider.
  - Logische Trennung der Agenten-Dateien zur protokollierten Ausführung der generierten Kommandos.

### Frontend (Vue-Dashboard)
- **Aufgaben:**
  - Darstellung von Logs, Konfigurations- und Steuerungsoptionen über eine moderne Browseroberfläche.
  - Kommunikation mit dem Flask-basierten Controller mittels HTTP-Fetch-Aufrufen.
- **Weitere Details:**
  - Implementierung in Vue 3, unterstützt interaktive Dashboards und Echtzeit-Feedback.
  - Getrennte Readme im `frontend/`-Ordner, welche die Nutzung und Erweiterbarkeit der UI beschreibt.

---

## Schnittstellen und HTTP-Endpunkte

Das System bietet eine Vielzahl von HTTP-Endpunkten, die zentral sowohl für die Kommunikation von Agenten als auch für das Frontend genutzt werden:

- **/next-config (GET):**
  Liefert die nächste Agenten-Konfiguration inkl. Aufgaben und Vorlagen.
- **/config (GET):**
  Rückgabe der vollständigen Controller-Konfiguration als JSON.
- **/approve (POST):**
  Validierung und Ausführung von Agentenvorschlägen.
- **/issues (GET):**
  Abfrage von GitHub-Issues zur Integration in den Aufgaben-Workflow.
- **/set_theme (POST):**
  Speicherung des Dashboard-Themes im Cookie.
- **/agent/<name>/toggle_active (POST):**
  Umschalten des Aktiv-Status eines spezifischen Agents.
- **/agent/<name>/log (GET):**
  Bereitstellung der Protokolldatei eines Agenten.
- **Weitere Endpunkte:**
  - `/stop`, `/restart` (Steuerung der Agenten-Läufe)
  - `/export` (Export der Logs und Konfigurationen)
  - `/ui` (Bereitstellung des gebauten Vue-Frontends)

---

## Datenflüsse und Abläufe

1. **Startup:**
   Der Controller initialisiert die Konfigurationsdateien (z. B. `config.json`) und lädt gegebenenfalls Team-spezifische Standardkonfigurationen.
2. **Agentenlauf:**
   - Der AI-Agent pollt periodisch den Controller über `/next-config`.
   - Basierend auf der erhaltenen Konfiguration wird ein Prompt konstruiert, der an einen dedizierten LLM-Provider gesendet wird.
   - Die erzeugte Antwort wird validiert und ggf. als Shell-Befehl ausgeführt. Die Ausführungsergebnisse werden in Log-Dateien dokumentiert.
3. **Dashboard-Betrieb:**
   - Das Vue-Dashboard stellt in Echtzeit die aktuellen Statuswerte, Logs und Steueroptionen zur Verfügung.
   - Die Interaktion erfolgt über definierte HTTP-Endpoint-Aufrufe und unterstützt so die Überwachung und direkte Steuerung der Abläufe.

---

## Technologien und Frameworks

- **Backend:**
  - Programmiersprache: Python (Version 3.13.5)
  - Framework: Flask (zur Erstellung des Controllers)
  - Packages: requests, pyyaml, werkzeug, etc.
- **Frontend:**
  - Framework: Vue 3 (Version 3.4.0)
  - Package-Manager: npm (für Node.js)
- **Infrastruktur:**
  - Unterstützung von Mehragenten-Systemen, die unterschiedliche Rollen (Architect, Backend Developer, etc.) übernehmen.
  - Nutzung von CPU- oder GPU-basierten LLMs, abhängig von der jeweiligen Agentenrolle und Ressourcenzuweisung.

---

## Erweiterbarkeit und Module

- **Modulare Architektur:**
  Die Struktur von Ananta erlaubt es, neue Agenten durch zusätzliche JSON-Konfigurationen und Prompt-Vorlagen einzubinden.
- **Modulare Komponenten:**
  - Die Verzeichnisstruktur (z. B. `src/agents`, `src/controller`, `src/models`) unterstützt eine klare Trennung der Verantwortlichkeiten.
  - Zusätzliche Module oder Services können bei Bedarf integriert werden.
- **Flexibilität:**
  Die Endpunkte für den AI-Agenten sind so gestaltet, dass sie einfach erweitert und an unterschiedliche LLM-Provider angepasst werden können. Ebenso können zusätzliche Validierungs- und Steuerungsebenen integriert werden.

---

## UML-Diagramme und weitere Beschreibungen

Für eine grafische Darstellung der Systemarchitektur wird empfohlen, ergänzend UML-Diagramme zu erstellen. Vorschläge:

- **Komponentendiagramm:**
  Visualisiert die Hauptmodule (Controller, AI-Agent, Frontend) und deren Interaktionen.
- **Sequenzdiagramm:**
  Zeigt den Ablauf von Konfigurationsabfragen, Prompt-Generierung, LLM-Kommunikation und Auftragserfüllung.
- **Klassendiagramm:**
  Beschreibt die Beziehungen innerhalb der Python-Module wie `ModelPool`, `PromptTemplates` und Agenten-spezifischen Klassen.

Diese Diagramme können in einem Unterordner, beispielsweise `architektur/uml/`, abgelegt werden. Für eine bessere Übersicht sollten jeweils auch kurze Beschreibungen zu den Diagrammen in diesem Readme aufgeführt werden.

---

## Zusammenfassung

Der Architekturplan von Ananta zeigt den modularen Aufbau eines Multi-Agenten-Systems, das eine enge Verzahnung von Backend-Logik, AI-Agenten und einem modernen Frontend-Dashboard vorsieht. Durch höhere Modularität und Erweiterbarkeit können neue Komponenten und Agentenrollen flexibel integriert werden, um auf sich ändernde Projektanforderungen zu reagieren.

---

*Weitere interne Details und spezifische Codeausschnitte finden Sie in den zugehörigen Modulen sowie in ergänzenden Dokumentationen in den jeweiligen Quellcodeverzeichnissen.*
