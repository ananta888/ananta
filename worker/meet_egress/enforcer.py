"""Bounded filter-only installation, exclusively inside the dedicated guard."""

import subprocess

from worker.meet_egress.rules import filter_rules


def install_filters(policy, *, run=subprocess.run):
    projections = (
        ("/usr/sbin/ip6tables-restore", filter_rules(policy, ipv6=True)),
        ("/usr/sbin/iptables-restore", filter_rules(policy)),
    )
    # Validate both before installation. A partial installation never produces
    # readiness; Docker must not start the namespace-sharing Worker in that case.
    for testing in (True, False):
        for executable, rules in projections:
            arguments = [executable, "--wait", "2"] + (["--test"] if testing else [])
            try:
                result = run(
                    arguments,
                    input=rules.encode("ascii"),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=4,
                    check=False,
                    env={
                        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                        "LC_ALL": "C",
                        "XTABLES_LOCKFILE": "/run/xtables/lock",
                    },
                )
            except (OSError, subprocess.SubprocessError):
                raise RuntimeError("meet_egress_filter_install_failed") from None
            if result.returncode != 0:
                raise RuntimeError("meet_egress_filter_install_failed")
