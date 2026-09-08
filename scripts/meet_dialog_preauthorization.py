"""Headless Hub operator CAS; never joins a room or activates Meet public trust."""

import argparse
import json
import os

from agent.models.meet_preauthorization_policy import identifier, integer
from agent.services.meet_contract import MeetError
from agent.services.meet_preauthorization_input import read_operator_policy


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise ValueError("invalid_operator_arguments")


def hub_store():
    from agent.config import settings

    if settings.role != "hub":
        raise MeetError("meet_preauthorization_hub_required", 403)
    from agent.database import engine
    from agent.repositories.meet_preauthorizations import SqlMeetPreauthorizations

    store = SqlMeetPreauthorizations(engine)
    store.initialize()
    return store


def main(argv=None, *, store_factory=hub_store):
    parser = _Parser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    provision = commands.add_parser("provision")
    provision.add_argument("--policy-file", required=True)
    provision.add_argument("--expected-revision", type=int, required=True)
    revoke = commands.add_parser("revoke")
    revoke.add_argument("--policy-id", required=True)
    revoke.add_argument("--expected-revision", type=int, required=True)
    try:
        args = parser.parse_args(argv)
        integer(args.expected_revision, minimum=0 if args.command == "provision" else 1, maximum=2**31 - 2)
        value = read_operator_policy(args.policy_file) if args.command == "provision" else identifier(args.policy_id)
        store = store_factory()
        operator = "local-uid:" + str(os.geteuid())
        result = (
            store.provision(value, args.expected_revision, operator)
            if args.command == "provision"
            else store.revoke(value, args.expected_revision, operator)
        )
    except Exception as error:
        allowed = {
            "meet_preauthorization_conflict",
            "meet_preauthorization_expired",
            "meet_preauthorization_hub_required",
            "meet_preauthorization_storage_unavailable",
            "meet_preauthorization_clock_invalid",
        }
        code = error.code if type(error) is MeetError and error.code in allowed else "meet_preauthorization_blocked"
        print(
            json.dumps(
                {
                    "schema": "ananta.meet-preauthorization-operator-result.v1",
                    "status": "blocked",
                    "code": code,
                    "dispatch_started": False,
                    "trust_activated": False,
                }
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
