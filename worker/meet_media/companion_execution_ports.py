"""Closed local execution-port frames; no HTTP credentials or task ownership."""

import json

PORT_LIMIT = 32_768
MODEL_REQUEST_LIMIT = 128_000
MAX_PORT_CALLS = 32
EXECUTION_REQUEST_LIMIT = 12_000


def request_limit(port):
    return MODEL_REQUEST_LIMIT if port == "model" else PORT_LIMIT


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("meet_companion_execution_duplicate_field")
        result[key] = value
    return result


def decode(raw):
    def invalid(_value):
        raise ValueError("meet_companion_execution_nonfinite")

    return json.loads(raw, object_pairs_hook=_unique, parse_constant=invalid)


def encode(value, *, maximum=PORT_LIMIT):
    if type(maximum) is not int or maximum not in {PORT_LIMIT, MODEL_REQUEST_LIMIT}:
        raise ValueError("meet_companion_execution_port_limit_invalid")
    raw = (json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n").encode()
    if len(raw) > maximum:
        raise ValueError("meet_companion_execution_port_oversize")
    return raw


def validate_request(value, *, expected_sequence, ports):
    if (not isinstance(value, dict) or set(value) != {"kind", "sequence", "port", "arguments"}
            or value["kind"] != "port" or type(value["sequence"]) is not int
            or value["sequence"] != expected_sequence or expected_sequence > MAX_PORT_CALLS
            or not isinstance(value["port"], str) or value["port"] not in ports
            or not isinstance(value["arguments"], dict)):
        raise ValueError("meet_companion_execution_port_invalid")
    return value


class CompanionExecutionPorts:
    """Child-side execution calls over its owner pipe, scoped to one answer."""

    def __init__(self, reader, writer, *, allowed):
        self.reader, self.writer, self.allowed = reader, writer, frozenset(allowed)
        self.sequence = 0

    def call(self, port, arguments):
        sequence = self.sequence + 1
        value = validate_request({"kind": "port", "sequence": sequence, "port": port, "arguments": arguments},
                                 expected_sequence=sequence, ports=self.allowed)
        self.writer.write(encode(value, maximum=request_limit(port)))
        self.writer.flush()
        self.sequence = sequence
        raw = self.reader.readline(PORT_LIMIT + 1)
        if not raw or len(raw) > PORT_LIMIT or not raw.endswith(b"\n"):
            raise ValueError("meet_companion_execution_port_reply_invalid")
        response = decode(raw)
        if (not isinstance(response, dict) or set(response) != {"kind", "sequence", "result"}
                or response["kind"] != "port_result" or type(response["sequence"]) is not int
                or response["sequence"] != sequence):
            raise ValueError("meet_companion_execution_port_reply_invalid")
        return response["result"]
