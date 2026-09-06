# Lokaler Meet-KI-Assistent (erste Ausbaustufe)

Die nachfolgende Dialog-Ausbaustufe beginnt mit der separat getesteten
[Hub-Chat-Admission-Grundlage](../contracts/meet-chat-admission.md). Sie ist
noch nicht an produktive Meet-Events oder die Antwort-Task-Ausführung angebunden;
die unten beschriebenen Grenzen der ersten Ausbaustufe bleiben bestehen.

## Geliefert und bewusst begrenzt

Ein explizit vorautorisierter Projektbenutzer kann über
`POST /api/meet/v1/projects/{project}/turns` einen geschlossenen Hub-Auftrag
auslösen. Ein eigener GPU-Worker erzeugt denselben kurzen deutschen Antworttext
für Chat, Piper-Sprache und ein einfaches amplitudenanimiertes Avatarvideo.
Die Angular-Meet-Karte zeigt Text und Video als lokale Vorschau.
Task-Ansichten verwenden `/projects/{project}/tasks/{task}/turns` und exakt
deren Raumzuordnung; es gibt keinen stillen Fallback auf den Projektraum.

Mit `publish_to_meet: true` und separat konfiguriertem Maschinen-Trust darf
derselbe Auftrag zusätzlich als **Ananta (KI)** dem bereits zugeordneten Raum
beitreten, Text und synthetisches Audio/Video über Meets vorhandenen
DataChannel-/SFrame-Pfad publizieren und wieder verlassen. Der isolierte
Chromium-Prozess verwendet keine menschlichen Profile oder Capture-Rechte.
Es handelt sich um **einen begrenzten Antwortauftrag**, nicht um einen dauerhaft
zuhörenden Gesprächsagenten. Eingehende Raumchats lösen noch keine automatischen
Antwortaufträge aus. Browser-Bildschirmstream, Persona-Assetverwaltung,
Spracherkennung, Sprachklonen und fotorealistisches Talking-Head-Video bleiben
eigene Aufgaben im großen Track.

Die laufende öffentliche Instanz `webrtc.ananta.de` wurde durch diese Änderung
**nicht aktualisiert oder mit einem neuen Hub-Schlüssel freigeschaltet**.
Ebenso sind die bestehenden Hub-Feature-Flags nicht still aktiviert worden.
Lokale Testserver und der neue GPU-Compose-Stack sind getrennte Instanzen.

## Architektur und Autorität

- Hub: bestehende Task Queue, explizite `(tenant_id, project_id)`-Policy,
  Projekt-Schreibrecht, Task-/Lease-IDs, Ed25519-Aufnahmegrant und terminaler CAS.
- GPU-Worker: genau ein signierter Auftrag gleichzeitig, lokales Modell,
  CUDA-Spracherzeugung, NVENC-Video und optional derselbe begrenzte
  Browser-Publikationsschritt. Keine Worker-Delegation oder neue Task-Schleife.
- Meet: eigener Betreiber-Trust, Gerätebeweis, Einmal-Ticket, Membership und
  kurzlebige Sitzung. Human-OIDC und menschliche Capture-Klicks bleiben geschützt.
- Vor Veröffentlichung und während der Wiedergabe fragt der Worker ausschließlich
  den Hub nach der aktuellen Lease. Entzug des Projektrechts, Task-Abbruch,
  Archivierung, veränderte Lease oder Nichterreichbarkeit stoppen fail-closed.
  Prüfintervall 500 ms plus maximal drei Sekunden für eine Lease-Anfrage.
- Anfrage und Antwort des GPU-Workers sind HMAC-gebunden; Tokens und Inhalte
  werden nicht in Task-Historie oder Anwendungslogs gespeichert. Browser, WAV,
  Rohframes und MP4 leben im begrenzten temporären Verzeichnis. Ohne expliziten
  Demo-Export werden keine Medien dauerhaft gespeichert.

Die Hub-Domain hängt an kleinen Task-/Worker-Ports, nicht an Piper, FFmpeg oder
Playwright (DIP/ISP). LLM, TTS, Avatar, Lease-Prüfung und Publikation sind getrennte
Worker-Module (SRP). Die große bestehende Meet-Composition `src/server.js` bleibt
eine SRP-Altlast; die neue Token- und Nachrichtenpolicy liegt separat in
`src/machine-admission.js`.

## Modelle, Versionen und Lizenzen

