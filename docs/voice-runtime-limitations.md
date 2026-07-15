# Voice Runtime Limitations

- Das aktuelle Voxtral-Backend ist eine stabile Adapter-Grenze; echte Modellverdrahtung ist austauschbar gehalten.
- Kurzes Streaming und beaufsichtigte rollierende Langzeit-Runs sind über den
  Hub verdrahtet. Der Langzeitmodus ist auf acht Stunden begrenzt und keine
  Zusage für unbeaufsichtigtes 24/7: Browser-Drosselung, widerrufene
  Audiofreigaben und Android-Prozesslebenszyklen bleiben Plattformgrenzen.
- `VOICE_STORE_AUDIO=true` aktiviert derzeit keine persistente Roh-Audio-Speicherung; der Hub bleibt fail-closed.
- Docker-Smoke- und Live-Voxtral-Tests sind opt-in, damit Standard-CI ohne GPU/Model-Downloads bleibt.
- Bei Runtime-Ausfall meldet der Hub degrade/unavailable-Metadaten statt stiller Fehler.
