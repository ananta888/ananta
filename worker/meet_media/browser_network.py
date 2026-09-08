"""Fixed assigned-origin HTTP/WS restriction, not an ICE or container firewall."""

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


def _url(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 8192:
        raise ValueError("meet_browser_network_origin_invalid")
    if not value.isascii() or any(ord(c) <= 32 or ord(c) == 127 or c == "\\" for c in value):
        raise ValueError("meet_browser_network_origin_invalid")
    parsed = urlsplit(value)
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or "%" in parsed.netloc
        or parsed.fragment
        or parsed.port is not None
        and not 1 <= parsed.port <= 65535
    ):
        raise ValueError("meet_browser_network_origin_invalid")
    return parsed


@dataclass(frozen=True)
class FixedMeetOrigin:
    """No wildcard, inferred sibling host, downgrade or mutable allowlist."""

    origin: str

    def __post_init__(self):
        parsed = _url(self.origin)
        host = parsed.hostname
        if ":" in host:
            ipaddress.IPv6Address(host)
            authority = "[" + host + "]"
        else:
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
                raise ValueError("meet_browser_network_origin_invalid")
            authority = host
        if parsed.port is not None:
            authority += ":" + str(parsed.port)
        if parsed.scheme != "https" or self.origin != "https://" + parsed.netloc or parsed.netloc != authority:
            raise ValueError("meet_browser_network_origin_invalid")

    def allows(self, url, *, websocket=False):
        try:
            parsed = _url(url)
            expected = self.origin.replace("https://", "wss://", 1) if websocket else self.origin
            return parsed.scheme + "://" + parsed.netloc == expected
        except ValueError:
            return False

    @property
    def csp(self):
        websocket = self.origin.replace("https://", "wss://", 1)
        return f"connect-src {self.origin} {websocket}; worker-src 'self' blob:; frame-src 'none'; object-src 'none'"

    def allows_request(self, method, url):
        if not self.allows(url):
            return False
        if method in {"GET", "HEAD"}:
            return True
        parsed = _url(url)
        return (
            method == "POST"
            and not parsed.query
            and parsed.path in {"/api/machine/sessions", "/api/machine/sessions/renew"}
        )


def restrict_meet_browser_network(context, origin):
    """Install before creating pages; no grant, source or Hub-policy ownership."""
    policy = FixedMeetOrigin(origin)

    def request(route):
        response = None
        try:
            if not policy.allows_request(route.request.method, route.request.url):
                route.abort("blockedbyclient")
                return
            # Continuing a checked first URL is not a redirect boundary. Never
            # expose a redirect response to the browser or follow it in Node.
            response = route.fetch(max_redirects=0, max_retries=0, timeout=2000)
            if not policy.allows(response.url) or 300 <= response.status < 400:
                route.abort("blockedbyclient")
                return
            headers = dict(response.headers)
            original = headers.get("content-security-policy", "")
            headers["content-security-policy"] = (original + ", " if original else "") + policy.csp
            route.fulfill(response=response, headers=headers)
        except Exception:
            try:
                route.abort("blockedbyclient")
            except Exception:
                pass  # A closing context cannot fall back to unrestricted I/O.
        finally:
            if response is not None:
                try:
                    response.dispose()
                except Exception:
                    pass  # Browser teardown may have already released it.

    context.route("**/*", request)
