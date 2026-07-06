from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from agent.services.sandbox_backend import SandboxBackend, SandboxConfig

from ..models import DetectorSignal, EvaluationStatus, TextQualityEvaluationRequest
from .avoid_ai_writing_contract import DETECTOR_SHA256, UPSTREAM_COMMIT, normalize_result


class AvoidAIWritingProvider:
    def __init__(
        self,
        *,
        sandbox: SandboxBackend,
        detector_path: str,
        detector_sha256: str = DETECTOR_SHA256,
        bridge_path: str = "scripts/avoid_ai_writing_detector_bridge.mjs",
        node_binary: str = "node",
        timeout: int = 20,
    ) -> None:
        self.sandbox = sandbox
        self.detector_path = Path(detector_path).resolve()
        self.detector_sha256 = detector_sha256
        self.bridge_path = Path(bridge_path).resolve()
        self.node_binary = Path(node_binary).name
        self.timeout = max(1, min(int(timeout), 60))

    def analyze(self, request: TextQualityEvaluationRequest) -> DetectorSignal:
        if not self.detector_path.is_file() or not self.bridge_path.is_file():
            return self._degraded("provider_unavailable")
        digest = hashlib.sha256(self.detector_path.read_bytes()).hexdigest()
        if digest != self.detector_sha256:
            return self._degraded("provider_checksum_mismatch")
        sandbox_id = self.sandbox.start(
            SandboxConfig(
                network="none",
                allowed_paths=[],
                env_allowlist=[],
                working_dir="/workspace",
            )
        )
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8") as input_file:
                json.dump(
                    {
                        "text": request.text,
                        "options": {
                            "contextMode": (
                                "technical"
                                if request.content_kind.value in {"technical_documentation", "structured_plan"}
                                else "general"
                            )
                        },
                    },
                    input_file,
                )
                input_file.flush()
                self.sandbox.copy_in(sandbox_id, self.bridge_path, "/workspace/bridge.mjs")
                self.sandbox.copy_in(sandbox_id, self.detector_path, "/workspace/patterns.js")
                self.sandbox.copy_in(sandbox_id, Path(input_file.name), "/workspace/input.json")
                result = self.sandbox.exec(
                    sandbox_id,
                    [self.node_binary, "/workspace/bridge.mjs"],
                    timeout=self.timeout,
                )
            if result.timeout:
                return self._degraded("provider_timeout")
            if result.exit_code != 0 or len(result.stdout.encode()) > 262144:
                return self._degraded("provider_invalid_response")
            payload = json.loads(result.stdout)
            signal = normalize_result(payload)
            signal.metadata.update(
                {
                    "upstream_commit": UPSTREAM_COMMIT,
                    "detector_sha256": digest,
                    "contract_version": "avoid-ai-writing-v1",
                }
            )
            return signal
        except (OSError, ValueError, json.JSONDecodeError):
            return self._degraded("provider_invalid_response")
        finally:
            self.sandbox.stop(sandbox_id)
            self.sandbox.cleanup(sandbox_id)

    @staticmethod
    def _degraded(reason: str) -> DetectorSignal:
        return DetectorSignal(
            provider_name="avoid_ai_writing",
            provider_version=UPSTREAM_COMMIT,
            reason_codes=[reason],
            status=EvaluationStatus.DEGRADED,
            degraded_reason=reason,
        )
