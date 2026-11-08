# Todo: Echten whisper.cpp-Fork Pin-Maintenance-Strategie

## Status
- [ ] Fork anlegen und Upstream pinnen
- [ ] Wartungsstrategie dokumentieren
- [ ] Tests ausführen (pytest)

## Details
- Nur drei Dateien ändern:
  - `docs/knowledge/eczten-whisper-fork-upstream-pinning.md`
  - `scripts/wadec/whisper_fork_setup.sh`
  - `todos/active/todo.wadec-echten-whisper-fork-pin-maintenance-strategy.md`
- Testkommando: `python -m pytest -q --confcutdir=tests/self_development -o addopts= -o cache_dir=/tmp/ananta-self-dev-cache tests/self_development`
- Keine Commits, Pushes oder Task-Erstellungen.
