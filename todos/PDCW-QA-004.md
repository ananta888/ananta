# PDCW-QA-004: Hub-Restart & Backpressure Soak

## Scope
- Reproduce **Hub-Restart** with DB/Redis/Search/Relay/SFU outages.
- Simulate **slow receivers**, **network switches**, and **queue pressure** for the Agent/Live lane.
- **Buzz failures are excluded** from this gate; they belong to the dedicated Buzz-Gate.

## Files Changed
- `scripts/gates/backpressure_soak.sh` — new soak script.
- `tests/chaos_gates/test_hub_restart_gate.py` — new pytest gate.
- `todos/PDCW-QA-004.md` — this todo note.

## Verification
Run:
```bash
python -m pytest -q --confcutdir=tests/self_development -o addopts= -o cache_dir=/tmp/ananta-self-dev-cache tests/self_development
```
Exit code must be **0** before marking complete.
