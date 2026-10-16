"""Hub-only Git credential capability; validated TLS endpoint pinning."""
from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path

from agent.services.git_remote_policy_service import GitRemoteAccessPolicy
from agent.services.hub_git_credential_resolver import SubprocessGitCredentialCommandResolver
from agent.services.ops_command_runner import CommandResult, CommandRunner

REFERENCE = 'secret://self-development/github'


class GitSecret:
    def __init__(self, path: Path):
        self.path = path

    def resolve(self, reference):
        if reference != REFERENCE:
            raise ValueError('credential_reference_denied')
        return self.path.read_text().strip()

    def status(self, reference):
        return 'active' if reference == REFERENCE and self.path.is_file() else 'missing'


def github_addresses(host: str, port: int):
    if host != 'github.com' or port != 443:
        raise ValueError('development_endpoint_denied')
    try:
        return [entry[4][0] for entry in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]
    except socket.gaierror:
        # Only fixed, certificate-validated public DoH fallback; no global DNS changes.
        result = subprocess.run(['curl', '--silent', '--show-error', '--fail', '--max-time', '10',
            '--resolve', 'cloudflare-dns.com:443:1.1.1.1', '-H', 'accept:application/dns-json',
            'https://cloudflare-dns.com/dns-query?name=github.com&type=A'],
            capture_output=True, timeout=12, check=False)
        if result.returncode:
            raise ValueError('development_dns_unavailable')
        return [a['data'] for a in json.loads(result.stdout).get('Answer', []) if a.get('type') == 1]


class CredentialRunner(CommandRunner):
    def __init__(self, resolver, checkout):
        super().__init__()
        self.resolver = resolver
        self.checkout = checkout

    def run(self, args, *, cwd=None, timeout_seconds=None, env=None):
        if 'push' not in args:
            return super().run(args, cwd=cwd, timeout_seconds=timeout_seconds, env=env)
        if cwd != self.checkout or args[0] != 'git':
            raise ValueError('credential_workspace_denied')
        with self.resolver.open_session(credential_ref=REFERENCE,
                credential_username='x-access-token', scheme='https') as session:
            result = session.run(args[1:], cwd=cwd, timeout_seconds=timeout_seconds or 90)
        return CommandResult(result.returncode, result.stdout.decode(errors='replace'),
            '' if result.returncode == 0 else 'authorized_git_transport_failed',
            timed_out=result.timed_out, truncated=result.output_truncated)


def transport(policy):
    secret = GitSecret(Path(policy.value['github_token_file']))
    resolver = SubprocessGitCredentialCommandResolver(secret_resolver=secret,
        credential_root=policy.root / 'credentials')
    remote_policy = GitRemoteAccessPolicy(allowed_schemes=['https'], allowed_hosts=['github.com'],
        credential_status=secret, dns_resolver=github_addresses)
    return CredentialRunner(resolver, policy.checkout), remote_policy
