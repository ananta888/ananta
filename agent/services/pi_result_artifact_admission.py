"""Require exact Hub-admitted answer bytes under the existing Task transaction."""

import hashlib

from agent.db_models import ArtifactDB
from agent.services.native_artifact_result_admission import require_registered_native_artifacts
from ananta_contracts.pi_result_artifact import pi_answer_artifact


def require_pi_result_artifacts(*, session, task, command, candidate, authority):
    result = candidate.result
    admitted = require_registered_native_artifacts(session=session, task=task, command=command,
        result=result, authority=authority)
    if not admitted:
        return []
    declaration = pi_answer_artifact(command)
    content = result.output_data['output'].encode('utf-8')
    if declaration is None or len(admitted) != 1:
        raise ValueError('pi_result_artifact_answer_binding_invalid')
    artifact = session.get(ArtifactDB, admitted[0]['artifact_id'])
    provenance = artifact.artifact_metadata['native_artifact_ingress']
    if (provenance['filename'] != declaration.filename or provenance['media_type'] != declaration.media_type
            or provenance['size_bytes'] != len(content) or provenance['sha256'] != hashlib.sha256(content).hexdigest()):
        raise ValueError('pi_result_artifact_answer_binding_invalid')
    return admitted
