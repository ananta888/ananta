# Voice Runtime Limitations

- Das aktuelle Voxtral-Backend ist eine stabile Adapter-Grenze; echte Modellverdrahtung ist austauschbar gehalten.
- Streaming ist vorbereitet, im MVP aber noch nicht als End-to-End-Feature verdrahtet.
- `VOICE_STORE_AUDIO=true` aktiviert derzeit keine persistente Roh-Audio-Speicherung; der Hub bleibt fail-closed.
- Docker-Smoke- und Live-Voxtral-Tests sind opt-in, damit Standard-CI ohne GPU/Model-Downloads bleibt.
- Bei Runtime-Ausfall meldet der Hub degrade/unavailable-Metadaten statt stiller Fehler.
