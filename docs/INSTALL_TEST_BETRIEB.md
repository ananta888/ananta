# Installations-, Test- und Betriebsanleitung

Dieses Dokument beschreibt die Schritte zur Installation, zum Testen und zum Betrieb des Ananta Multi-Agent-Systems.

## 1. Installation

### Voraussetzungen
- **Docker & Docker Compose**: Empfohlen für den schnellsten Start (inkl. Postgres).
- **Postgres 15**: Wird für die zentrale Datenspeicherung im Hub-Modus verwendet.
- **Python 3.11+**: Für die manuelle Installation des Agents.
- **Node.js 18+ & npm**: Für die manuelle Installation des Frontends.

### A. Schnellstart mit Docker (Empfohlen)
1. Repository klonen oder Dateien kopieren.
2. Kopieren Sie die `.env.example` nach `.env` und passen Sie diese an (insbesondere für Windows/Git Bash).
3. Im Hauptverzeichnis ausführen:
   ```bash
   docker-compose up -d
   ```

3. Das System ist nun unter folgenden Adressen erreichbar:
   - Frontend: `http://localhost:4200`
   - Hub-Agent: `http://localhost:5000`
   - Worker-Agenten: `http://localhost:5001`, `http://localhost:5002`

4. **Initialer Login:**
   - **Benutzer:** `admin`
   - **Passwort:** `admin`
   - *Wichtig: Ändern Sie das Passwort sofort nach der Anmeldung über die Dashboard-Einstellungen.*

### B. Manuelle Installation (Entwicklung)

#### AI-Agent (Hub oder Worker)
1. In das Verzeichnis `agent/` wechseln.
2. Abhängigkeiten installieren: `pip install -r ../requirements.txt`.
3. Starten:
   ```bash
   # Als Hub (Standard-Port 5000)
   ROLE=hub python -m agent.ai_agent
   
   # Als Worker (z.B. Port 5001)
   PORT=5001 python -m agent.ai_agent
   ```

#### Frontend
1. In das Verzeichnis `frontend-angular/` wechseln.
2. Abhängigkeiten installieren: `npm install`.
3. Starten: `npm start`.
4. Browser öffnen: `http://localhost:4200`.

---

## 2. Testbetrieb

Das System verfügt über automatisierte Tests auf verschiedenen Ebenen.

### Unit- & Integrationstests (Backend)
Die Python-Tests decken die API-Endpunkte, Task-Management und Shell-Validierung ab.
```bash
python -m unittest discover tests
```

### E2E-Tests (Frontend)
Die Playwright-Tests prüfen den gesamten Flow im Dashboard.
```bash
cd frontend-angular
npm run test:e2e
```

### Smoke-Test (Gesamtsystem)
Ein dediziertes Skript prüft, ob der Agent bereit ist und Befehle ausführen kann.
```bash
# Erfordert laufendes System (z.B. via Docker)
python docker/smoke-test.py
```

---

## 3. Betrieb

### Überwachung (Monitoring)
- **Health-Check**: Jeder Agent bietet einen `/health` Endpunkt.
- **Ready-Check**: `/ready` gibt Aufschluss über die Betriebsbereitschaft.
- **Logs**: Terminal-Ausgaben werden in `data/terminal_log.jsonl` persistiert und können über das Frontend oder die API (`/logs`) eingesehen werden.

### Datensicherung (Backup)
Alle relevanten Daten liegen im Verzeichnis `data/`. Zur Sicherung genügt ein Backup dieses Ordners:
- SQLModel-Datenbank (Postgres/SQLite) f?r Tasks/Templates/Teams/Rollen.
- `config.json`: Agent-Konfigurationen.

### Skalierung
Weitere Worker-Agenten können einfach hinzugefügt werden, indem neue Instanzen des Agents auf anderen Ports gestartet und im Hub/Frontend registriert werden.

### Sicherheit
- **Port-Binding**: Standardmäßig sind die Ports in der `docker-compose.yml` offen (`"4200:4200"`), damit sie sowohl über `localhost` als auch über die Netzwerk-IP erreichbar sind. Falls Sie den Zugriff auf Ihren lokalen Rechner beschränken möchten, können Sie die Bindung auf `127.0.0.1` ändern (z.B. `"127.0.0.1:4200:4200"`). Beachten Sie jedoch, dass manche Browser dann Probleme mit der Auflösung von `localhost` (IPv6 vs IPv4) haben könnten.
- **Tokens**: Nutzen Sie die Umgebungsvariable `AGENT_TOKEN`, um schreibende Zugriffe abzusichern.
- **Shell-Validierung**: Der Agent verfügt über eine Blacklist für gefährliche Befehle (konfigurierbar in `blacklist.txt`).

---

## 4. Troubleshooting

### Docker Netzwerk-Fehler (`network is unreachable` / DNS / pip install)
Falls beim Starten von Docker Compose Fehler auftreten (z.B. beim Herunterladen von Images oder während `pip install` / `npm install` im Container):

