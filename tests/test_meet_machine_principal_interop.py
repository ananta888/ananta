"""Real SQL Hub roles/signatures and local Meet P-256/HTTP/WS, without media claims."""

import json
import shutil
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import jwt
import pytest
from sqlmodel import Session

from agent.db_models import OrganizationRoleAssignmentDB, OrganizationRoleSlotDB, TaskDB
from agent.models.meet_session_observation import validate_observation
from agent.services.meet_contract import MeetError
from tests.meet_dialog_lifecycle_fixture import PUBLISHER
from tests.test_meet_machine_principal_sql import start_principal


@pytest.mark.timeout(45)
def test_two_real_hub_assignment_principals_are_distinct_under_same_meet_display_name(app, tmp_path):
    from agent.database import engine

    meet = Path(__file__).resolve().parents[2] / "webrtc-minimize-server"
    if not shutil.which("node") or not (meet / "node_modules/jose").is_dir():
        pytest.skip("actual adjacent Meet Node/jose required for cross-repository identity verification")
    with app.app_context():
        f, first, first_scope, key = start_principal(engine, tmp_path)
        with Session(engine) as session:
            slot = session.get(OrganizationRoleSlotDB, "meet-test-slot")
            session.add(OrganizationRoleSlotDB(**(slot.model_dump() | {"id": "second-slot", "slot_key": "second"})))
            session.commit()
            assignment = session.get(OrganizationRoleAssignmentDB, "meet-test-assignment")
            session.add(
                OrganizationRoleAssignmentDB(
                    **(assignment.model_dump() | {"id": "second-assignment", "role_slot_id": "second-slot"})
                )
            )
            parent = session.get(TaskDB, "meet-test-parent")
            session.add(TaskDB(**(parent.model_dump() | {"id": "second-parent", "role_slot_id": "second-slot"})))
            session.commit()
        started = f.service.start(f.principal, "project", f.payload, parent="second-parent")
        second = f.tasks.get_by_id(started["task_id"])
        context = second.worker_execution_context["meet_dialog"]
        second_scope = f.f.authority.current(second.id, context["lease_id"], context["runtime_id"])
        scopes = [first_scope, second_scope]
        assert first_scope.machine_subject != second_scope.machine_subject
        assert first.assigned_agent_url == second.assigned_agent_url == PUBLISHER
        assert second_scope.machine_principal.assignment_id == "second-assignment"
        tokens = [
            [
                f.service.issuer.issue_dialog(f.f.authority, scope.task_id, scope.lease_id, scope.runtime_id, f.f.now)[
                    "grant"
                ]
                for _ in range(4)
            ]
            for scope in scopes
        ]
        profile = {
            "schema": "ananta.meet-machine-trust.v1",
            "revision": 1,
            "issuer": f.service.issuer.issuer,
            "audiences": ["ananta-meet-machine-v2"],
            "keys": [
                {
                    "kid": "synthetic-key-1",
                    "x": jwt.algorithms.OKPAlgorithm.to_jwk(key.public_key(), as_dict=True)["x"],
                    "notBefore": f.f.now - 60,
                    "notAfter": f.f.now + 1200,
                }
            ],
            "scopes": [
                {
                    "subject": scope.machine_subject,
                    "tenantId": scope.tenant_id,
                    "projectId": scope.project_id,
                    "capabilities": list(scope.capabilities),
                }
                for scope in scopes
            ],
        }
        script = """
import assert from 'node:assert/strict';
import { WebSocket } from 'ws';
import { createAppServer } from './src/server.js';
import { testDevice } from './test/helpers/machine-trust.mjs';
let raw=''; for await (const chunk of process.stdin) raw += chunk;
const { profile, tokens, roomId } = JSON.parse(raw);
const app = createAppServer({ config: { host:'127.0.0.1',port:0,authMode:'required',
  machineHubTrustProfile:profile,oidcIssuer:'https://synthetic-human.test',oidcAudience:'human',
  oidcJwksUrl:'https://synthetic-human.test/jwks',stunUrls:[],turnServers:[],mediaE2eeMode:'required' } });
await new Promise(resolve => app.server.listen(0,'127.0.0.1',resolve));
const base='http://127.0.0.1:'+app.server.address().port, sessions=[], sockets=[];
const post=(path,token,body)=>fetch(base+'/api/machine/sessions'+path,{method:'POST',
  signal:AbortSignal.timeout(2500),headers:{'content-type':'application/json',Authorization:'Bearer '+token},
  body:JSON.stringify(body)});
try {
  for (const [index, grants] of tokens.entries()) {
    const context={roomId,mode:'room',displayName:'Ananta (KI)'}, proof=testDevice();
    const joined=await post('',grants[0],{...context,deviceProof:proof(context),machineReceiveVersion:1});
    assert.equal(joined.status,201); const body=await joined.json();
    const socket=new WebSocket(base.replace('http','ws')+body.signalingPath,{origin:base}); sockets.push(socket);
    const welcome=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('bounded_welcome_missing')),2000);
      socket.on('message',raw=>{const value=JSON.parse(raw);
        if(value.type==='welcome'){clearTimeout(timer);resolve(value);}});
      socket.once('error',()=>{clearTimeout(timer);reject(new Error('bounded_socket_error'));});
    });
    sessions.push({body,peerId:welcome.peerId,proof,index});
  }
  assert.equal(app.registry.participantCount,2);
  assert.notEqual(sessions[0].peerId,sessions[1].peerId);
  const views=[];
  for(const [index, session] of sessions.entries()) {
    const request={roomId,sessionId:session.body.machineLease.sessionId,nonce:'a'.repeat(32)};
    const response=await post('/observation',tokens[index][1],request);
    assert.equal(response.status,200); const view=await response.json(); views.push(view);
    assert.equal(view.binding.subject,'machine:'+profile.scopes[index].subject);
    assert.equal((await post('/observation',tokens[1-index][2],request)).status,401);
    const renewal={roomId,sessionId:request.sessionId,expectedGeneration:1,
      deviceProof:session.proof({roomId,mode:'room',displayName:'Ananta (KI)',
        machineSessionId:request.sessionId,expectedGeneration:1})};
    assert.equal((await post('/renew',tokens[1-index][3],renewal)).status,401);
  }
  process.stdout.write(JSON.stringify({synthetic:true,mediaClaim:false,views}));
} finally {
  for(const socket of sockets) socket.terminate();
  app.server.closeAllConnections(); await new Promise(resolve=>app.server.close(resolve));
}
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=meet,
            input=json.dumps({"profile": profile, "tokens": tokens, "roomId": first_scope.room_id}),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert result.returncode == 0, "bounded synthetic Hub/Meet principal integration failed; grants redacted"
        observed = json.loads(result.stdout)
        assert observed["synthetic"] is True and observed["mediaClaim"] is False
        for index, scope in enumerate(scopes):
            view = observed["views"][index]
            args = (f.service.issuer.issuer, view["lease"]["sessionId"], view["nonce"], int(time.time() * 1000))
            assert validate_observation(view, scope, *args) == view
            with pytest.raises(MeetError, match="scope_invalid"):
                validate_observation(view, replace(scope, machine_principal=scopes[1 - index].machine_principal), *args)
            with pytest.raises(MeetError, match="scope_invalid"):
                validate_observation(view, replace(scope, machine_principal=None), *args)
