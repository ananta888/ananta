# ADR: Semantic-Visual Feasibility Gate

Status: **No-Go für Aktivierung**, Forschungskomponenten dürfen fail-closed
weiterentwickelt werden.

## Entscheidung

Der observe-only Spike `scripts/spikes/semantic_visual_feasibility.mjs` nutzt
für Ordinary und Semantic dieselben 24 Frames mit 320×180 Pixeln bei 12 fps
und einem festen Seed. Ordinary wird mit dem Standardcodec VP9/WebM über
FFmpeg 6.1 encodiert. Der modellfreie Semantic-Prototyp nutzt deterministische
16×16-Change-Tiles und Standard-DEFLATE. Es werden keine Modellfähigkeiten
angenommen oder simuliert.

Vor dem Lauf waren folgende Schwellen im Skript festgeschrieben:

- Sicherheitsgrenze: PSNR mindestens 30 dB, Drift-MAE höchstens 6.
- Go: Byte-Ratio für statische UI, Textscroll und Cursor höchstens 0,70;
  Kamera höchstens 1,10; Rauschen höchstens 1,25; CPU höchstens 2× Ordinary;
  Speicher höchstens 64 MiB; p95-Latenz höchstens 1,25×; mindestens drei
  byte-beneficial Szenarien.
- No-Go: mittlere Byte-Ratio über 1,25; CPU über 3×; Speicher über 128 MiB;
  p95-Latenz über 1,75×; weniger als zwei byte-beneficial Szenarien oder eine
  verletzte Sicherheitsgrenze.
- Werte zwischen Go und No-Go wären `conditional_go` gewesen.

Gemessen werden statische UI, Textscroll, Cursor/Animation, Kamera, Scene Cut
und starkes Rauschen jeweils unter LAN-, WAN- und constrained Profil. Der
vollständige maschinenlesbare Lauf liegt in
`artifacts/domain/semantic-visual-feasibility.json`.

## Ergebnis

Der Lauf ergab:

- mittlere Byte-Ratio: **6,441×**,
- maximale p95-Latenz-Ratio: **61,612×**,
- maximale CPU-Ratio: **0,145×**,
- maximaler Working-Set: **197.281 Byte**,
- minimales PSNR: **39,223 dB**,
- maximale Drift-MAE: **1,664**,
- byte-beneficial: **1 von 6 Szenarien**.

Damit sind die Qualitäts- und Speichergrenzen bestanden, der Transportnutzen
aber eindeutig nicht. Nachträgliches Ändern der Schwellen oder Entfernen der
schlechten Kamera-/Scroll-/Cursorfälle ist unzulässig.

Das nachgelagerte deterministische Kapazitätsmodell prüft zusätzlich 2-, 10-
und 20-Sekunden-Fenster für 2 und 10 Teilnehmer. Die maximale mittlere
Byte-Ratio darf 0,70, die p95-Ratio 1,25 und der Worst Burst 512 KiB nicht
überschreiten. Gemessen wurden bis zu 6,888 mittlere Ratio, 26,700 p95-Ratio
und 7.626.960 Byte Worst Burst. Das maschinenlesbare Release-Gate weist daher
neben `spike_no_go` auch fehlenden Byte-Nutzen und überschrittene Bursts aus.
Der Gate-Runner berechnet die Spike-Entscheidung erneut aus den Rohmetriken;
ein bloßes Umschreiben des Ergebnisfelds kann kein Go erzeugen.

## Konsequenzen

- Semantic Visual darf nicht als aktiver Produktpfad freigeschaltet werden.
- Nachfolgende Scene-, Capture-, Encoder-, Receiver- und Rendererbausteine
  bleiben Forschungsimplementierungen hinter einem fail-closed Release-Gate.
- Ein fehlender M3-SFU-Port, ein abgelaufener Contract/eine Lease oder ein
  unbestätigter Qualitätsbericht ergibt Ordinary-Fallback, nie implizite
  Aktivierung.
- Ordinary-WebRTC und seine Feature-Flags bleiben intakt.
- Ein neuer Go-Versuch benötigt eine neue versionierte Strategie, dieselben
  Szenarien und Profile sowie erneut vorab versionierte Schwellen. Das aktuelle
  Artefakt bleibt unverändert nachvollziehbar.
