"""Dedicated Temporal worker process entrypoint."""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import timedelta

from temporalio.client import Client
from temporalio.worker import Worker

from ananta_contracts.runtime_authorization_crypto import (
    ED25519_VERIFICATION_KEYRING_SCHEMA,
    Ed25519VerificationKeyRing,
)
from worker.temporal.activities import HubActivityGateway, probe_activity
from worker.temporal.authorization import RuntimeAuthorizationVerifier
from worker.temporal.command_authority import (
    PublicKeyWorkflowCommandAuthorityVerifier,
    WorkflowCommandAuthorityActivity,
)
from worker.temporal.config import TemporalWorkerConfig
from worker.temporal.health import WorkerHealthServer, WorkerHealthState
from worker.temporal.hub_gateway import HttpHubTaskGateway, UnavailableHubTaskGateway
from worker.temporal.retry_profiles import validate_all_retry_profiles
from worker.temporal.workflows import (
    AnantaWorkflow,
    TemporalProbeWorkflow,
    TemporalRecoveryProbeWorkflow,
)

LOGGER = logging.getLogger(__name__)


async def connect_client(config: TemporalWorkerConfig) -> Client:
    return await Client.connect(
        config.address,
        namespace=config.namespace,
        api_key=config.read_api_key(),
        tls=config.temporal_tls_config(),
        identity=config.identity,
    )


def build_activity_gateway(config: TemporalWorkerConfig) -> HubActivityGateway:
    keyring = config.read_authorization_keyring()
    verifier = (
        RuntimeAuthorizationVerifier.from_config_mapping(
            keyring,
            allow_legacy_hmac=config.allow_legacy_hmac_keyring,
        )
        if keyring
        else None
    )
    if config.productive_gateway_configured:
        gateway = HttpHubTaskGateway(
            hub_url=config.hub_url,
            bearer_token=str(config.read_hub_token() or ""),
            service_id=config.workflow_service_id,
        )
    else:
        gateway = UnavailableHubTaskGateway()
    return HubActivityGateway(
        gateway=gateway,
        authorization_verifier=verifier,
        poll_seconds=config.hub_poll_seconds,
        activity_timeout_seconds=config.hub_activity_timeout_seconds,
    )


def build_command_authority_activity(config: TemporalWorkerConfig) -> WorkflowCommandAuthorityActivity:
    keyring = config.read_authorization_keyring()
    if not keyring or str(keyring.get("schema") or "") != ED25519_VERIFICATION_KEYRING_SCHEMA:
        return WorkflowCommandAuthorityActivity()
    verification_key_ring = Ed25519VerificationKeyRing.from_mapping(keyring)
    return WorkflowCommandAuthorityActivity(PublicKeyWorkflowCommandAuthorityVerifier(verification_key_ring))


def build_worker(config: TemporalWorkerConfig, client: Client) -> Worker:
    validate_all_retry_profiles()
    gateway = build_activity_gateway(config)
    command_authority = build_command_authority_activity(config)
    return Worker(
        client,
        task_queue=config.task_queue,
        workflows=[TemporalProbeWorkflow, TemporalRecoveryProbeWorkflow, AnantaWorkflow],
        activities=[probe_activity, gateway.execute, command_authority.verify],
        build_id=config.build_id,
        identity=config.identity,
        use_worker_versioning=config.use_worker_versioning,
        max_concurrent_workflow_tasks=config.max_concurrent_workflow_tasks,
        max_concurrent_activities=config.max_concurrent_activities,
        graceful_shutdown_timeout=timedelta(seconds=config.graceful_shutdown_seconds),
    )


async def run_worker(
    config: TemporalWorkerConfig,
    *,
    health: WorkerHealthState,
    stop_event: asyncio.Event | None = None,
) -> None:
    stop = stop_event or asyncio.Event()
    loop = asyncio.get_running_loop()
    if stop_event is None:
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(signum, stop.set)
            except (NotImplementedError, RuntimeError):
                pass

    try:
        client = await connect_client(config)
        worker = build_worker(config, client)
    except Exception as exc:
        health.degraded(f"temporal_worker_start_failed:{type(exc).__name__}")
        raise

    worker_task = asyncio.create_task(worker.run(), name="temporal-worker")
    stop_task = asyncio.create_task(stop.wait(), name="temporal-worker-stop")
    health.ready()
    try:
        done, _ = await asyncio.wait(
            {worker_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if worker_task in done:
            exception = worker_task.exception()
            if exception is not None:
                health.degraded(f"temporal_worker_failed:{type(exception).__name__}")
                raise exception
            return
        health.draining()
        await worker.shutdown()
        try:
            await asyncio.wait_for(worker_task, timeout=config.graceful_shutdown_seconds + 5)
        except TimeoutError:
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
            raise RuntimeError("temporal_worker_shutdown_timeout")
    finally:
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)
        health.stopped()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = TemporalWorkerConfig.from_env()
    health = WorkerHealthState(build_id=config.build_id, identity=config.identity)
    server = WorkerHealthServer(host=config.health_host, port=config.health_port, state=health)
    server.start()
    LOGGER.info(
        "Temporal worker starting namespace=%s task_queue=%s build_id=%s identity=%s productive_gateway=%s",
        config.namespace,
        config.task_queue,
        config.build_id,
        config.identity,
        config.productive_gateway_configured,
    )
    try:
        asyncio.run(run_worker(config, health=health))
    finally:
        server.close()


if __name__ == "__main__":
    main()


__all__ = [
    "build_activity_gateway",
    "build_command_authority_activity",
    "build_worker",
    "connect_client",
    "main",
    "run_worker",
]
