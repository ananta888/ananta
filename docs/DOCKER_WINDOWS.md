# Docker auf Windows und WSL2

## Hängender Docker-Daemon bei nativem systemd

Moderne WSL2-Distributionen können systemd nativ über
`[boot] systemd=true` in `/etc/wsl.conf` starten. Ein historischer
`start-systemd-namespace`-Aufruf aus `/etc/bash.bashrc` darf dann nicht
zusätzlich ausgeführt werden. Zwei konkurrierende systemd-/Docker-Instanzen
können dazu führen, dass `docker info` hängt oder Clients unterschiedliche
Sockets sehen.

Diagnose in Ubuntu:

```bash
ps -p 1 -o comm=
systemctl is-active containerd docker
timeout 10 docker version
rg -n 'start-systemd-namespace' /etc/bash.bashrc /etc/profile.d 2>/dev/null
```

Ist PID 1 bereits `systemd`, zuerst `/etc/bash.bashrc` sichern, dann
ausschließlich den alten Namespace-Hook deaktivieren. Anschließend in Windows
PowerShell `wsl.exe --shutdown` ausführen und die Distribution neu öffnen.
Dabei werden keine Containerdaten gelöscht. Volumes, Ollama-Bind-Mounts und
Workflow-Keyrings dürfen für diese Reparatur nicht entfernt werden.

## Verschlüsseltes Backup auf WSL und Windows-Desktop

Das lokale Backup wird aus WSL mit `scripts/ananta-backup.py` gestartet. Sein
privates WSL-Ziel muss vollständig außerhalb `/mnt` liegen. Als Windows-Ziel
akzeptiert die CLI ausschließlich den Pfad, den Windows aktuell als Known
Folder `DesktopDirectory` meldet; bei OneDrive-Umleitung ist deshalb der
umgeleitete Desktop zu verwenden. Auf Windows werden nur OpenPGP-Ciphertext und
dessen Prüfsummenmetadaten veröffentlicht, keine entschlüsselten Nutzdaten.

Die Desktop-Kopie ist keine Offline-Sicherung. Die verschlüsselten Pakete
müssen zusätzlich auf mindestens zwei physisch getrennten Offline-Datenträgern
liegen; der private Recovery-Key wird separat aufbewahrt. Restore-Ziele unter
`/mnt` sind unzulässig, und ein Restore erfolgt ausschließlich als isolierter
Drill.

Alle Voraussetzungen, Befehle und Sicherheitsgrenzen stehen im
[`lokalen Backup-/Restore-Runbook`](operator/local-backup-restore.md).

## Hot Reload

Bei der Entwicklung mit Docker Desktop auf Windows kann es zu Problemen mit dem "Hot-Reload" von Volumes kommen, insbesondere bei Frontend-Frameworks wie Angular.

## Das Problem

Aenderungen am Quellcode auf dem Host-System werden zwar in den Container gespiegelt (Volume Mount), aber der Build-Prozess innerhalb des Containers (z.B. `ng serve`) erkennt die Dateiaenderungen nicht zuverlaessig oder liefert weiterhin alte, gecachte JavaScript-Bundles aus.

Dies fuehrt dazu, dass im Browser trotz Code-Aenderungen die alte Version der Anwendung angezeigt wird oder Tests gegen einen veralteten Stand laufen.

## Prüfung und Fallback

`compose.dev.ollama.yml` bindet Angular- und Python-Quellen ein. Angular nutzt
Polling; Flask nutzt den Development-Reloader. Eine normale Quelländerung soll
daher ohne Image-Rebuild erkannt werden. Zuerst Container-Logs prüfen:

```bash
docker compose --env-file .env \
  -f docker/compose-next/compose.dev.ollama.yml logs -f \
  angular-frontend ai-agent-hub ai-agent-alpha ai-agent-beta
```

Nur wenn Abhängigkeiten, Dockerfile-Inhalte oder gebackene Dateien geändert
wurden, den Stack mit Build neu abgleichen:

### Befehl
```bash
docker compose --env-file .env \
  -f docker/compose-next/compose.dev.ollama.yml up -d --build
```

In diesem Repository ist der explizite Stack ueblicherweise:
```bash
docker compose --env-file .env -f docker/compose-next/compose.stack.quickstart.yml up -d --build
```

Der Legacy-Helfer `scripts/compose-test-stack.sh clean` gehört nicht zu diesem
Compose-Stack: Er ist ausschließlich für einen wegwerfbaren Test-Stack
bestimmt und entfernt dessen Nicht-Ollama-Volumes. Für den lokalen
`compose-next`-Stack darf er weder als Hot-Reload-Fallback noch als
Reparaturbefehl verwendet werden.

Wenn unter Windows Fehler wie `invalid volume specification` auftreten:
```powershell
$env:COMPOSE_CONVERT_WINDOWS_PATHS=1
docker compose up -d --build
```

## Best Practices fuer die Entwicklung

1. **Manueller Build vor Start**: Fuehren Sie `npm run build` auf dem Host aus, bevor Sie die Container starten, wenn Sie nicht den Dev-Server im Container nutzen.
2. **Browser-Cache leeren**: Oft hilft es auch, den Browser-Cache zu leeren oder ein privates Fenster zu nutzen, falls das Frontend bereits neu gebaut wurde, der Browser aber noch alte Files haelt.
3. **Automatisierte Builds**: In Continuous-Integration-Umgebungen sollte immer mit dem Flag `--no-cache` gebaut werden, um Seiteneffekte zu vermeiden.

## Troubleshooting

Falls der Agent keine Verbindung zum LLM auf dem Host aufbauen kann (z.B. Ollama oder LM Studio), nutzen Sie das Skript:
```powershell
.\setup_host_services.ps1
```
Dieses Skript konfiguriert die Firewall und den Proxy fuer den Zugriff auf `host.docker.internal`.

Falls Redis beim Start `vm.overcommit_memory` meldet, setzen Sie den Wert auf dem Host einmalig (Admin-PowerShell):
```powershell
wsl -d docker-desktop sysctl -w vm.overcommit_memory=1
```
Persistente Variante (bleibt nach Neustarts erhalten):
```powershell
wsl -d docker-desktop sh -c "echo 'vm.overcommit_memory=1' >> /etc/sysctl.conf && sysctl -p"
```
