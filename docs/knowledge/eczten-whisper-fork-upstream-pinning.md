# Echten whisper.cpp-Fork: Upstream-Pinning

## Ziel
- Einen echten whisper.cpp-Fork anlegen und den Upstream fest pinnen.
- Wartungsstrategie dokumentieren, um Updates kontrolliert zu übernehmen.

## Pinned Upstream
- Repository: `whisper.cpp`
- Pinning-Methode: Git-Tag oder SHA (konkret in `scripts/wadec/whisper_fork_setup.sh`).

## Wartungsstrategie
1. **Regelmäßige Checks**: Prüfen, ob neue Tags/SHAs verfügbar sind.
2. **Kontrollierte Updates**: Nur bei Bedarf den Fork auf das nächste Pin-Target aktualisieren.
3. **Testlauf**: Nach jedem Update `python -m pytest -q --confcutdir=tests/self_development -o addopts= -o cache_dir=/tmp/ananta-self-dev-cache tests/self_development` ausführen.
4. **Dokumentation**: Änderungen in diesem Dokument und im Todo-Entry nachvollziehbar erfassen.

## Verantwortlichkeiten
- Hub: Veröffentlichung und Policy-Checks.
- Worker: Dateiänderungen, Tests, keine Commits/Pushes.
