# Speech adaptation backend spike

Der Spike ist absichtlich auf das deterministische Mock-Backend begrenzt. Er
verwendet `tests/fixtures/speech_training/mock_manifest.json`, enthält kein
Audio und keine personenbezogenen Daten und misst Laufzeit, RSS, Disk- und
Artefaktbytes. Ein OpenVoice-Spike ist durch den ADR-No-Go blockiert, bis die
Modell- und Dependency-Digests lokal zugelassen sind.

Ausführung:

```bash
python scripts/spikes/speech_adaptation_backend/run_mock_spike.py
```

Das JSON-Ergebnis wird auf stdout geschrieben und nicht als volatile
Repository-Datei gespeichert.
