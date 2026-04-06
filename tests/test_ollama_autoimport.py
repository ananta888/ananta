import hashlib
import re
import shlex
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "ollama-autoimport.sh"


def _sanitize(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"^-+|-+$", "", value)
    value = re.sub(r"-+", "-", value)
    return value


def _generated_name(file_path: str) -> str:
    relative = file_path.removeprefix("/models/")
    rel_path = Path(relative)
    base = _sanitize(rel_path.stem)
    parent = _sanitize(rel_path.parent.name)
    grandparent = _sanitize(rel_path.parent.parent.name)

    if parent in {"", "."}:
        return base
    if base == parent:
        return f"{grandparent}-{parent}" if grandparent not in {"", "."} else parent
    return f"{grandparent}-{parent}-{base}" if grandparent not in {"", "."} else f"{parent}-{base}"


def _expected_trimmed_name(file_path: str, max_len: int = 80) -> str:
    base_name = _generated_name(file_path)
    if len(base_name) <= max_len:
        return base_name

    digest = hashlib.sha256(base_name.encode("utf-8")).hexdigest()[:12]
    prefix_len = max(1, max_len - len(digest) - 1)
    prefix = re.sub(r"[-._]+$", "", base_name[:prefix_len]) or "m"
    return f"{prefix}-{digest}"


def _run_model_name(file_path: str, tmp_path: Path) -> str:
    command = (
        f". {shlex.quote(str(SCRIPT_PATH))}; "
        f"model_name {shlex.quote(file_path)}"
    )
    result = subprocess.run(
        ["bash", "-lc", command],
        check=True,
        capture_output=True,
        text=True,
        env={
            "OLLAMA_AUTOIMPORT_LIB_ONLY": "1",
            "AUTOIMPORT_STATE_DIR": str(tmp_path / "state"),
            "OLLAMA_MODEL_NAME_MAX_LEN": "80",
        },
    )
    return result.stdout.strip()


def _run_shell_function(command: str, tmp_path: Path) -> str:
    result = subprocess.run(
        ["bash", "-lc", command],
        check=True,
        capture_output=True,
        text=True,
        env={
            "OLLAMA_AUTOIMPORT_LIB_ONLY": "1",
            "AUTOIMPORT_STATE_DIR": str(tmp_path / "state"),
            "OLLAMA_MODEL_NAME_MAX_LEN": "80",
        },
    )
    return result.stdout.strip()


def test_model_name_keeps_valid_short_name(tmp_path: Path) -> None:
    file_path = "/models/lmstudio-community/Phi-4-mini-reasoning-GGUF/Phi-4-mini-reasoning-Q4_K_M.gguf"

    assert _run_model_name(file_path, tmp_path) == _expected_trimmed_name(file_path)


def test_model_name_trims_long_name_to_ollama_limit(tmp_path: Path) -> None:
    file_path = "/models/lmstudio-community/Qwen2.5-Coder-14B-Instruct-GGUF/Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf"

    actual = _run_model_name(file_path, tmp_path)
    expected = _expected_trimmed_name(file_path)

    assert actual == expected
    assert len(actual) <= 80
    assert len(_generated_name(file_path)) > 80


def test_model_name_trimming_is_deterministic_and_unique(tmp_path: Path) -> None:
    file_a = "/models/lmstudio-community/Qwen2.5-Coder-14B-Instruct-GGUF/Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf"
    file_b = "/models/lmstudio-community/Qwen2.5-Coder-0.5B-Instruct-GGUF/Qwen2.5-Coder-0.5B-Instruct-Q8_0.gguf"

    first = _run_model_name(file_a, tmp_path)
    second = _run_model_name(file_a, tmp_path)
    third = _run_model_name(file_b, tmp_path)

    assert first == second
    assert first != third
    assert len(first) <= 80
    assert len(third) <= 80


def test_resolve_configured_alias_source_prefers_first_available_candidate(tmp_path: Path) -> None:
    command = (
        f". {shlex.quote(str(SCRIPT_PATH))}; "
        "list_model_refs() { printf '%s\\n' "
        "'lmstudio-community-qwen2.5-coder-14b-instruct-gguf-qwen2.5-coder-14-081c3c49a2d2:latest' "
        "'bartowski-qwen2.5-coder-7b-instruct-gguf-qwen2.5-coder-7b-instruct-q4_k_s:latest'; }; "
        "resolve_configured_alias_source "
        "'bartowski-qwen2.5-coder-7b-instruct-gguf-qwen2.5-coder-7b-instruct-q4_k_s,lmstudio-community-qwen2.5-coder-14b-instruct-gguf-qwen2.5-coder-14-081c3c49a2d2'"
    )
    assert _run_shell_function(command, tmp_path) == (
        "bartowski-qwen2.5-coder-7b-instruct-gguf-qwen2.5-coder-7b-instruct-q4_k_s"
    )


def test_first_available_text_model_skips_non_text_entries(tmp_path: Path) -> None:
    command = (
        f". {shlex.quote(str(SCRIPT_PATH))}; "
        "list_model_refs() { printf '%s\\n' "
        "'lmstudio-community-gemma-4-e2b-it-gguf-mmproj-gemma-4-e2b-it-bf16:latest' "
        "'bartowski-mistralai_voxtral-mini-3b-2507-gguf-mistralai_voxtral-min-9e08d0b2625f:latest' "
        "'mradermacher-qwen2.5-coder-3b-instruct-distill-qwen3-coder-next-abl-0836a1d595c6:latest'; }; "
        "first_available_text_model"
    )
    assert _run_shell_function(command, tmp_path) == (
        "mradermacher-qwen2.5-coder-3b-instruct-distill-qwen3-coder-next-abl-0836a1d595c6"
    )
