from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from agent.services.git_remote_policy_service import (
    AuthorizedGitRemote,
    GitRemoteAccessPolicy,
    GitRemotePolicyError,
    GitRemotePolicyRequest,
    GitTransportAuthorization,
    hardened_git_transport_args,
)
from agent.services.workspace_git_service import (
    WorkspaceGitInitError,
    WorkspaceGitService,
)

_PUBLIC_ADDRESS = "93.184.216.34"


def _policy(*, resolver=None, credential_status=None) -> GitRemoteAccessPolicy:
    return GitRemoteAccessPolicy(
        allowed_hosts=("git.example.com",),
        dns_resolver=resolver or (lambda _host, _port: (_PUBLIC_ADDRESS,)),
        credential_status=credential_status,
    )


def _request(**overrides) -> GitRemotePolicyRequest:
    values = {
        "remote_url": "https://git.example.com/acme/repository.git",
        "operation": "clone",
    }
    values.update(overrides)
    return GitRemotePolicyRequest(**values)


@pytest.mark.parametrize(
    ("address", "reason_code"),
    [
        ("127.0.0.1", "git_remote_address_denied"),
        ("169.254.169.254", "git_remote_address_denied"),
        ("10.0.0.8", "git_remote_address_denied"),
        ("::1", "git_remote_address_denied"),
    ],
)
def test_policy_denies_ssrf_addresses(address: str, reason_code: str) -> None:
    policy = _policy(resolver=lambda _host, _port: (address,))

    with pytest.raises(GitRemotePolicyError, match=reason_code):
        policy.authorize(_request())


@pytest.mark.parametrize(
    "remote_url",
    [
        "https://token@git.example.com/acme/repository.git",
        "https://user:password@git.example.com/acme/repository.git",
        "ssh://user@git.example.com/acme/repository.git",
    ],
)
def test_policy_denies_url_credentials_and_unregistered_ssh_identity(
    remote_url: str,
) -> None:
    with pytest.raises(GitRemotePolicyError):
        _policy().authorize(_request(remote_url=remote_url))


def test_policy_denies_redirect_and_proxy_configuration() -> None:
    policy = _policy()

    with pytest.raises(GitRemotePolicyError, match="git_remote_redirect_denied"):
        policy.authorize(
            _request(
                allow_redirects=True,
                redirect_url="https://git.example.com/redirected.git",
            )
        )
    with pytest.raises(GitRemotePolicyError, match="git_remote_proxy_denied"):
        policy.authorize(_request(proxy_url="https://proxy.example.test"))


def test_policy_detects_dns_rebinding_between_resolutions() -> None:
    answers = iter(((_PUBLIC_ADDRESS,), ("127.0.0.1",)))

    with pytest.raises(GitRemotePolicyError):
        _policy(resolver=lambda _host, _port: next(answers)).authorize(_request())


def test_policy_denies_submodule_recursion_and_lfs_download() -> None:
    policy = _policy()

    with pytest.raises(GitRemotePolicyError, match="git_remote_submodule_denied"):
        policy.authorize(_request(recurse_submodules=True))
    with pytest.raises(GitRemotePolicyError, match="git_remote_lfs_mode_denied"):
        policy.authorize(_request(lfs_mode="fetch"))


class _RevokedCredentialStatus:
    def status(self, _credential_ref: str) -> str:
        return "revoked"


class _ActiveCredentialStatus:
    def status(self, _credential_ref: str) -> str:
        return "active"


def test_policy_accepts_repository_bound_percent_encoded_secret_reference() -> None:
    reference = (
        "secret://github-oauth/grant/user-1/repository/owner%2Frepository"
    )

    authorized = _policy(
        credential_status=_ActiveCredentialStatus()
    ).authorize(_request(credential_ref=reference))

    assert authorized.credential_ref == reference


@pytest.mark.parametrize(
    "reference",
    [
        "secret://github-oauth/grant/user-1/repository/owner%repository",
        "secret://github-oauth/grant/user-1/repository/owner%2Grepository",
        "secret://" + "a" * 504,
    ],
)
def test_policy_rejects_malformed_or_oversized_secret_reference(
    reference: str,
) -> None:
    with pytest.raises(
        GitRemotePolicyError, match="git_credential_reference_invalid"
    ):
        _policy(credential_status=_ActiveCredentialStatus()).authorize(
            _request(credential_ref=reference)
        )


def test_policy_denies_revoked_vault_credential_reference() -> None:
    policy = _policy(credential_status=_RevokedCredentialStatus())

    with pytest.raises(GitRemotePolicyError, match="git_credential_revoked"):
        policy.authorize(
            _request(credential_ref="vault://git/acme-read-token")
        )


