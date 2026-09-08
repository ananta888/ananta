"""Pure private filter-table projection; no shell, host firewall or DNS resolution."""

from ananta_contracts.meet_egress import EgressPolicy, parse_egress_policy


def filter_rules(policy: EgressPolicy, *, ipv6=False):
    if type(policy) is not EgressPolicy or type(ipv6) is not bool:
        raise ValueError("meet_egress_rules_invalid")
    # Preserve the closed contract even for a caller constructing the object
    # outside the JSON admission boundary. No string is used as a shell command.
    import json

    policy = parse_egress_policy(json.dumps(policy.projection()).encode())
    lines = ["*filter", ":INPUT DROP [0:0]", ":FORWARD DROP [0:0]", ":OUTPUT DROP [0:0]"]
    if not ipv6:
        for chain in ("INPUT", "OUTPUT"):
            lines.append(f"-A {chain} -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT")
        for protocol in ("tcp", "udp"):
            for chain, interface in (("INPUT", "-i"), ("OUTPUT", "-o")):
                lines.append(f"-A {chain} {interface} lo -d 127.0.0.1 -p {protocol} --dport 53 -j ACCEPT")
                # Docker DNATs its embedded resolver to a namespace-local high
                # port. Its only upstream must be our non-forwarding resolver.
                lines.append(f"-A {chain} {interface} lo -d 127.0.0.11 -p {protocol} -j ACCEPT")
        for chain, interface in (("INPUT", "-i"), ("OUTPUT", "-o")):
            lines.append(f"-A {chain} {interface} lo -d 127.0.0.1 -p tcp --dport 8094 -j ACCEPT")
        for client in sorted(policy.hub_clients):
            lines.append(f"-A INPUT -s {client} -p tcp --dport 8094 -m conntrack --ctstate NEW -j ACCEPT")
        for endpoint in sorted(policy.endpoints):
            lines.append(
                f"-A OUTPUT -d {endpoint.address} -p {endpoint.protocol} --dport {endpoint.port} "
                "-m conntrack --ctstate NEW -j ACCEPT"
            )
    return "\n".join(lines + ["COMMIT", ""])
