"""Read-only live Meet diagnostics; no room creation, login, trust or deployment writes."""

import argparse
import json

from scripts.meet_live_observation import container_observation, public_observation, readiness_report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="https://webrtc.ananta.de")
    parser.add_argument("--container", default="webrtc-minimize-server-webrtc-1")
    parser.add_argument("--local-tls-route", action="store_true", help="Use local TLS virtual host; NOT public DNS/NAT")
    args = parser.parse_args(argv)
    try:
        container = container_observation(args.container)
        observations = [
            public_observation(args.origin, path, local_tls_route=args.local_tls_route)
            for path in ("/healthz", "/config", "/api/machine/capabilities")
        ]
        integration = public_observation(args.origin, "/api/machine/integration", local_tls_route=args.local_tls_route)
        report = readiness_report(
            args.origin, *observations, container, local_tls_route=args.local_tls_route, integration=integration
        )
    except ValueError:
        report = {
            "schema": "ananta.meet-live-readiness.v1",
            "status": "blocked",
            "code": "meet_readiness_input_invalid",
            "production_release_eligible": False,
        }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "observed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
