"""Closed execution projection for one Hub-reserved source-bound ASR task."""

from ananta_contracts.meet_audio_profile import parse_audio_profile
from ananta_contracts.meet_source_job import PUBLICATION_ID as PUBLICATION_ID
from ananta_contracts.meet_source_job import SOURCE_JOB_FIELDS, validate_source_job
from ananta_contracts.meet_source_job import source_job_current as audio_job_current

__all__ = ["PUBLICATION_ID", "validate_audio_job", "audio_job_current"]


def validate_audio_job(job, now):
    fields = SOURCE_JOB_FIELDS | ({"audio_profile"} if isinstance(job, dict) and "audio_profile" in job else set())
    validate_source_job(job, now, fields=fields, sources=("microphone", "screen-audio"), error="meet_audio_job_invalid")
    if "audio_profile" in job:
        parse_audio_profile(job["audio_profile"])
    return job
