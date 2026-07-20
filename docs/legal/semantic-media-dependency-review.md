# Dependency- und Lizenzreview: Semantic Media und Speech

| Komponente | Herkunft / Pinning | Lizenz- und Nutzungsstatus | Wartung / Freigabe |
|---|---|---|---|
| Angular / WebRTC-Client | `frontend-angular/package-lock.json`, exakte Versionen und Integrityhashes | OSS-Lizenzen müssen im normalisierten SBOM belegt sein | automatisierter Dependency-Scan erforderlich |
| LiveKit Client | `livekit-client@2.20.1` exakt gepinnt; Lockfile bindet Transitivedependencies | Apache-2.0; Browser-/E2EE-Nutzung zulässig, NOTICE prüfen | Client- und Serverversion separat scannen |
| SFU-Server | `livekit/livekit-server:v1.13.1@sha256:2c6869d2d5ff6c9c0166f47be1c92dad6928bfecfa5e4060a6ece48db8accfa3` | [Apache-2.0 laut Upstream](https://github.com/livekit/livekit); NOTICE muss mit dem Image-SBOM archiviert werden | ohne Scanner-/SBOM-Nachweis `NO-GO` |
| TURN-Gate | `coturn/coturn:4.6.3-r3@sha256:71c3c990283385567f11794ee692e3a47b66fd9b0bb39e42afbe776e331dd888`; nur isoliertes Relay-Gate-Profil | [BSD-3-Clause laut Upstream](https://github.com/coturn/coturn); Image-SBOM und NOTICE-Pruefung erforderlich | kein Produktiv-Credential; non-root; `cap_drop: ALL` plus ausschließlich `NET_BIND_SERVICE`, da das Upstream-Binary diese File-Capability trägt |
| Speech-/Reconciliation-Codecs | Python-/Container-Lockquellen, keine dynamische Laufzeitinstallation | Codec-, Modell- und Datensatzlizenz getrennt prüfen | kein Modell ohne Herkunfts- und Usage-Beleg |
| Speech-Training-Worker | digestgepinntes Base-Image und gehashte Requirements | Offline-/lokale Nutzung; Modellartefakte separat freigeben | non-root, read-only, keine Hub-Task-/Policylogik |
| Hub | digestgepinntes Base-Image und reproduzierbare Pythonauflösung | bestehende Projektlizenz plus Paket-SBOM | Secrets und lokale Pfade dürfen nie im SBOM landen |

Die aktuell erlaubten lokalen ASR-Bundles sind nicht implizit Teil des
Containerimages, sondern werden read-only gemountet. Ihre Herkunft,
unveränderliche Revision, Lizenz und jeder einzelne Dateidigest stehen in
`models/voice/manifests/voice-models.json`:

| Modell-ID | Herkunft / Revision | Lizenz- und Nutzungsstatus |
|---|---|---|
| `vosk-model-small-de-0.15` | [offizieller Vosk-Modellkatalog](https://github.com/alphacep/vosk-space/blob/master/models.md), Revision `0.15` | Apache-2.0; lokales Offline-ASR erlaubt |
| `whisper-cpp-v1.8.6-ggml-small` | [whisper.cpp](https://github.com/ggml-org/whisper.cpp) Commit `23ee03506a91ac3d3f0071b40e66a430eebdfa1d`, konvertiertes OpenAI-Small-Modell `90a64d80ea254cf67575b41a5971f972c79f7b45` | MIT; [OpenAI veröffentlicht Code und Gewichte unter MIT](https://github.com/openai/whisper); nur manifestgebundenes Offline-ASR |
| `faster-whisper-small-2ec96c54` | [Systran/faster-whisper-small](https://huggingface.co/Systran/faster-whisper-small/tree/2ec96c5472da50d38d40c0cfe0602af2e94b4c8a), Commit `2ec96c5472da50d38d40c0cfe0602af2e94b4c8a` | MIT laut Modellkarte; nur manifestgebundenes Offline-ASR |

Die Runtime verifiziert die im Manifest hinterlegten SHA-256-Werte vor der
Nutzung. Mutable Revisionen, fehlende Lizenzangaben, Runtime-Downloads und
nicht manifestierte Dateien bleiben fail-closed. Synthetische Voice- und
Adaptermodelle folgen zusätzlich dem getrennten Review in
`docs/legal/speech-model-license-review.md` und sind ohne vollständige
Gewichts-/Datensatzakte nicht freigegeben.

Normativer Gate-Command:

```bash
python scripts/run_semantic_media_supply_chain_gate.py \
  --sbom-report <cyclonedx-normalized.json> \
  --scanner-report <vulnerability-summary.json> \
  --as-of 2026-07-19
```

Der normalisierte Input wird reproduzierbar aus den tatsaechlichen lokalen
Image-IDs erzeugt:

```bash
python scripts/build_semantic_media_containers.py

python scripts/generate_semantic_media_supply_chain_reports.py \
  --syft /path/to/syft \
  --grype /path/to/grype \
  --exceptions config/semantic-media-vulnerability-exceptions.v1.json \
  --sbom-output artifacts/domain/semantic-media-sbom-normalized.json \
  --scanner-output artifacts/domain/semantic-media-vulnerabilities-normalized.json
```

Der Build-Command baut Hub, Angular-Frontend, CPU-Reconciliation und
Speech-Training aus den eingecheckten digestgepinnten Dockerfiles und bindet
den SFU- und TURN-Tag an die in Compose festgeschriebenen OCI-Digests. Er
schreibt eine content-freie Inventur aller sechs resultierenden Image-IDs; ein
fehlender oder abweichender externer Digest beendet den Lauf fail-closed.

Der Generator verwirft Scannerzeitstempel, Layerpfade und Hostpfade, bindet
jedes Ergebnis an die Docker-Image-ID und akzeptiert nur explizite, noch nicht
abgelaufene High-Ausnahmen fuer tatsaechlich vorhandene Finding-IDs. Critical
Findings koennen nicht ausgenommen werden. Ueberlange kombinierte
Lizenzausdruecke werden nicht abgeschnitten, sondern als Digest markiert und
bleiben bis zum manuellen Detailreview release-blockierend.

## Archivierter Vor-TURN-Lauf vom 2026-07-19

Werkzeuge: Syft `1.44.0` und Grype `0.112.0`; die Releasearchive wurden vor
Ausfuehrung gegen die offiziellen Checksum-Dateien verifiziert. Dieser Lauf
erfasste 4.046 Pakete in Hub, Frontend, SFU, Reconciliation und Training, ist
aber seit Aufnahme des TURN-Gates **kein aktueller Release-Nachweis mehr**.
Die Source-of-Truth ist ausschließlich ein danach neu erzeugtes, sechs
Komponenten umfassendes Artifact.

| Komponente | Image-ID (SHA-256) | Pakete | Critical | High | Lizenz ohne Aussage |
|---|---|---:|---:|---:|---:|
| Frontend | `e193a1c0977472c0b6499f6e9c41a48af83be55d5b3e95fda8fee8aafdd55799` | 1.589 | 128 | 709 | 30 |
| Hub | `ea87d0eb6cbb457b3277745b9e516b4c04ebc702b266539998a1a751f982ddf5` | 1.807 | 55 | 240 | 174 |
| Reconciliation | `61731f95c4e3ac6bc62156b4d2cedcc1b898c90aea095dfcc57f480591a5fac0` | 373 | 34 | 169 | 14 |
| SFU | `2c6869d2d5ff6c9c0166f47be1c92dad6928bfecfa5e4060a6ece48db8accfa3` | 136 | 2 | 18 | 119 |
| Training | `51abee88232df0ce71b42c427c1b3b63da950444fdb3fe4d516fb4b7a16e8c4a` | 141 | 14 | 80 | 12 |
| TURN | nicht Bestandteil dieses archivierten Laufs | — | — | — | — |

Das archivierte Ergebnis war ein ausdrueckliches **NO-GO**: 233 Critical- und 1.216 nicht
ausgenommene High-Treffer sowie 350 nicht aufgeloeste und 69 zusammengesetzte,
noch im Detail zu pruefende Lizenzangaben. Die Zahlen sind Scanner-Treffer und
nicht deduplizierte CVE-Zaehler; die normalisierte Finding-Datei ist die
Source-of-Truth fuer Triage und Paket-/Image-Zuordnung. Keine Ausnahme ist
eingetragen. Das Gate darf erst nach Image-Updates, Rebuild, erneutem Scan und
einem dokumentierten Lizenzreview auf gruen wechseln.

Beide Reports müssen source-/image-digestgebunden und content-free sein. Ohne
lokal verfügbaren Scanner oder echtes Container-SBOM bleibt die Entscheidung
`unverified` und release-blockierend. Critical Findings sind nie akzeptierbar.
High-Ausnahmen benötigen Finding-ID, Owner, Begründung und Ablaufdatum.

Container müssen non-root, read-only, `cap_drop: [ALL]`,
`no-new-privileges`, begrenzte CPU/RAM/PIDs, interne Kontrollnetze und explizite
Secretzuführung verwenden. Das isolierte TURN-Gate erhält nach `cap_drop: ALL`
als einzige Zusatz-Capability `NET_BIND_SERVICE`; SFU und Worker erhalten keine.
SFU und Worker dürfen keine Hub-Taskqueue, Orchestrierung oder
Policyentscheidung importieren.
