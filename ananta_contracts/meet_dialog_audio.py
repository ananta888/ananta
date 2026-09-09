"""Closed execution projection for one Hub-reserved source-bound ASR task."""

import re

from ananta_contracts.meet_audio_profile import parse_audio_profile

PUBLICATION_ID = re.compile(r"[A-Za-z0-9_={}:-]{1,128}")


def validate_audio_job(job, now):
    fields = {
        "task_id",
        "lease_id",
        "issued_at",
        "deadline",
        "meet_session_id",
        "generation",
        "membership_epoch",
        "receive_revision",
        "peer_id",
        "own_peer_id",
        "publication_id",
        "publication_epoch",
        "source",
        "control_revision",
    }
    if not isinstance(job, dict) or set(job) - {"audio_profile"} != fields:
        raise ValueError("meet_audio_job_invalid")
    if "audio_profile" in job:
        parse_audio_profile(job["audio_profile"])
    for field in ("task_id", "lease_id"):
        if not isinstance(job[field], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", job[field]):
            raise ValueError("meet_audio_job_invalid")
    if not isinstance(job["publication_id"], str) or not PUBLICATION_ID.fullmatch(job["publication_id"]):
        raise ValueError("meet_audio_job_invalid")
    for field in (
        "generation",
        "membership_epoch",
        "receive_revision",
        "publication_epoch",
        "issued_at",
        "deadline",
        "control_revision",
    ):
        if type(job[field]) is not int or not 1 <= job[field] < 2**53:
            raise ValueError("meet_audio_job_invalid")
    if (
        not now - 30 < job["issued_at"] <= now + 2
        or not now < job["deadline"] <= job["issued_at"] + 30
        or not isinstance(job["meet_session_id"], str)
        or not re.fullmatch(r"ms_[A-Za-z0-9_-]{32}", job["meet_session_id"])
        or any(
            not isinstance(job[k], str) or not re.fullmatch(r"[a-f0-9]{16}", job[k]) for k in ("peer_id", "own_peer_id")
        )
        or job["peer_id"] == job["own_peer_id"]
        or job["source"] not in ("microphone", "screen-audio")
    ):
        raise ValueError("meet_audio_job_invalid")
    return job


def audio_job_current(job, receipt, now):
    return (
        job["meet_session_id"] == receipt["lease"]["sessionId"]
        and job["generation"] == receipt["lease"]["generation"]
        and now < job["deadline"]
        and job["membership_epoch"] == receipt["membershipEpoch"]
        and job["receive_revision"] == receipt["receiveRevision"]
        and job["own_peer_id"] == receipt["peerId"]
        and any(
            p
            == {
                "peerId": job["peer_id"],
                "publicationId": job["publication_id"],
                "source": job["source"],
                "publicationEpoch": job["publication_epoch"],
            }
            for p in receipt["publications"]
        )
    )
