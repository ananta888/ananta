"""Non-exportable runtime enrollment key provider port and TPM2 adapter."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

_TPM_HANDLE = re.compile(r"^(?:0x[0-9a-fA-F]{8}|/run/ananta-tpm/[A-Za-z0-9._-]{1,128})$")


class KeyProviderUnavailable(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class NonExportableKeyProvider(Protocol):
    @property
    def algorithm(self) -> str: ...

    @property
    def key_id(self) -> str: ...

    def public_key_pem(self) -> str: ...

    def sign(self, message: bytes) -> bytes: ...


class CommandRunner(Protocol):
    def __call__(self, command: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess: ...


def _run(command: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
    )


class Tpm2KeyProvider:
    """Uses an opaque TPM persistent handle; private material is never readable."""

    def __init__(
        self,
        key_handle: str,
        algorithm: str,
        *,
        runner: CommandRunner = _run,
        timeout_seconds: float = 5.0,
    ) -> None:
        if not _TPM_HANDLE.fullmatch(key_handle):
            raise KeyProviderUnavailable("tpm2_key_handle_invalid")
        if algorithm not in {"ECDSA-SHA256", "RSA-PSS-SHA256"}:
            raise KeyProviderUnavailable("tpm2_key_algorithm_unsupported")
        self._key_handle = key_handle
        self._algorithm = algorithm
        self._runner = runner
        self._timeout = timeout_seconds

    @property
    def algorithm(self) -> str:
        return self._algorithm

    @property
    def key_id(self) -> str:
        return f"tpm2:{self._key_handle}"

    def public_key_pem(self) -> str:
        with tempfile.TemporaryDirectory(prefix="ananta-tpm-public-") as directory:
            output = Path(directory) / "public.pem"
            self._execute(
                ["tpm2_readpublic", "-Q", "-c", self._key_handle, "-f", "pem", "-o", str(output)]
            )
            try:
                pem = output.read_bytes()
                key = serialization.load_pem_public_key(pem)
            except (OSError, ValueError) as exc:
                raise KeyProviderUnavailable("tpm2_public_key_invalid") from exc
            if self._algorithm == "ECDSA-SHA256" and not isinstance(key, ec.EllipticCurvePublicKey):
                raise KeyProviderUnavailable("tpm2_public_key_algorithm_mismatch")
            if self._algorithm == "RSA-PSS-SHA256" and not isinstance(key, rsa.RSAPublicKey):
                raise KeyProviderUnavailable("tpm2_public_key_algorithm_mismatch")
            return pem.decode("ascii")

    def sign(self, message: bytes) -> bytes:
        if not isinstance(message, bytes) or not message:
            raise KeyProviderUnavailable("tpm2_signing_message_invalid")
        if len(message) > 65_536:
            raise KeyProviderUnavailable("tpm2_signing_message_oversize")
        with tempfile.TemporaryDirectory(prefix="ananta-tpm-sign-") as directory:
            input_path = Path(directory) / "message.bin"
            output_path = Path(directory) / "signature.bin"
            input_path.write_bytes(message)
            command = [
                "tpm2_sign",
                "-Q",
                "-c",
                self._key_handle,
                "-g",
                "sha256",
                "-f",
                "der",
                "-o",
                str(output_path),
            ]
            if self._algorithm == "RSA-PSS-SHA256":
                command.extend(["-s", "rsapss"])
            command.append(str(input_path))
            self._execute(command)
            try:
                signature = output_path.read_bytes()
            except OSError as exc:
                raise KeyProviderUnavailable("tpm2_signature_unavailable") from exc
            if not signature or len(signature) > 16_384:
                raise KeyProviderUnavailable("tpm2_signature_invalid")
            return signature

    def _execute(self, command: Sequence[str]) -> None:
        try:
            result = self._runner(command, timeout=self._timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            raise KeyProviderUnavailable("tpm2_provider_unavailable") from exc
        if result.returncode != 0:
            raise KeyProviderUnavailable("tpm2_provider_command_failed")


def key_provider_from_environment(
    environment: Mapping[str, str] | None = None,
) -> NonExportableKeyProvider:
    env = environment or os.environ
    if env.get("ANANTA_SFU_RUNTIME_CONTROL_MODE") != "authenticated_runtime_extension":
        raise KeyProviderUnavailable("runtime_extension_mode_not_selected")
    provider = str(env.get("ANANTA_SFU_KEY_PROVIDER") or "").strip().lower()
    if provider != "tpm2":
        raise KeyProviderUnavailable("requested_key_provider_unsupported")
    return Tpm2KeyProvider(
        str(env.get("ANANTA_SFU_TPM2_KEY_HANDLE") or ""),
        str(env.get("ANANTA_SFU_TPM2_ALGORITHM") or ""),
    )


__all__ = [
    "KeyProviderUnavailable",
    "NonExportableKeyProvider",
    "Tpm2KeyProvider",
    "key_provider_from_environment",
]
