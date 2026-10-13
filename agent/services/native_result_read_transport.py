"""Bounded, non-retrying Hub transport for retrieval-only Native results."""

import json
import time

from agent.common.http import HttpClient


class NativeResultReadTransport:
    def __init__(self, *, client=None):
        self._client = client or HttpClient(timeout=5, retries=0)

    def forward_task(self, worker_url, endpoint, payload, *, token, timeout):
        if endpoint != "/api/internal/native-tasks/result":
            raise ValueError("native_result_transport_read_only")
        timeout = max(0.1, min(float(timeout), 5))
        deadline = time.monotonic() + timeout
        response = self._client.post(
            worker_url.rstrip("/") + endpoint, data=payload, headers={"Authorization": "Bearer " + token},
            timeout=timeout, return_response=True, allow_redirects=False, stream=True,
            deadline_monotonic=deadline, raise_on_transport_error=True,
        )
        try:
            if response.status_code != 200:
                raise ValueError("native_result_transport_response_denied")
            data = bytearray()
            for chunk in response.iter_content(chunk_size=4096):
                if time.monotonic() >= deadline or len(data) + len(chunk) > 262144:
                    raise ValueError("native_result_transport_limit")
                data.extend(chunk)
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError("native_result_transport_invalid")
            return value
        finally:
            response.close()