#### Symptome:
- `Error response from daemon: Get "https://registry-1.docker.io/v2/": dial tcp ... connect: network is unreachable`
- `WARNING: Retrying ... Failed to establish a new connection: [Errno 101] Network is unreachable` während `pip install`.

#### Warum passiert das?
Docker Desktop unter Windows (WSL2) hat oft Probleme mit der DNS-Auflösung oder versucht fälschlicherweise IPv6 zu nutzen, wenn das Netzwerk dies nicht unterstützt. Dies betrifft sowohl den Host (beim Pull) als auch die laufenden Container (beim Installieren von Paketen).

#### Lösungsschritte:
1. **Automatischer Fix**: Führen Sie das bereitgestellte PowerShell-Skript aus, um die Verbindung zu testen und detaillierte Diagnosen (IP, DNS, MTU) zu erhalten:
   ```powershell
   .\fix_docker_network.ps1
   ```
2. **Docker Build nutzen**: Wir haben die Installation der Python-Pakete in ein `Dockerfile` verlagert. Dies macht den Start der Container robuster. Falls `pip install` fehlschlägt, führen Sie explizit einen Build aus, wenn Sie eine stabile Verbindung haben:
   ```bash
   docker compose build --no-cache
   ```
3. **MTU Probleme (VPN)**: Falls Sie ein VPN nutzen und "Network unreachable" oder hängende Verbindungen sehen, liegt dies oft an der MTU. Setzen Sie die MTU für Docker auf einen kleineren Wert (z.B. 1400).
   - In Docker Desktop: *Settings -> Docker Engine*
   - Fügen Sie hinzu: `"mtu": 1400`
4. **Explizites DNS**: Wir haben in der `docker-compose.yml` feste DNS-Server (`8.8.8.8`) vorbereitet. Falls Sie jedoch "Temporary failure in name resolution" sehen, **kommentieren Sie diese Zeilen aus**, damit Docker die DNS-Einstellungen Ihres Hosts verwendet. Dies ist oft in restriktiven Netzwerken oder Firmen-VPNs notwendig.
5. **WSL2 Neustart**: Ein kompletter Reset des WSL-Subsystems löst 90% der Netzwerk-Hänger:
   ```bash
   wsl --shutdown
   ```
   Starten Sie Docker Desktop danach neu.

7. **Redis Performance (vm.overcommit_memory)**:
   Redis meldet unter Linux/WSL oft eine Warnung bezüglich `vm.overcommit_memory`. In unseren Docker-Compose Dateien ist dies bereits via `sysctls` vorkonfiguriert. Falls der Start dennoch fehlschlägt oder Warnungen im Log erscheinen, führen Sie auf dem Host (bei WSL2 in der WSL-Distro) folgenden Befehl aus:
   ```bash
   sudo sysctl vm.overcommit_memory=1
   ```
   Dies stellt sicher, dass Redis Hintergrund-Snapshots zuverlässig erstellen kann.

8. **IPv6 Deaktivieren**: Falls Ihr Netzwerk kein IPv6 unterstützt, kann dies zu Timeouts führen. Deaktivieren Sie es in der Docker Engine Config:
   ```json
   {
     "ipv6": false,
     "dns": ["8.8.8.8", "1.1.1.1"]
   }
   ```

#### Permanenter Workaround (Ohne Postgres)
Wenn Sie die Netzwerkprobleme nicht beheben können, können Sie das System stattdstattdessen mit **SQLite** betreiben. Wir haben dafür eine separate Konfigurationsdatei vorbereitet:
```bash
docker compose -f docker-compose.sqlite.yml up -d
```
Das System nutzt dann automatisch lokale Datenbankdateien im `data/`-Ordner der jeweiligen Agenten.

### LLM Verbindungsfehler (`host.docker.internal` / Connection Refused)
Falls in den Logs Fehler wie `Failed to establish a new connection: [Errno 111] Connection refused` in Verbindung mit `host.docker.internal` (z.B. Port 11434 für Ollama oder 1234 für LMStudio) erscheinen:

#### Ursache:
1. **Localhost-Beschränkung**: Lokal installierte LLM-Server wie Ollama oder LMStudio lauschen standardmäßig oft nur auf `127.0.0.1` (localhost). Da Docker-Container über eine virtuelle Netzwerkbrücke auf den Host zugreifen, wird die Verbindung abgelehnt.
2. **Subnetz-Isolation (VirtualBox vs. WSL2)**: Die IP `192.168.56.1` gehört meist zum *VirtualBox Host-Only Adapter*. WSL2 läuft jedoch in einem eigenen Hyper-V Netzwerk. Windows routet Pakete zwischen diesen beiden virtuellen Netzwerken standardmäßig oft **nicht**, weshalb ein Container `192.168.56.1` schlichtweg nicht "sehen" kann.
3. **Firewall**: Die Windows-Firewall blockiert häufig eingehenden Traffic aus dem WSL2-Subnetz auf physische oder virtuelle Host-Interfaces (außer dem direkten WSL-Interface).

