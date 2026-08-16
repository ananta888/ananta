"""Real Temporal worker bound to the isolated example Hub HTTP port."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import timedelta

from example_public_material import (
    EXAMPLE_COMMAND_KEY_ID,
    EXAMPLE_KEY_ID,
    EXAMPLE_TASK_QUEUE,
    example_bearer,
    example_command_public_key,
    example_signing_key,
)
from temporalio.client import Client
from temporalio.worker import Worker

from ananta_contracts.runtime_authorization_crypto import Ed25519VerificationKeyRing
from worker.temporal.activities import HubActivityGateway, probe_activity
from worker.temporal.authorization import RuntimeAuthorizationVerifier
from worker.temporal.command_authority import (
    PublicKeyWorkflowCommandAuthorityVerifier,
    WorkflowCommandAuthorityActivity,
)
from worker.temporal.health import WorkerHealthServer, WorkerHealthState
from worker.temporal.hub_gateway import HttpHubTaskGateway
from worker.temporal.workflows import (
    AnantaWorkflow,
    TemporalProbeWorkflow,
    TemporalRecoveryProbeWorkflow,
)

LOGGER = logging.getLogger(__name__)


async def run(health: WorkerHealthState | None = None) -> None:
    address = str(os.getenv("ANANTA_TEMPORAL_ADDRESS") or "temporal:7233")
    namespace = str(os.getenv("ANANTA_TEMPORAL_NAMESPACE") or "default")
    hub_url = str(os.getenv("ANANTA_EXAMPLE_HUB_URL") or "http://workflow-runtime-example-hub:8080")
    client = await Client.connect(
        address,
        namespace=namespace,
        identity="ananta-workflow-runtime-example-worker",
    )
    gateway = HubActivityGateway(
        gateway=HttpHubTaskGateway(
            hub_url=hub_url,
            bearer_token=example_bearer(),
            timeout_seconds=5,
        ),
        authorization_verifier=RuntimeAuthorizationVerifier(
            keys={EXAMPLE_KEY_ID: example_signing_key()},
            active_key_id=EXAMPLE_KEY_ID,
        ),
        poll_seconds=0.1,
        activity_timeout_seconds=60,
    )
    # AnantaWorkflow verifies every non-legacy command through this Local
    # Activity.  Leaving it unregistered fails the update inside the workflow,
    # not at startup, so the approval and cancel drills die mid-run.  The worker
    # gets the public half only: it can verify a command but never issue one,
    # which is the whole point of signing commands asymmetrically.
    command_authority = WorkflowCommandAuthorityActivity(
        PublicKeyWorkflowCommandAuthorityVerifier(
            Ed25519VerificationKeyRing({EXAMPLE_COMMAND_KEY_ID: example_command_public_key()}),
        ),
    )
    worker = Worker(
        client,
        task_queue=EXAMPLE_TASK_QUEUE,
        workflows=[TemporalProbeWorkflow, TemporalRecoveryProbeWorkflow, AnantaWorkflow],
        activities=[probe_activity, gateway.execute, command_authority.verify],
        build_id="ananta-workflow-runtime-example-v1",
        identity="ananta-workflow-runtime-example-worker",
        graceful_shutdown_timeout=timedelta(seconds=5),
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    worker_task = asyncio.create_task(worker.run(), name="example-temporal-worker")
    stop_task = asyncio.create_task(stop.wait(), name="example-temporal-stop")
    if health is not None:
        health.ready()
    done, _ = await asyncio.wait({worker_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    if worker_task in done:
        exception = worker_task.exception()
        if exception is not None:
            raise exception
        return
    await worker.shutdown()
    await worker_task
    stop_task.cancel()
    await asyncio.gather(stop_task, return_exceptions=True)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    health = WorkerHealthState(
        build_id="ananta-workflow-runtime-example-v1",
        identity="ananta-workflow-runtime-example-worker",
    )
    server = WorkerHealthServer(host="0.0.0.0", port=8088, state=health)
    server.start()
    LOGGER.info("starting example-only Temporal worker on task queue %s", EXAMPLE_TASK_QUEUE)
    try:
        asyncio.run(run(health))
    except Exception as exc:
        health.degraded(f"example_temporal_worker_failed:{type(exc).__name__}")
        raise
    finally:
        health.stopped()
        server.close()


if __name__ == "__main__":
    main()


__all__ = ["main", "run"]