def test_policy_returns_only_reference_and_credential_free_url() -> None:
    authorized = _policy().authorize(_request())

    assert authorized.canonical_url == "https://git.example.com/acme/repository.git"
    assert authorized.redacted_url == authorized.canonical_url
    assert authorized.credential_ref is None
    assert authorized.resolved_ips == (_PUBLIC_ADDRESS,)


def test_workspace_git_policy_failure_is_typed_and_creates_no_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    service = WorkspaceGitService(
        remote_policy=_policy(resolver=lambda _host, _port: ("127.0.0.1",))
    )

    with pytest.raises(WorkspaceGitInitError) as raised:
        service.init_workspace(
            workspace,
            remote_url="https://git.example.com/acme/repository.git",
            branch="main",
        )

    assert raised.value.reason_code == "git_remote_address_denied"
    assert not workspace.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("canonical_url", "https://other.example/repository.git"),
        ("host", "other.example"),
        ("validated_ips", ("127.0.0.1",)),
        ("port", 8443),
        ("redirects", "allow"),
        ("proxy", "allow"),
    ),
)
def test_transport_authorization_rejects_coordinate_tampering(
    field: str,
    value: object,
) -> None:
    credential_ref = "vault://git/repository"
    authorized = AuthorizedGitRemote(
        canonical_url="https://git.example.com/acme/repository.git",
        redacted_url="https://git.example.com/acme/repository.git",
        scheme="https",
        host="git.example.com",
        port=443,
        resolved_ips=(_PUBLIC_ADDRESS,),
        credential_ref=credential_ref,
    )
    request = _request(credential_ref=credential_ref)
    authorization = GitTransportAuthorization.create(
        authorized=authorized,
        request=request,
    )

    with pytest.raises(GitRemotePolicyError):
        replace(authorization, **{field: value}).validate()


def test_transport_authorization_redacts_opaque_credential_reference() -> None:
    credential_ref = "vault://git/repository"
    authorized = AuthorizedGitRemote(
        canonical_url="https://git.example.com/acme/repository.git",
        redacted_url="https://git.example.com/acme/repository.git",
        scheme="https",
        host="git.example.com",
        port=443,
        resolved_ips=(_PUBLIC_ADDRESS,),
        credential_ref=credential_ref,
    )
    authorization = GitTransportAuthorization.create(
        authorized=authorized,
        request=_request(credential_ref=credential_ref),
    )

    authorization.validate()

    assert credential_ref not in repr(authorization)


def test_https_git_cli_is_pinned_without_credential_reference() -> None:
    credential_ref = "vault://git/repository"
    authorized = AuthorizedGitRemote(
        canonical_url="https://git.example.com/acme/repository.git",
        redacted_url="https://git.example.com/acme/repository.git",
        scheme="https",
        host="git.example.com",
        port=443,
        resolved_ips=(_PUBLIC_ADDRESS,),
        credential_ref=credential_ref,
    )
    authorization = GitTransportAuthorization.create(
        authorized=authorized,
        request=_request(operation="fetch", credential_ref=credential_ref),
    )

    arguments = hardened_git_transport_args(
        authorization,
        ["fetch", "--no-recurse-submodules", "origin"],
        remote_name="origin",
    )
    rendered = "\n".join(arguments)

    assert (
        f"http.curloptResolve=git.example.com:443:{_PUBLIC_ADDRESS}"
        in arguments
    )
    assert "http.sslVerify=true" in arguments
    assert (
        "remote.origin.url="
        "https://git.example.com/acme/repository.git"
    ) in arguments
    assert credential_ref not in rendered


def test_ssh_git_cli_uses_ip_with_original_host_key_alias() -> None:
    credential_ref = "vault://git/repository"
    authorized = AuthorizedGitRemote(
        canonical_url="ssh://git@git.example.com/acme/repository.git",
        redacted_url="ssh://git@git.example.com/acme/repository.git",
        scheme="ssh",
        host="git.example.com",
        port=22,
        resolved_ips=(_PUBLIC_ADDRESS,),
        credential_ref=credential_ref,
    )
    authorization = GitTransportAuthorization.create(
        authorized=authorized,
        request=_request(
            remote_url=authorized.canonical_url,
            operation="push",
            credential_ref=credential_ref,
        ),
    )

    arguments = hardened_git_transport_args(
        authorization,
        ["push", "origin", "HEAD:refs/heads/main"],
        remote_name="origin",
    )
    rendered = "\n".join(arguments)

    assert f"Hostname={_PUBLIC_ADDRESS}" in rendered
    assert "HostKeyAlias=git.example.com" in rendered
    assert "StrictHostKeyChecking=yes" in rendered
    assert "remote.origin.pushurl=" in rendered
    assert credential_ref not in rendered
