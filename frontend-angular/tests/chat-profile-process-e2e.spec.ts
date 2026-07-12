import {test,expect} from '@playwright/test';
import {HUB_URL} from './utils';

test.describe('chat profile settings and process inheritance',()=>{
  test('persists profile/session deltas and immutable process run metadata',async({request})=>{
    const suffix=Date.now().toString(36),graphId=`vp-e2e-${suffix}`,profileId=`profile-e2e-${suffix}`,sessionId=`chat-e2e-${suffix}`;
    const presets=await request.get(`${HUB_URL}/api/visual-process/presets`);expect(presets.ok()).toBeTruthy();const presetList=await presets.json() as any[];expect(presetList.length).toBeGreaterThan(0);
    const preset=await request.get(`${HUB_URL}/api/visual-process/presets/${presetList[0].id}`);const graph=await preset.json() as any;graph.id=graphId;graph.name='E2E chat flow';
    expect((await request.post(`${HUB_URL}/api/visual-process/graphs`,{data:graph})).ok()).toBeTruthy();
    const profile=await request.post(`${HUB_URL}/api/chat/profiles`,{data:{id:profileId,name:'E2E Profile',settings:{chat_backend:'lmstudio',chat_backend_model:'e2e-model',chat_backend_api_base:'http://localhost:1234/v1',chat_rag_top_k:24,chat_use_history:true,chat_history_turns:10},process_ref:{graph_id:graphId,version:'latest'}}});expect(profile.status()).toBe(201);
    const session=await request.post(`${HUB_URL}/api/chat/sessions`,{data:{id:sessionId,name:'E2E Chat',profile_id:profileId}});expect(session.status()).toBe(201);
    const assigned=await request.get(`${HUB_URL}/api/chat/sessions/${sessionId}/process`);expect((await assigned.json() as any).source).toBe('profile');
    await request.patch(`${HUB_URL}/api/chat/sessions/${sessionId}`,{data:{settings:{chat_max_tokens:3000}}});
    const reloaded=await request.get(`${HUB_URL}/api/chat/sessions/${sessionId}`);const sessionBody=await reloaded.json() as any;expect(sessionBody.settings.chat_backend_model).toBe('e2e-model');expect(sessionBody.settings.chat_max_tokens).toBe(3000);
    await request.patch(`${HUB_URL}/api/chat/profiles/${profileId}`,{data:{settings:{chat_rag_top_k:32}}});const afterProfile=await request.get(`${HUB_URL}/api/chat/sessions/${sessionId}`);const afterBody=await afterProfile.json() as any;expect(afterBody.settings.chat_rag_top_k).toBe(32);expect(afterBody.settings.chat_max_tokens).toBe(3000);
    const run=await request.post(`${HUB_URL}/api/chat/sessions/${sessionId}/process/runs`,{data:{message_id:`msg-${suffix}`}});expect(run.status()).toBe(201);const runBody=await run.json() as any;expect(runBody.snapshot_hash).toMatch(/^[a-f0-9]{64}$/);
    graph.description='changed after run start';const versioned=await request.put(`${HUB_URL}/api/visual-process/graphs/${graphId}`,{data:graph});expect(versioned.ok()).toBeTruthy();expect((await versioned.json() as any).version).not.toBe(runBody.process_version);
    const overlay=await request.get(`${HUB_URL}/api/chat/sessions/${sessionId}/process/runs/${runBody.run_id}`);const overlayBody=await overlay.json() as any;expect(overlayBody.process_id).toBe(graphId);expect(overlayBody.message_id).toBe(`msg-${suffix}`);expect(overlayBody.graph_snapshot.id).toBe(graphId);
    expect(overlayBody.graph_snapshot.description).not.toBe('changed after run start');
    await request.delete(`${HUB_URL}/api/chat/sessions/${sessionId}`);await request.delete(`${HUB_URL}/api/chat/profiles/${profileId}`);await request.delete(`${HUB_URL}/api/visual-process/graphs/${graphId}`);
  });
});
