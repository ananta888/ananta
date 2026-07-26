from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SCRIPT = ROOT / "scripts/ollama-create-ananta-model-aliases.sh"
GEMMA_MODELFILE = (
    ROOT / "config/models/modelfiles/ananta-gemma4-reasoning-8k.Modelfile"
)


def _fake_ollama(tmp_path: Path, *, available: tuple[str, ...]) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls_path = tmp_path / "calls.log"
    available_path = tmp_path / "available.txt"
    available_path.write_text(
        "".join(f"{model}\n" for model in available),
        encoding="utf-8",
    )
    executable = bin_dir / "ollama"
    executable.write_text(
        """#!/bin/sh
set -eu
command="$1"
model="${2:-}"
printf '%s %s\\n' "$command" "$model" >> "$FAKE_OLLAMA_CALLS"
case "$command" in
  show)
    grep -Fx -- "$model" "$FAKE_OLLAMA_AVAILABLE" >/dev/null
    ;;
  pull)
    printf '%s\\n' "$model" >> "$FAKE_OLLAMA_AVAILABLE"
    ;;
  create)
    ;;
  *)
    exit 64
    ;;
esac
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return bin_dir, calls_path


def _run_bootstrap(
    tmp_path: Path,
    *,
    available: tuple[str, ...],
    offline: str,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir, calls_path = _fake_ollama(tmp_path, available=available)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "FAKE_OLLAMA_CALLS": str(calls_path),
        "FAKE_OLLAMA_AVAILABLE": str(tmp_path / "available.txt"),
        "OLLAMA_BOOTSTRAP_OFFLINE": offline,
    }
    result = subprocess.run(
        ["/bin/sh", str(BOOTSTRAP_SCRIPT)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    calls = (
        calls_path.read_text(encoding="utf-8").splitlines()
        if calls_path.exists()
        else []
    )
    return result, calls


def test_bootstrap_skips_pull_when_base_models_are_present(tmp_path: Path) -> None:
    result, calls = _run_bootstrap(
        tmp_path,
        available=("phi4-mini", "gemma4:e4b-it-qat"),
        offline="0",
    )

    assert result.returncode == 0, result.stderr
    assert not any(call.startswith("pull ") for call in calls)
    assert calls[-2:] == [
        "create ananta-phi4-mini-32k",
        "create ananta-gemma4-reasoning-8k",
    ]
    gemma_modelfile = GEMMA_MODELFILE.read_text(encoding="utf-8")
    assert "PARAMETER num_ctx 8192" in gemma_modelfile
    assert "PARAMETER top_k 64" in gemma_modelfile
    assert "PARAMETER top_p 0.95" in gemma_modelfile
    assert "<|think|>" not in gemma_modelfile


def test_bootstrap_pulls_only_missing_base_model(tmp_path: Path) -> None:
    result, calls = _run_bootstrap(
        tmp_path,
        available=("phi4-mini",),
        offline="false",
    )

    assert result.returncode == 0, result.stderr
    assert [call for call in calls if call.startswith("pull ")] == [
        "pull gemma4:e4b-it-qat"
    ]
    assert calls[-2:] == [
        "create ananta-phi4-mini-32k",
        "create ananta-gemma4-reasoning-8k",
    ]


def test_bootstrap_offline_mode_fails_before_alias_creation(
    tmp_path: Path,
) -> None:
    result, calls = _run_bootstrap(
        tmp_path,
        available=("phi4-mini",),
        offline="1",
    )

    assert result.returncode != 0
    assert "required base model missing in offline mode" in result.stderr
    assert not any(call.startswith("pull ") for call in calls)
    assert not any(call.startswith("create ") for call in calls)