| Bestandteil | Auswahl | Herkunft / Lizenzinformation |
|---|---|---|
| LLM | Qwen2.5 1.5B Instruct, Ollama-Quantisierung | [Qwen-Modellkarte](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct), Apache-2.0 |
| TTS | Piper 1.4.2, ONNX Runtime GPU 1.22.0 | [Piper](https://github.com/OHF-Voice/piper1-gpl), GPL-3.0; [ONNX Runtime](https://github.com/microsoft/onnxruntime), MIT |
| Stimme | de_DE-thorsten-medium | [Modellkarte](https://huggingface.co/rhasspy/piper-voices/blob/1162a9173d0ce503555aed757976b7a9912eae4c/de/de_DE/thorsten/medium/MODEL_CARD), Dataset CC0 |
| Video | Pillow-Geometrie + FFmpeg `h264_nvenc` | Synthetische Grafik, kein generatives Videomodell; [NVIDIA-FFmpeg-Dokumentation](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.1/ffmpeg-with-nvidia-gpu/index.html) |
| Browser | Playwright Python 1.58.0 / Chromium | [Docker-/Sandbox-Dokumentation](https://playwright.dev/docs/docker), Apache-2.0 für Playwright |

Ollama-Manifest: `65ec06548149b04c096a120e4a6da9d4017ea809c91734ea5631e89f96ddc57b`.
Gewichtsblob: `183715c435899236895da3869489cc30ac241476b4971a20285b1a462818a5b4`.
Der Adapter prüft Modellname, Manifestdigest und belegten GPU-Speicher; ein
stiller CPU- oder Cloud-Fallback ist nicht vorgesehen. Änderungen der Modell-
auswahl benötigen eine bewusste Anpassung von Name und freigegebenem Digest.
Der Voice-Download ist revisions- und SHA-256-gepinnt.

Open-Source-Modelle und Anwendungen bedeuten nicht, dass NVIDIAs gesamte
Treiber-/CUDA-Laufzeit Open Source ist. Für Weitergabe von Images gelten auch
die jeweiligen Drittanbieterbedingungen; die kommerzielle Ananta-Lizenz
ersetzt insbesondere keine Piper-/FFmpeg-Lizenzpflichten.

## GPU-Worker provisionieren

Das Laufzeitverzeichnis muss privat sein. Schlüssel und generierte Daten
gehören niemals in Git. UID/GID im Compose-Profil müssen dessen Eigentümer
entsprechen (Standard 1000:1000).

```bash
.venv/bin/python scripts/setup_meet_media.py /home/krusty/ananta/data/meet-media --machine-keys
export MEET_MEDIA_STATE_DIR=/home/krusty/ananta/data/meet-media
docker compose -p ananta-meet-media -f docker-compose.meet-media.yml build
docker compose -p ananta-meet-media -f docker-compose.meet-media.yml up -d
docker compose -p ananta-meet-media -f docker-compose.meet-media.yml exec meet-ollama ollama pull qwen2.5:1.5b
```

Dieses normale Profil setzt NVIDIA Container Toolkit voraus. Auf diesem PC
fehlte die Toolkit-/CDI-Anbindung. Dafür gibt es einen lokalen, generierten
Treiber-Overlay ohne Docker-Daemon-Änderung oder privilegierten Container:

```bash
.venv/bin/python scripts/meet_media_host_gpu.py /home/krusty/ananta/data/meet-media/host-gpu.yml
docker compose -p ananta-meet-media -f docker-compose.meet-media.yml \
  -f /home/krusty/ananta/data/meet-media/host-gpu.yml up -d
```

Nach einem Treiberwechsel neu generieren; der Generator überschreibt keine
vorhandene Datei. Die aktuellen Referenzläufe verwendeten `host-gpu-v2.yml`,
weil der erste Entwicklungsversuch `libnvcuvid.so.1` noch nicht mitführte.
Der aktuelle Generator enthält diese Abhängigkeit. Das Overlay bindet nur GPU 0
und konkret aufgelöste Treiberbibliotheken read-only ein. Keine Host-Kamera,
kein Desktop, Docker-Socket oder persönliches Browserprofil wird eingebunden.

Chromium läuft mit **aktivierter Sandbox** als Nicht-root-Benutzer, ohne
zusätzliche Container-Capabilities und unter `no-new-privileges`. Das eng
konfigurierte seccomp-Profil erlaubt seine Benutzer-Namespace-Erzeugung und
den inneren `chroot`; AppArmor bleibt aktiv. Herkunft des Profils:
`docker/meet-media/THIRD_PARTY.md`.

## Hub und Meet explizit verbinden

`docker-compose.meet-media-hub.yml` ergänzt die bestehende Hub-Compose-Datei,
nicht den separaten Worker-Stack. Bestehende Netzwerke bleiben beim Merge
erhalten. Der Operator setzt:

```text
ANANTA_MEET_ENABLED=1
ANANTA_MEET_MEDIA_ENABLED=1
ANANTA_MEET_MEDIA_ALLOWED_SCOPES=[["EXAKTER-TENANT","EXAKTES-PROJEKT"]]
ANANTA_MEET_MACHINE_ENABLED=1
ANANTA_MEET_MACHINE_ISSUER=https://ECHTER-HUB-HOST
```

Der GPU-Worker erhält als feste Rückrufadresse
`MEET_HUB_LEASE_URL=http://meet-authorizing-hub:5000/api/meet/v1/internal/lease`.

Der private Callback verlangt jetzt [anfragegebundene Lease V2](../contracts/meet-lease-v2.md).
Hub und Worker gemeinsam aktualisieren; ältere Zwei-Feld-Anfragen erhalten eine
maschinenlesbare Upgrade-Meldung, keine wiederverwendbare Freigabe.

Das zusätzliche Opt-in-Gate `tests/test_meet_profile_gpu.py` führt einen echten
lokalen LLM-/Piper-CUDA-/Persona-NVENC-Turn mit realer Profil-SQL, regulärem
Hub-Task und anfragegebundenem HTTP-Callback aus. Seine Bildzulassungs-Policies
sind ausdrücklich synthetische Test-Fixtures; es veröffentlicht weder in Meet
noch liefert es produktive Release-Evidenz. Die Callback-URL des privaten Workers
auf einen freien Port seines Docker-Gateways konfigurieren und pytest
`MEET_PROFILE_GPU_GATE=1`, `MEET_PROFILE_GPU_CALLBACK_HOST`,
`MEET_PROFILE_GPU_CALLBACK_PORT`, `MEET_MEDIA_GPU_ENDPOINT` und
`MEET_MEDIA_GPU_KEY_FILE` übergeben. Der Test startet und schließt den privaten,
auf die Lease-Route beschränkten Callback automatisch. Der öffentliche Hub oder
Meet-Server wird weder verändert noch neu gestartet.

Der Hub-Overlay mountet nur den Worker-Schlüssel und den privaten
Maschinenschlüssel. An Meet wird **nur** `machine-public.pem` read-only
weitergegeben; dort müssen `MACHINE_HUB_PUBLIC_KEY_FILE` und
`MACHINE_HUB_ISSUER` exakt zu diesem Hub passen. Ohne diesen separaten Trust
wird `/api/machine/sessions` abgewiesen. Bei aktiviertem Maschinenpfad bleibt
`MEDIA_E2EE_MODE=required` Pflicht. Einen zugeordneten privaten Raum müssen
Gegenstellen bereits betreten haben; ohne Gegenstelle endet die Publikation
innerhalb des Budgets mit einem Fehler, nicht mit einem fingierten Empfang.

Die aktuelle Publikationslease setzt explizite Projektmitgliedschaft mit
Schreibrecht voraus; ein bloßes Tenant-Admin-Recht erweitert sie nicht.
Eine Vorschau verleiht keine Room-Membership. Browser können die Vorschau
ohne Autoplay wiedergeben; headless Worker-Publikation benötigt keinen Klick.
Das Löschen der lokalen Vorschau bricht keinen bereits delegierten Auftrag ab;
dafür die vorhandene Hub-Aufgabenverwaltung verwenden.

## Verifikation und Grenzen

Implementierungsstände: Ananta `5c300821de83b2057afd4953294cc692142239a7`,
Meet `c1e05b1ad7edb7701ed12ea4718af59fc9b79eda`.

Referenz-PC: RTX 3080, 10.240 MiB VRAM, Treiber 595.84. Beobachteter warmer
synthetischer Lauf: **2,3 s Erzeugung für 3,82 s Audio/Video**. Das ist eine
einzelne technische Beobachtung, kein belastbarer Lastbenchmark.

```bash
MEET_MEDIA_GPU_GATE=1 \
MEET_MEDIA_GPU_ENDPOINT=http://PRIVATE-WORKER-IP:8094/v1/turns \
MEET_MEDIA_GPU_KEY_FILE=/home/krusty/ananta/data/meet-media/worker-key \
.venv/bin/python -m pytest tests/test_meet_media_gpu.py -q
```

`scripts/check_meet_media_local.py` erzeugt optional ein ausdrücklich
synthetisches WAV-/MP4-Demoartefakt. Im separaten Meet-Repository prüft
`MACHINE_E2E_VIDEO=/absoluter/pfad/demo.mp4 node --test test/machine.browser.e2e.test.js`
zwei echte isolierte Browser, Chatempfang, dekodierte Video-/Audiodaten und
erforderliches SFrame. Kamera-/Mikrofon-/Bildschirm-Capture wird im Test
absichtlich verweigert und gezählt. Keine Person muss einen Test entsperren.

Diese Prüfungen sind lokale synthetische technische Beobachtungen ohne
vorreservierte Registry-Run-ID. Sie sind **keine Produktionsfreigabe** und
beweisen weder die öffentliche Instanz noch externe TURN-/Multi-Host-Pfade,
Langzeitbetrieb oder den gesamten Persona-/Browserstream-Track.
