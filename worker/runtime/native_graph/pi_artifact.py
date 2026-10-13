"""Export answer bytes through existing Hub artifact admission; no SDK writes."""

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from ananta_contracts.native_artifact_transfer import NativeArtifactBytes
from ananta_contracts.pi_result_artifact import pi_answer_artifact
from worker.runtime.native_graph.contracts import NativeNodeCommand


class PiAnswerPublicationPort(Protocol):
    def publish(self, answer: str) -> dict[str, str]: ...


class PiAnswerArtifactPort(Protocol):
    def prepare(self, *, command, task) -> PiAnswerPublicationPort: ...


class PiNativeArtifactPublisherPort(Protocol):
    def publish(self, *, command, hub_task_id, project_id, artifacts) -> dict[str, str]: ...


@dataclass(frozen=True)
class PreparedPiAnswerPublication:
    publisher: PiNativeArtifactPublisherPort
    command_json: str
    task_id: str
    project_id: str | None

    def publish(self, answer):
        if not isinstance(answer, str) or len(answer) > 16384:
            raise ValueError('pi_result_artifact_answer_invalid')
        command = NativeNodeCommand.from_mapping(json.loads(self.command_json))
        declaration = pi_answer_artifact(command)
        content = answer.encode('utf-8')
        if len(content) > 65536:
            raise ValueError('pi_result_artifact_answer_invalid')
        artifact = NativeArtifactBytes(declaration.output_name, declaration.filename,
            declaration.media_type, content, hashlib.sha256(content).hexdigest())
        return self.publisher.publish(command=command, hub_task_id=self.task_id,
            project_id=self.project_id, artifacts=(artifact,))


class PiAnswerArtifactPublisher:
    def __init__(self, *, publisher_factory):
        self._publisher_factory = publisher_factory

    def prepare(self, *, command, task):
        if pi_answer_artifact(command) is None:
            raise ValueError('pi_result_artifact_declaration_required')
        if not task.get('id') or task.get('tenant_id') != command.tenant_id:
            raise ValueError('pi_result_artifact_task_binding_invalid')
        return PreparedPiAnswerPublication(self._publisher_factory(),
            json.dumps(command.to_dict(), sort_keys=True, separators=(',', ':')),
            task['id'], task.get('project_id'))


def configured_pi_answer_publisher():
    from worker.runtime.native_graph.artifact_http import NativeArtifactHttpTransport
    from worker.runtime.native_graph.artifact_publication import NativeArtifactPublisher

    transport = NativeArtifactHttpTransport.from_environment()
    if transport is None:
        raise ValueError('pi_result_artifact_transport_unavailable')
    return NativeArtifactPublisher(transport=transport)
