"""Bounded, credential-free inspection of the operator-configured provider."""

import json
import time

import requests

from agent.services.meet_contract import MeetProfile


class MeetHealthProbe:
    def __init__(self, profile: MeetProfile, session_factory=requests.Session):
        self.profile = profile
        self.session_factory = session_factory

    def inspect(self):
        try:
            with self.session_factory() as session:
                # Do not consult .netrc or inherit proxy credentials.
                session.trust_env = False
                health = self._json(session, "/healthz")
                config = self._json(session, "/config")
            compatible = (
                health.get("status") == "ok"
                and config.get("auth", {}).get("mode") == "required"
                and config.get("mediaE2ee", {}).get("mode") == "required"
            )
            return {
                "status": "available" if compatible else "incompatible",
                "room_verified": False,
                "media_verified": False,
            }
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            return {"status": "unavailable", "room_verified": False, "media_verified": False}

    def _json(self, session, path):
        deadline = time.monotonic() + 6
        with session.get(
            self.profile.origin + path,
            timeout=(2, 2),
            allow_redirects=False,
            stream=True,
            headers={"Accept": "application/json"},
        ) as response:
            if response.status_code != 200:
                raise ValueError("meet_health_status")
            body = bytearray()
            for chunk in response.iter_content(chunk_size=1):
                body.extend(chunk)
                if len(body) > 32768 or time.monotonic() > deadline:
                    raise ValueError("meet_health_budget")
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ValueError("meet_health_contract")
            return result
