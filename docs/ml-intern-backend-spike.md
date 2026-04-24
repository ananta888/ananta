# ml-intern Backend Spike (bounded)

Der Spike integriert `ml_intern` als optionalen, begrenzten External-Worker-Adapter.

## Ziel

- Additive Integration ohne Aenderung am Standard-Backend-Pfad.
- Harte Bounds fuer Laufzeit, Prompt-Groesse und Output-Groesse.
- Keine implizite Aktivierung (default: off).

## Konfiguration

```json
{
  "ml_intern_spike": {
    "enabled": false,
    "command_template": "python worker.py --prompt-file {prompt_file}",
    "timeout_seconds": 180,
    "max_prompt_chars": 6000,
    "max_output_chars": 8000,
    "working_dir": "agent",
    "env_allowlist": ["HOME"]
  }
}
```

## Adapter-Vertrag

- Service: `agent/services/ml_intern_adapter_service.py`
- Einstieg: `invoke_spike(prompt, agent_cfg, model=None, timeout_seconds=None)`
- Response:
  - `ok`, `returncode`, `stdout`, `stderr`
  - `bounded_execution` mit `timeout_seconds`, `max_prompt_chars`, `max_output_chars`, `working_dir`, `duration_ms`

## SGPT-Backend-Pfad

- `POST /api/sgpt/execute` akzeptiert `backend=ml_intern`, wenn `ml_intern_spike.enabled=true`.
- Option-Flags sind fuer `ml_intern` nicht erlaubt.
