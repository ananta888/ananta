# ADR: lokales Speech-Adaptation-Backend

- Status: accepted with production No-Go
- Datum: 2026-07-19
- Vertrag: `ananta.speech-adaptation.v1`

## Kontext

Ananta benötigt receiver-lokale, eng an Pair, Richtung, Sprecher und Consent
gebundene Speech-Adapter. Der bestehende Text-LoRA-Vertrag ist dafür weder
semantisch noch datenschutzrechtlich geeignet. Die Auswahl muss lokale
Ausführung, reproduzierbare Artefakte, kommerzielle Nutzbarkeit und einen
offline installierbaren Supply-Chain-Pfad abdecken.

## Vergleich

| Verfahren | Lizenz/Supply Chain | Datenbedarf | Hardware | Checkpoint/Export | Inferenz | Entscheidung |
|---|---|---|---|---|---|---|
| OpenVoice V2 Tone-Color-Embedding | Repository und V1/V2 laut offiziellem Projekt MIT; Checkpoint-Bundle muss separat per Digest und Lizenznachweis zugelassen werden | kurze Referenzaufnahme, zero-shot | CPU möglich, GPU empfohlen | Speaker-Embedding plus Converter-Bindung; kein klassisches LoRA | lokale Tone-Color-Konvertierung, mehrsprachig | ausgewählter Kandidat, derzeit No-Go für Produktion |
| Coqui XTTS-v2 | Modellgewichte unter CPML, nur nicht-kommerzielle Nutzung | kurze Referenzen oder Fine-Tuning-Daten | typischerweise GPU und hoher RAM/VRAM-Bedarf | Full/Fine-Tune-Checkpoint, große Artefakte | lokale TTS-Inferenz | abgelehnt: Nutzungsumfang nicht kompatibel |
| StyleTTS2 | Quellcode MIT; Gewichte, Hilfsmodelle und Datensätze müssen einzeln geprüft werden | höherer Trainings- und Kurationsbedarf | GPU erforderlich | mehrteilige Checkpoints, wartungsintensiv | lokale TTS-Inferenz | abgelehnt: Supply Chain und Betriebsaufwand |
| Piper Training | aktuelle `piper-tts`-Distribution GPL-3.0-or-later; Stimmen haben individuelle Modell-/Dataset-Lizenzen | sprecherbezogener Korpus | CPU-Inferenz, Training typischerweise GPU | vollständiges ONNX-Modell statt kleinem Pair-Adapter | sehr gute lokale Inferenz | abgelehnt: Artefaktsemantik und Lizenzmatrix |

Offizielle Quellen:

- OpenVoice-Projekt und Lizenz: https://github.com/myshell-ai/OpenVoice
- OpenVoice-V2-Nutzung: https://github.com/myshell-ai/OpenVoice/blob/main/docs/USAGE.md
- XTTS-v2-Modelllizenz: https://huggingface.co/coqui/XTTS-v2/blob/main/LICENSE.txt
- StyleTTS2-Projekt: https://github.com/yl4579/StyleTTS2
- Piper-Paket: https://pypi.org/project/piper-tts/

## Entscheidung

OpenVoice V2 ist der einzige für eine spätere reale Adapterimplementierung
ausgewählte Kandidat. Die untersuchten Quellstände werden auf folgende Commits
gepinnt:

- OpenVoice: `74a1d147b17a8c3092dd5430504bd83ef6c7eb23`
- MeloTTS: `209145371cff8fc3bd60d7be902ea69cbdb7965a`

Der Produktionsstatus bleibt trotzdem **No-Go**, bis alle folgenden Belege im
lokalen Modellkatalog vorhanden sind:

1. SHA-256 und Herkunft jedes Converter-, Base-Speaker- und Hilfsmodellfiles,
2. separater Lizenznachweis für genau diese Modellfiles,
3. offline reproduzierbares Dependency-Lock inklusive Torch/CUDA-Bindung,
4. synthetischer Hardware-Spike mit RAM, VRAM, Disk, Laufzeit und Artefaktgröße,
5. Nachweis, dass das exportierte Speaker-Embedding keine rückgewinnbare
   Referenzaufnahme enthält und die Memorization-Gates besteht.

Bis dahin enthält der Container ausschließlich das deterministische
`mock`-Backend. `openvoice_v2` ist im Wire-Vertrag reserviert, wird aber nicht
in der Startup-Registry registriert. Dadurch schlägt jede reale Auswahl
fail-closed mit `speech_backend_not_admitted` fehl.

## Architekturfolgen

- Der Hub bleibt Eigentümer von Task, Capacity-Lease, Attempt, Fencing und
  Artifact-Publish-Ziel.
- Worker-Backends kennen weder Hub-Queue noch Consent-Mutation, Peer oder SFU.
- Backends sind über einen kleinen Port substituierbar; die Registry ist nach
  Startup unveränderlich.
- Die No-Go-Entscheidung blockiert keine Contract-, Mock-, Evaluation-,
  Registry- und Receiver-Fallback-Tests, begründet aber ausdrücklich keinen
  Modellqualitätsnachweis.

Diese Trennung schützt insbesondere SRP, OCP und DIP: Orchestrierung,
Frameworkadapter, Evaluation, Registry und lokale Inferenz bleiben eigene
Verantwortlichkeiten und hängen nur von kleinen Ports ab.
