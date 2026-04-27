from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.database import init_db
from agent.services.ingestion_service import IngestionService
from agent.services.rag_helper_index_service import RagHelperIndexService

PROJECT_ROOT = REPO_ROOT / "tests" / "fixtures" / "mini_coding_project"
JAVA_FILES = ["SecurityController.java", "TokenVerifier.java", "PolicyService.java"]
TOKEN = "evidence-agent-token-with-sufficient-length-1234567890"
HUB_URL = "http://127.0.0.1:5861"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(
        f"{HUB_URL}{path}",
        json=payload,
        headers={"Authorization": f"Bearer {TOKEN}"},
        timeout=30,
    )
    data = response.json() if response.text else {}
    if response.status_code >= 400:
        raise RuntimeError(f"POST {path} failed: {response.status_code} {data}")
    return {"status_code": response.status_code, "json": data}


def _get(path: str) -> dict[str, Any]:
    response = requests.get(f"{HUB_URL}{path}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30)
    data = response.json() if response.text else {}
    if response.status_code >= 400:
        raise RuntimeError(f"GET {path} failed: {response.status_code} {data}")
    return {"status_code": response.status_code, "json": data}


def _wait_for_hub(timeout: int = 30) -> None:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            _get("/tasks/orchestration/read-model")
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"Hub did not become ready: {last_error}")


