"""Explicit local Hub key-only setup: python -m scripts.provision_meet_machine_keys DIRECTORY."""

import argparse
import json

from agent.services.meet_machine_key_provisioning import provision_meet_machine_keys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    args = parser.parse_args(argv)
    try:
        result = provision_meet_machine_keys(args.directory)
    except ValueError:
        print(
            json.dumps(
                {
                    "schema": "ananta.meet-machine-key-provisioning.v1",
                    "status": "blocked",
                    "code": "meet_machine_key_provisioning_blocked",
                    "trust_activated": False,
                }
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
