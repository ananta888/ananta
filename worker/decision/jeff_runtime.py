"""Offline, authenticated wrapper around the pinned upstream Jeff service."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from pathlib import Path


def verify_weights(root: Path, manifest: dict) -> None:
    expected_manifest = "ebb5633965a516fd2b351155ce533c3727e27d968e3c6188e38d605820f94ca1"
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if not hmac.compare_digest(digest, expected_manifest):
        raise ValueError("jeff_model_manifest_unverified")
    root = root.resolve(strict=True)
    if manifest.get("model_revision") != "d0a4e53d09cebe6bc963dd9be319d4279084bb2d":
        raise ValueError("jeff_model_revision_invalid")
    for name, expected in manifest["files"].items():
        path = root / name
        if path.is_symlink() or path.resolve(strict=True).parent != root or not path.is_file():
            raise ValueError("jeff_model_path_invalid")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if not hmac.compare_digest(digest.hexdigest(), expected):
            raise ValueError("jeff_model_digest_mismatch")


def conservative_token_bound(payload: dict) -> int:
    """Byte upper bound for the pinned tokenizer plus schema/special-token overhead.

    Deliberately conservative. It counts labels/instructions as well as state;
    the character limit alone is never used as an encoder-token guarantee.
    """
    if set(payload) != {"state", "model", "questions"}:
        raise ValueError("jeff_request_fields_invalid")
    questions = payload.get("questions", {})
    if not isinstance(questions, dict) or not 1 <= len(questions) <= 32:
        raise ValueError("jeff_questions_invalid")
    rendered = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    label_count = 0
    for entry in questions.values():
        if not isinstance(entry, dict):
            raise ValueError("jeff_questions_invalid")
        if set(entry) - {"type", "instructions", "criteria"}:
            raise ValueError("jeff_question_fields_invalid")
        criteria = entry.get("criteria", {})
        if not isinstance(criteria, (dict, list)) or len(criteria) > 64:
            raise ValueError("jeff_labels_invalid")
        label_count += len(criteria)
    return len(rendered.encode()) + 128 * (label_count + len(questions))


def create_app():
    from fastapi.responses import JSONResponse
    from jeff.server.app import create_app as upstream_app
    from jeff.server.config import Settings

    secret_file = Path(os.environ.get("JEFF_API_KEY_FILE", "/run/secrets/jeff_api_key"))
    if not secret_file.is_file() or secret_file.stat().st_size > 4096:
        raise ValueError("jeff_api_key_missing")
    key = secret_file.read_text().strip()
    if len(key) < 16 or any(c.isspace() for c in key):
        raise ValueError("jeff_api_key_invalid")
    root = Path(os.environ.get("JEFF_MODEL", "/models/gliformer-large-v1"))
    manifest_path = Path(os.environ.get("JEFF_MODEL_MANIFEST", "/app/worker/decision/jeff-model-manifest.json"))
    verify_weights(root, json.loads(manifest_path.read_text()))
    if os.environ.get("HF_HUB_OFFLINE") != "1" or os.environ.get("TRANSFORMERS_OFFLINE") != "1":
        raise ValueError("jeff_offline_policy_required")
    settings = Settings()
    # The executed path must be exactly the inventory verified above, including
    # when JEFF_MODEL was omitted and upstream defaults differ from this wrapper.
    settings.model_path = str(root)
    if getattr(settings, "backend", "torch") != "torch":
        raise ValueError("jeff_backend_not_verified")
    settings.api_keys = [key]
    settings.isolate = "all"
    settings.max_questions = 32
    settings.max_labels_per_question = 64
    settings.max_state_chars = 8000
    settings.device = os.environ.get("JEFF_DEVICE", "cpu")
    if settings.device not in {"cpu", "cuda"} or not 1 <= settings.max_queue <= 256:
        raise ValueError("jeff_runtime_config_invalid")
    app = upstream_app(settings)
    require_assignment = os.environ.get("JEFF_REQUIRE_ASSIGNMENT", "1") == "1"
    public_key = None
    if require_assignment:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        public_key_path = Path(os.environ.get("JEFF_HUB_PUBLIC_KEY_FILE", "/run/secrets/jeff_hub_public_key"))
        if not public_key_path.is_file() or public_key_path.stat().st_size > 4096:
            raise ValueError("jeff_hub_issuer_key_unavailable")
        public_key = load_pem_public_key(public_key_path.read_bytes())
        if not isinstance(public_key, Ed25519PublicKey):
            raise ValueError("jeff_hub_issuer_key_invalid")
    used_assignments = {}
    max_tokens = int(os.environ.get("JEFF_ENCODER_TOKEN_LIMIT", "4096"))
    if not 256 <= max_tokens <= 16384:
        raise ValueError("jeff_encoder_limit_invalid")

    @app.middleware("http")
    async def closed_ingress(request, call_next):
        assignment_digest = None
        deadline_seconds = 30
        if request.url.path != "/healthz" and not hmac.compare_digest(
            request.headers.get("authorization", ""), "Bearer " + key
        ):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if request.url.path == "/v1/systemone":
            try:
                length = int(request.headers.get("content-length", "-1"))
            except ValueError:
                length = -1
            if request.headers.get("transfer-encoding") or not 0 <= length <= 32768:
                return JSONResponse({"error": "bounded_content_length_required"}, status_code=413)
            body = await request.body()
            if len(body) != length:
                return JSONResponse({"error": "request_size_mismatch"}, status_code=400)
            try:
                payload = json.loads(body)
                if not isinstance(payload, dict) or conservative_token_bound(payload) > max_tokens:
                    raise ValueError("token_limit")
            except (ValueError, TypeError, RecursionError):
                return JSONResponse({"error": "invalid_request_or_encoder_limit"}, status_code=422)
            if require_assignment:
                from ananta_contracts.specialist_inference_assignment import verify_assignment

                token = request.headers.get("x-ananta-assignment", "")
                try:
                    bound = verify_assignment(
                        token, public_key, body=payload, model_revision="d0a4e53d09cebe6bc963dd9be319d4279084bb2d"
                    )
                except ValueError:
                    return JSONResponse({"error": "assignment_unverified"}, status_code=403)
                assignment_digest = hashlib.sha256(token.encode()).hexdigest()
                replay_key = bound["evidence"]["binding_digest"]
                epoch = time.time()
                for digest in tuple(used_assignments):
                    if used_assignments[digest] <= epoch:
                        del used_assignments[digest]
                if replay_key in used_assignments:
                    return JSONResponse({"error": "assignment_replay"}, status_code=409)
                if len(used_assignments) >= 256:
                    return JSONResponse({"error": "assignment_capacity"}, status_code=529)
                used_assignments[replay_key] = bound["expires_at"]
                deadline_seconds = min(30, bound["expires_at"] - epoch)
        try:
            response = await asyncio.wait_for(call_next(request), timeout=deadline_seconds)
        except asyncio.TimeoutError:
            return JSONResponse({"error": "deadline_exceeded"}, status_code=504)
        except Exception as error:
            # A failed one-shot assignment is never replayed implicitly. Keep
            # allocator failures bounded and never echo model/input diagnostics.
            exhausted = isinstance(error, MemoryError) or type(error).__name__ == "OutOfMemoryError"
            return JSONResponse(
                {"error": "runtime_memory_exhausted" if exhausted else "inference_failed"},
                status_code=529 if exhausted else 500,
            )
        if response.status_code == 422:
            # Upstream validation echoes input. Never return prompts/PII in an
            # error envelope, which is commonly logged by clients and proxies.
            return JSONResponse({"error": "invalid_request"}, status_code=422)
        response.headers["x-ananta-model-revision"] = "d0a4e53d09cebe6bc963dd9be319d4279084bb2d"
        if assignment_digest:
            response.headers["x-ananta-assignment-digest"] = assignment_digest
        return response

    return app


def main():
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=8000, access_log=False)


if __name__ == "__main__":
    main()
