"""Closed Hub declaration for exporting one no-tools Pi assistant answer."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PiAnswerArtifact:
    output_name: str
    filename: str
    media_type: str


def pi_answer_artifact(command):
    raw = command.node.metadata.get('pi_result_artifact')
    outputs = command.node.output_artifacts
    if not outputs and raw is None:
        return None
    if (len(outputs) != 1 or not isinstance(raw, dict)
            or set(raw) != {'output_name', 'source', 'filename', 'media_type'}
            or raw['output_name'] != outputs[0] or raw['source'] != 'assistant_answer'
            or not isinstance(raw['media_type'], str)
            or raw['media_type'] not in {'text/plain', 'text/markdown'}
            or outputs[0] not in command.authorization.allowed_artifacts):
        raise ValueError('pi_result_artifact_declaration_invalid')
    name = raw['filename']
    if (not isinstance(name, str) or not name or name != name.strip() or name in {'.', '..'}
            or any(char in name for char in '/\\') or any(ord(char) < 32 or ord(char) == 127 for char in name)):
        raise ValueError('pi_result_artifact_declaration_invalid')
    try:
        if len(name.encode('utf-8')) > 255:
            raise ValueError('pi_result_artifact_declaration_invalid')
    except UnicodeEncodeError:
        raise ValueError('pi_result_artifact_declaration_invalid') from None
    return PiAnswerArtifact(outputs[0], name, raw['media_type'])
