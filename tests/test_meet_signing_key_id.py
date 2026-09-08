"""Ephemeral real Hub signatures; key IDs are operator configuration, not Worker input."""

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent.services.meet_contract import MeetError
from agent.services.meet_machine_grant import MeetMachineGrantIssuer
from tests.test_meet_dialog_authority import fixture
from tests.test_meet_media import PRINCIPAL, turn


def signer(tmp_path, key_id=None):
    key = Ed25519PrivateKey.generate()
    path = tmp_path / "synthetic-hub.pem"
    path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    path.chmod(0o600)
    return MeetMachineGrantIssuer("https://hub.example.test", path, key_id=key_id), key


@pytest.mark.parametrize("key_id", ["synthetic-rotation-1", "key_2.v1", "x" * 64, None])
@pytest.mark.parametrize("version", [1, 2])
def test_key_selection_changes_only_protected_header_and_retains_current_hub_authority(tmp_path, key_id, version):
    issuer, key = signer(tmp_path, key_id)
    f = fixture()
    if version == 2:
        result = issuer.issue_dialog(f.authority, "task", "dispatch", "runtime", f.now)
    else:
        request = turn()
        result = issuer.issue(request, f.binding, PRINCIPAL, f.now)
    expected = {"alg": "EdDSA", "typ": "ananta-meet-machine-v2+jwt" if version == 2 else "ananta-meet-machine+jwt"}
    if key_id is not None:
        expected["kid"] = key_id
    assert jwt.get_unverified_header(result["grant"]) == expected
    claims = jwt.decode(
        result["grant"],
        key.public_key(),
        algorithms=["EdDSA"],
        issuer=issuer.issuer,
        audience=f"ananta-meet-machine-v{version}",
    )
    assert set(claims) == {"iss", "aud", "sub", "iat", "exp", "jti", "roomId", "taskId", "tenantId", "projectId"} | (
        {"runtimeId", "sessionId", "capabilities"} if version == 2 else set()
    )
    assert claims["sub"] == "ananta" and "dispatch" not in claims.values()
    assert set(result) == {"origin", "room_id", "grant"}
    f.task.status = "cancelled"
    with pytest.raises(MeetError):
        issuer.issue_dialog(f.authority, "task", "dispatch", "runtime", f.now)


@pytest.mark.parametrize("key_id", ["", " ", "../key", "x" * 65, "key\nsecret", "ä", True, 3, [], {}])
def test_bad_operator_key_id_is_denied_before_private_key_io(key_id):
    loader = Mock(side_effect=AssertionError("must not load key"))
    with pytest.raises(ValueError, match="^meet_machine_key_id_invalid$"):
        MeetMachineGrantIssuer("https://hub.example.test", "unused", key_loader=loader, key_id=key_id)
    loader.assert_not_called()


@pytest.mark.parametrize("version", [1, 2])
def test_real_hub_keyed_grant_is_accepted_only_by_matching_meet_trust(tmp_path, version):
    meet = Path(__file__).resolve().parents[2] / "webrtc-minimize-server"
    if not shutil.which("node") or not (meet / "node_modules/jose").is_dir():
        pytest.skip("actual companion Node/jose installation required for cross-repository signature verification")
    issuer, key = signer(tmp_path, "synthetic-rotation-1")
    f = fixture()
    if version == 2:
        result = issuer.issue_dialog(f.authority, "task", "dispatch", "runtime", f.now)
    else:
        result = issuer.issue(turn(), f.binding, PRINCIPAL, f.now)
    # This decode is test data construction, never an authorization decision.
    claims = jwt.decode(
        result["grant"], key.public_key(), algorithms=["EdDSA"], audience=f"ananta-meet-machine-v{version}"
    )
    x = jwt.algorithms.OKPAlgorithm.to_jwk(key.public_key(), as_dict=True)["x"]
    profile = {
        "schema": "ananta.meet-machine-trust.v1",
        "revision": 1,
        "issuer": issuer.issuer,
        "audiences": [f"ananta-meet-machine-v{version}"],
        "keys": [{"kid": "synthetic-rotation-1", "x": x, "notBefore": f.now - 60, "notAfter": f.now + 1200}],
        "scopes": [
            {
                "subject": "ananta",
                "tenantId": claims["tenantId"],
                "projectId": claims["projectId"],
                "capabilities": claims.get("capabilities", ["avatar.publish", "chat.send", "speech.publish"]),
            }
        ],
    }
    script = """
import assert from 'node:assert/strict';
import { MachineAdmission } from './src/machine-admission.js';
let raw = ''; for await (const chunk of process.stdin) raw += chunk;
const { profile, grant, roomId, now } = JSON.parse(raw);
const context = { roomId, mode: 'room', displayName: 'Ananta (KI)' };
const admission = new MachineAdmission({ trustProfile: profile });
const identity = await admission.verify('Bearer ' + grant, context, now);
assert.equal(identity.subject, 'machine:ananta');
await assert.rejects(admission.verify('Bearer ' + grant, context, now), /invalid/);
for (const field of ['subject', 'tenantId', 'projectId']) {
  const wrong = structuredClone(profile); wrong.scopes[0][field] = 'foreign';
  await assert.rejects(new MachineAdmission({ trustProfile: wrong }).verify(
    'Bearer ' + grant, context, now), /invalid/);
}
const unknown = structuredClone(profile); unknown.keys[0].kid = 'different-key';
await assert.rejects(new MachineAdmission({ trustProfile: unknown }).verify(
  'Bearer ' + grant, context, now), /invalid/);
process.stdout.write('synthetic_cross_repository_signature_verified');
"""
    checked = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=meet,
        input=json.dumps(
            {"profile": profile, "grant": result["grant"], "roomId": result["room_id"], "now": f.now * 1000}
        ),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert checked.returncode == 0, "bounded companion trust verification failed (test grant output redacted)"
    assert checked.stdout == "synthetic_cross_repository_signature_verified"