#### Die definitive Lösung:
1. **Automatisches Setup (Empfohlen)**: 
   Wir haben ein Skript erstellt, das die notwendigen Firewall-Regeln und Port-Weiterleitungen auf Ihrem Windows-Host automatisch einrichtet.
   - Öffnen Sie den Projektordner im Explorer.
   - Klicken Sie mit der rechten Maustaste auf **`setup_host_services.ps1`**.
   - Wählen Sie **"Mit PowerShell ausführen"**. 
   - Das Skript bittet ggf. um Administratorrechte.
   - **Neu**: Das Skript erkennt nun automatisch, auf welcher IP Ihre LLM-Dienste lauschen (z.B. falls LMStudio auf `192.168...` statt `127.0.0.1` steht) und konfiguriert den Proxy passend.
   - **Neu**: Das Skript stellt sicher, dass der erforderliche Windows "IP-Hilfsdienst" läuft.
   - Danach sind LMStudio/Ollama für Docker erreichbar.

2. **Verbesserte Agent-Logik**:
   Die AI-Agenten verfügen nun über einen **automatischen Fallback**. Wenn die Verbindung über `host.docker.internal` fehlschlägt, versucht der Agent automatisch, den Host über die IP des Netzwerk-Gateways zu erreichen. Dies löst viele Probleme in komplexen WSL2-Umgebungen ohne manuelles Eingreifen.

3. **Manuelle Konfiguration**:
   Falls Sie das Skript nicht nutzen möchten:
   - **Bindung auf 0.0.0.0 (oder alle Interfaces)**:
     - **LMStudio (Version 0.3.x / neu)**: 
       1. Klicken Sie in der linken Seitenleiste auf das **Entwickler-Icon** (`<->` oder "Local Server").
       2. Suchen Sie nach dem Schalter **"Im lokalen Netzwerk bereitstellen"** (oder "Provide on local network"). 
       3. **Empfehlung**: Lassen Sie diesen Schalter **AUS**, wenn Sie `setup_host_services.ps1` nutzen. Falls Sie ihn **AN** haben, achten Sie darauf, dass die gewählte IP (Dropdown in den Network Settings) erreichbar ist. Unser Skript erkennt dies nun automatisch.
   - **Ollama**: Ollama nutzt standardmäßig eine Umgebungsvariable. Setzen Sie `OLLAMA_HOST=0.0.0.0`. Unter Windows können Sie dies in den Systemeigenschaften (Umgebungsvariablen) festlegen oder Ollama über die PowerShell starten: `$env:OLLAMA_HOST="0.0.0.0"; ollama serve`.
4. **`host.docker.internal` nutzen**: Verwenden Sie in der `docker-compose.yml` immer `host.docker.internal`. Durch die neuen Fallback-Mechanismen ist dies nun die stabilste Option.

#### Alternative: Spezifische Host-IP nutzen (Nur für Experten)
Falls Sie LMStudio unbedingt auf einer IP wie `192.168.56.1` lassen möchten, müssen Sie sicherstellen, dass Ihr Windows-Host das Routing zwischen dem WSL2-Adapter und dem VirtualBox-Adapter erlaubt. Dies ist meist komplizierter als die `0.0.0.0`-Lösung. Wir empfehlen daher dringend: **`0.0.0.0` + Firewall-Regel**.

#### Unterschied zwischen Port-Binding und Host-IP Zugriff
Es ist wichtig, zwei Richtungen der Kommunikation zu unterscheiden:
1. **Vom Host zum Container (Inbound):** In der `docker-compose.yml` legen Sie fest, auf welcher IP Ihres Rechners die Dienste lauschen (z.B. `0.0.0.0` für alle, `127.0.0.1` für nur lokal).
2. **Vom Container zum Host (Outbound):** Wenn ein Agent auf LMStudio zugreift, kontaktiert er den Host (via `host.docker.internal` oder Proxy). Dies ist unabhängig von den Port-Einstellungen in der Compose-Datei.

#### Sicherheitshinweis zu 0.0.0.0:
Die Einstellung `0.0.0.0` bedeutet, dass der Dienst auf **allen** Netzwerkgeräten Ihres Rechners lauscht (auch WLAN/LAN). Falls Sie sich in einem unsicheren Netzwerk befinden:
- Nutzen Sie die Windows-Firewall, um den Zugriff auf die Ports einzuschränken.
- Alternativ binden Sie die Ports in Docker explizit an `127.0.0.1`.

#### Firewall:
Stellen Sie sicher, dass Ihre Windows-Firewall eingehende Verbindungen auf den Ports 11434 (Ollama) bzw. 1234 (LMStudio) für das vEthernet (WSL) Netzwerk erlaubt.