def _build_rag_context(out_dir: Path) -> dict[str, Any]:
    # Uses the real RagHelperIndexService in the same process before the hub starts.
    # The generated context is then passed into the real Hub task payload.
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{out_dir / 'preindex.db'}")
    os.environ.setdefault("CONTROLLER_URL", "http://mock-controller")
    os.environ.setdefault("AGENT_NAME", "evidence-preindex")
    os.environ.setdefault("INITIAL_ADMIN_USER", "admin")
    os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "admin")
    init_db()
    records = []
    for filename in JAVA_FILES:
        source = PROJECT_ROOT / "src" / "main" / "java" / "example" / "security" / filename
        artifact, _version, _collection = IngestionService().upload_artifact(
            filename=filename,
            content=source.read_bytes(),
            created_by="evidence",
            media_type="text/x-java-source",
        )
        _knowledge_index, run = RagHelperIndexService().index_artifact(
            artifact.id,
            created_by="evidence",
            profile_name="deep_code",
        )
        output_dir = Path(str(run.output_dir))
        target = out_dir / "preindex-rag-helper" / filename
        target.mkdir(parents=True, exist_ok=True)
        for file_name in ["manifest.json", "index.jsonl"]:
            src = output_dir / file_name
            if src.exists():
                (target / file_name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        records.extend(_read_jsonl(output_dir / "index.jsonl"))
    context = {
        "index_kind": "real_rag_helper_index_service",
        "profile_name": "deep_code",
        "record_count": len(records),
        "records_preview": records[:12],
    }
    _write_json(out_dir / "preindex-rag-helper" / "rag-context.json", context)
    return context


def _start_hub(out_dir: Path) -> subprocess.Popen[str]:
    data_dir = out_dir / "hub-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    log = (out_dir / "hub.log").open("w", encoding="utf-8")
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{data_dir / 'hub.db'}"}
    return subprocess.Popen(
        [
            sys.executable,
            "scripts/evidence_hub_server.py",
            "--host",
            "127.0.0.1",
            "--port",
            "5861",
            "--data-dir",
            str(data_dir),
            "--token",
            TOKEN,
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _run_worker(engine: str, task_id: str, out_dir: Path, *, require_opencode: bool) -> dict[str, Any]:
    worker_out = out_dir / "workers" / engine
    env = {**os.environ}
    if require_opencode:
        env["RUN_LIVE_OPENCODE_TESTS"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/evidence_worker_runtime.py",
            "--hub-url",
            HUB_URL,
            "--token",
            TOKEN,
            "--task-id",
            task_id,
            "--engine",
            engine,
            "--out",
            str(worker_out),
            "--agent-url",
            f"http://evidence-worker-{engine}:5001",
            "--timeout",
            "30",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
        check=False,
    )
    (worker_out / "process-stdout.txt").parent.mkdir(parents=True, exist_ok=True)
    (worker_out / "process-stdout.txt").write_text(result.stdout, encoding="utf-8")
    (worker_out / "process-stderr.txt").write_text(result.stderr, encoding="utf-8")
    summary_path = worker_out / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    summary.update({"process_returncode": result.returncode})
    _write_json(worker_out / "process-summary.json", summary)
    if result.returncode != 0:
        raise RuntimeError(f"Worker {engine} failed with {result.returncode}: {result.stderr or result.stdout}")
    return summary


def run(out_dir: Path, *, require_opencode: bool) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rag_context = _build_rag_context(out_dir)
    hub = _start_hub(out_dir)
    try:
        _wait_for_hub()
        context_text = (
            "Reference profile: ref.java.keycloak\n"
            "Reference repo: keycloak/keycloak\n"
            "Boundary: guidance_not_clone; no blind copy.\n\n"
            f"RAG helper context:\n{json.dumps(rag_context, indent=2, sort_keys=True)}"
        )
        task_ids = {
            "ananta_native": "task-real-worker-ananta-native",
            "opencode": "task-real-worker-opencode",
        }
        ingest_results = {}
        for engine, task_id in task_ids.items():
            ingest_results[engine] = _post(
                "/tasks/orchestration/ingest",
                {
                    "id": task_id,
                    "title": f"Real worker runtime evidence for {engine}",
                    "description": f"Run real worker process with engine={engine} using rag-helper context.",
                    "source": "real-worker-runtime-evidence",
                    "created_by": "evidence",
                    "priority": "high",
                    "task_kind": "coding",
                    "required_capabilities": ["coding", "java", "security", "rag_helper", engine],
                    "worker_execution_context": {
                        "context": {
                            "context_text": context_text,
                            "reference_profile_id": "ref.java.keycloak",
                            "rag_index_kind": "real_rag_helper_index_service",
                            "worker_engine": engine,
                        },
                        "expected_output_schema": {
                            "type": "object",
                            "required": ["engine", "status", "execution_mode"],
                        },
                    },
                },
            )
        _write_json(out_dir / "orchestration" / "ingest-results.json", ingest_results)
        worker_results = {
            "ananta_native": _run_worker("ananta_native", task_ids["ananta_native"], out_dir, require_opencode=False),
            "opencode": _run_worker("opencode", task_ids["opencode"], out_dir, require_opencode=require_opencode),
        }
        read_model = _get("/tasks/orchestration/read-model")
        _write_json(out_dir / "orchestration" / "read-model.json", read_model)
        summary = {
            "status": "completed",
            "real_hub_http_server": True,
            "real_worker_processes": True,
            "real_opencode_cli_required": require_opencode,
            "rag_context": {
                "index_kind": rag_context["index_kind"],
                "record_count": rag_context["record_count"],
            },
            "worker_results": worker_results,
            "checks": {
                "ananta_native_worker_process_completed": worker_results["ananta_native"].get("engine_result_status") == "completed",
                "opencode_worker_process_completed": worker_results["opencode"].get("engine_result_status") == "completed",
                "opencode_real_cli": worker_results["opencode"].get("real_opencode_cli") is True,
                "read_model_has_completed_tasks": read_model["json"]["data"].get("queue", {}).get("completed", 0) >= 2,
            },
        }
        _write_json(out_dir / "evidence-summary.json", summary)
        (out_dir / "README.md").write_text(
            "# Real Worker Runtime Evidence\n\n"
            "This artifact proves the runtime path with a real local Hub HTTP server and separate worker processes.\n\n"
            "Flow:\n"
            "1. Real `RagHelperIndexService` pre-indexes Java fixture artifacts.\n"
            "2. A local Hub HTTP server is started.\n"
            "3. Two tasks are ingested into the Hub.\n"
            "4. A separate worker process claims and completes the `ananta_native` task.\n"
            "5. A separate worker process claims and completes the `opencode` task.\n"
            "6. The OpenCode worker invokes the real OpenCode CLI in safe help/smoke mode.\n\n"
            "See `evidence-summary.json`, `workers/*/`, `orchestration/`, and `preindex-rag-helper/`.\n",
            encoding="utf-8",
        )
        failed = [name for name, ok in summary["checks"].items() if not ok]
        if failed:
            raise SystemExit(f"Evidence checks failed: {failed}")
    finally:
        if hub.poll() is None:
            hub.send_signal(signal.SIGTERM)
            try:
                hub.wait(timeout=10)
            except subprocess.TimeoutExpired:
                hub.kill()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="ci-artifacts/real-worker-runtime-evidence")
    parser.add_argument("--require-opencode", action="store_true")
    args = parser.parse_args()
    run(Path(args.out), require_opencode=args.require_opencode)
