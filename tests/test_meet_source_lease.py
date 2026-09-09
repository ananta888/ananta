"""Both receive adapters preserve the same immutable source-lease policy."""

from copy import deepcopy

import pytest

from tests.test_meet_dialog_audio import runtime
from worker.meet_media.dialog_audio import AudioLease
from worker.meet_media.source_lease import SourceLease


@pytest.mark.parametrize("lease_type", [AudioLease, SourceLease])
def test_received_source_lease_copies_inputs_and_cannot_be_reopened(lease_type):
    _, receipt, service, payload = runtime()
    job = service.start(payload)["job"]
    binding = {"source": "microphone", "epoch": job["publication_epoch"]}
    lease = lease_type(binding, job)
    original = deepcopy(binding)
    lease.require(binding)
    binding["epoch"] += 1
    job["deadline"] += 30
    with pytest.raises(ValueError):
        lease.require(binding)
    lease.require(original)
    lease.close()
    lease.refresh(receipt, lease.job)
    with pytest.raises(ValueError):
        lease.require(original)


@pytest.mark.parametrize("lease_type", [AudioLease, SourceLease])
def test_changed_source_or_hub_job_revokes_both_adapters(lease_type):
    _, receipt, service, payload = runtime()
    job = service.start(payload)["job"]
    lease = lease_type("binding", job)
    receipt["publications"][0]["publicationEpoch"] += 1
    lease.refresh(receipt, job)
    with pytest.raises(ValueError):
        lease.require("binding")
