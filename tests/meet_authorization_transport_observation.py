"""Private fixture transport observation; no bodies, credentials or policy changes."""

import urllib.request


def observe_authorization_transport(monkeypatch, rows):
    factory = urllib.request.build_opener

    class ObservedOpener:
        def __init__(self, delegate):
            self.delegate = delegate

        def __getattr__(self, name):
            return getattr(self.delegate, name)

        def open(self, request, *args, **kwargs):
            observed = any(request.full_url.endswith("/api/machine/sessions/" + endpoint)
                           for endpoint in ("authorization", "observation", "retire"))
            try:
                response = self.delegate.open(request, *args, **kwargs)
                if observed:
                    status = getattr(response, "status", None)
                    rows.append({"kind": "response",
                                 "status": status if type(status) is int and 100 <= status <= 599 else None})
                return response
            except Exception as error:
                if observed:
                    kind = type(error).__name__
                    status = getattr(error, "code", None)
                    rows.append({"kind": kind if kind in {"HTTPError", "URLError", "TimeoutError"} else "other",
                                 "status": status if type(status) is int and 100 <= status <= 599 else None})
                raise
            finally:
                if len(rows) > 32:
                    del rows[:-32]

    monkeypatch.setattr(
        urllib.request, "build_opener", lambda *args, **kwargs: ObservedOpener(factory(*args, **kwargs))
    )
