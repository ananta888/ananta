"""Exact-target HTTP boundary for authenticated Hub command requests."""

from __future__ import annotations

import ssl
import urllib.error
import urllib.request
from contextlib import contextmanager


class _DenyHubRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@contextmanager
def open_hub_command(
    request: urllib.request.Request,
    *,
    timeout: float,
    ssl_context: ssl.SSLContext | None,
):
    """Never forward Hub credentials or accept authority from a redirect."""
    opener = urllib.request.build_opener(
        _DenyHubRedirect(),
        urllib.request.HTTPSHandler(context=ssl_context),
    )
    with opener.open(request, timeout=timeout) as response:
        if response.geturl() != request.full_url or 300 <= response.getcode() < 400:
            raise urllib.error.HTTPError(request.full_url, 302, "workflow_hub_redirect_denied", None, None)
        yield response
