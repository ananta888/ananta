# Sandbox-Abstraktion
<!-- COSMOS-005 -->

## Zweck

Schreibende Agentenarbeit (Builds, Tests, CLI-Aufrufe, Diffs) läuft in einer isolierten
Sandbox — nie direkt im Host-Prozess. Die Sandbox-Schicht ist ein austauschbarer Port:
Tests nutzen FakeSandbox, Produktion nutzt local_process_restricted oder Docker.

---

## Port-Interface

```python
from pathlib import Path
from typing import Protocol

class SandboxBackend(Protocol):
    def start(self, config: "SandboxConfig") -> str:
        """Startet Sandbox, gibt sandbox_id zurück."""
        ...

    def exec(
        self,
        sandbox_id: str,
        cmd: list[str],
        timeout: int,                  # Sekunden
        env_override: dict | None = None,
    ) -> "ExecResult":
        """Führt Befehl in Sandbox aus. Blockiert bis Abschluss oder Timeout."""
        ...

    def copy_in(self, sandbox_id: str, src: Path, dst: str) -> None:
        """Kopiert Datei/Verzeichnis vom Host in die Sandbox."""
        ...

    def copy_out(self, sandbox_id: str, src: str, dst: Path) -> None:
        """Kopiert Datei/Verzeichnis aus Sandbox auf Host."""
        ...

    def diff(self, sandbox_id: str, baseline: str) -> list["FileDiff"]:
        """Gibt Dateisystem-Diff seit baseline-Snapshot zurück."""
        ...

    def stop(self, sandbox_id: str) -> None:
        """Stoppt Sandbox (Prozesse beendet, Zustand erhalten)."""
        ...

    def cleanup(self, sandbox_id: str) -> None:
        """Entfernt Sandbox und alle temporären Daten."""
        ...
```

```python
@dataclass
class ExecResult:
    exit_code: int
    stdout: str           # ggf. gekürzt nach max_output_bytes
    stderr: str
    duration_seconds: float
    timed_out: bool
    cmd_hash: str         # sha256 von cmd (für Audit)

@dataclass
class FileDiff:
    path: str
    change_type: str      # "added" | "modified" | "deleted"
    content_hash_before: str | None
    content_hash_after: str | None
```

---

## Backends

| backend_id                 | Beschreibung                                    | Verfügbarkeit   |
|----------------------------|-------------------------------------------------|-----------------|
| local_process_restricted   | Subprocess mit eingeschränktem ENV/PATH         | Default, immer  |
| docker_container           | Docker-Container mit resource limits            | optional        |
| devcontainer               | Dev-Container-Spec (`.devcontainer/`)           | optional        |
| wsl_workspace              | WSL-Instanz als isolierter Workspace            | optional (WSL2) |
| FakeSandbox                | Stub für Tests — keine reale Ausführung         | nur Tests       |

`local_process_restricted` ist der Default. Docker und devcontainer werden nur genutzt,
wenn explizit in der Projektconfig aktiviert.

---

## SandboxConfig

```python
@dataclass
class SandboxConfig:
    backend_id: str                    # aus Backends-Tabelle
    max_cpu_seconds: int = 60          # CPU-Zeitlimit
    max_memory_mb: int = 512
    network: str = "none"              # "none" | "restricted" | "allowed"
    allowed_paths: list[str] = field(default_factory=list)
    env_allowlist: list[str] = field(default_factory=list)  # erlaubte ENV-Keys
    working_dir: str | None = None
    image: str | None = None           # nur für docker_container
```

Default: `network="none"`. Netzwerkzugriff muss explizit per Policy und SandboxConfig
freigegeben werden — kein automatischer Internetzugriff.

---

## Default-Verhalten

- `local_process_restricted` filtert ENV auf `env_allowlist` (default: leer außer PATH, HOME)
- Kein Netzwerk ohne `network != "none"` und Policy-Erlaubnis
- Kein Schreibzugriff außerhalb `allowed_paths`
- `max_cpu_seconds=60`, `max_memory_mb=512` als sichere Defaults

Es gibt keinen "privileged"-Modus ohne explizite Policy-Freigabe und Audit.

---

## Audit

Jeder `exec()`-Aufruf erzeugt einen Audit-Event:

```python
@dataclass
class SandboxAuditEvent:
    sandbox_id: str
    run_id: str
    cmd_hash: str          # sha256(cmd) — nicht der Befehl selbst im Log
    exit_code: int
    duration_seconds: float
    timed_out: bool
    policy_class: str      # Sandbox-Netzwerkklasse
    created_at: float
```

Befehlstext (`cmd`) wird nicht im Audit-Event gespeichert — nur der Hash.
Sensitive Befehle (erkannt durch Secret-Referenzen in Argumenten) werden vollständig
redigiert bevor ein Artefakt erstellt wird.

---

## FakeSandbox (Tests)

```python
class FakeSandbox:
    """Kein Subprocess — gibt konfigurierte ExecResults zurück."""

    def __init__(self, responses: dict[str, ExecResult]):
        self._responses = responses   # cmd_hash → ExecResult

    def exec(self, sandbox_id, cmd, timeout, env_override=None) -> ExecResult:
        key = hashlib.sha256(" ".join(cmd).encode()).hexdigest()
        if key in self._responses:
            return self._responses[key]
        raise UnknownCommandError(f"FakeSandbox: no response for {cmd}")
```

FakeSandbox wirft bei `copy_in`/`copy_out` per Default `NotImplementedError` —
Tests müssen explizit entscheiden, ob sie Dateizugriff simulieren wollen.

---

## Tests

| Testfall                                          | Erwartung                                          |
|---------------------------------------------------|----------------------------------------------------|
| exec() mit FakeSandbox (bekannter Befehl)         | Konfiguriertes ExecResult zurück                   |
| exec() mit unbekanntem Befehl in FakeSandbox      | UnknownCommandError                                |
| exec() überschreitet max_cpu_seconds              | ExecResult.timed_out=True, Prozess beendet         |
| exec() mit network_call in network="none"         | Policy-Verletzung, Audit-Event, kein Aufruf        |
| copy_in / copy_out außerhalb allowed_paths        | PermissionDenied                                   |
| Audit-Event nach exec()                           | cmd_hash, exit_code, duration vorhanden            |
| Docker-Backend ohne Docker-Daemon                 | BackendUnavailableError, Fallback auf local?       |
| cleanup() nach stop()                             | Keine temporären Dateien hinterlassen              |
