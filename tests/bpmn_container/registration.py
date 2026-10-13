"""Synthetic registered-Worker admission fixture; private provisioning stays with the Hub."""

import hashlib
import json
import os
import secrets
from pathlib import Path

from bpmn_container.transport import WORKER_ID, WORKER_URL


def register_worker(app, agents):
    from agent.db_models import AgentInfoDB
    from agent.services.workflow_worker_service_auth import (
        STRICT_WORKER_REGISTRATION_PROVENANCE,
        WORKER_REGISTRATION_KEYRING_SCHEMA,
        validate_strict_worker_registration,
    )

    registration = secrets.token_urlsafe(48)
    token = os.environ["BPMN_WORKER_TOKEN"]
    capabilities = ["workflow.adapter.native"]
    path = Path("/tmp/registration-keyring.json")
    with path.open("x", encoding="utf-8") as output:
        json.dump(
            {
                "schema": WORKER_REGISTRATION_KEYRING_SCHEMA,
                "workers": {
                    WORKER_ID: {
                        "worker_url": WORKER_URL,
                        "registration_token": registration,
                        "service_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                        "session_signing_key_sha256": hashlib.sha256(secrets.token_bytes(48)).hexdigest(),
                        "allowed_capabilities": capabilities,
                    }
                },
            },
            output,
        )
    path.chmod(0o600)
    app.config["ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE"] = str(path)
    credential = validate_strict_worker_registration(
        {
            "name": WORKER_ID,
            "url": WORKER_URL,
            "role": "worker",
            "token": token,
            "registration_token": registration,
            "capabilities": capabilities,
        },
        registered_agents=(),
        hub_service_token=None,
        config=app.config,
    )
    return agents.save(
        AgentInfoDB(
            url=credential.worker_url,
            name=credential.worker_id,
            token=token,
            role="worker",
            status="online",
            registration_validated=True,
            registration_provenance=STRICT_WORKER_REGISTRATION_PROVENANCE,
            capabilities=capabilities,
            authorized_capabilities=list(credential.allowed_capabilities),
        )
    )

