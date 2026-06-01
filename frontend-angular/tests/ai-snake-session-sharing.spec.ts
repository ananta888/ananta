import { test, expect } from '@playwright/test';
import { ADMIN_PASSWORD, ADMIN_USERNAME, HUB_URL, getAccessToken } from './utils';

test.describe('AI Snake Session Sharing', () => {
  test('creates and joins session via invite code', async ({ request }) => {
    const token = await getAccessToken(ADMIN_USERNAME, ADMIN_PASSWORD);

    const create = await request.post(`${HUB_URL}/share-sessions`, {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        title: 'PW Share Test',
        mode: 'relay',
        transport: 'hub_relay',
        permissions: { chat: true, view_tui: true, remote_cursor: false },
      },
    });
    expect(create.ok()).toBeTruthy();
    const c = await create.json() as any;
    const sessionId = c?.session?.id;
    const inviteCode = c?.session?.invite_code;
    expect(sessionId).toBeTruthy();
    expect(inviteCode).toBeTruthy();

    const join = await request.post(`${HUB_URL}/share-sessions/join`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { invite_code: inviteCode },
    });
    expect(join.ok()).toBeTruthy();

    const postMessage = await request.post(`${HUB_URL}/share-sessions/${sessionId}/chat/messages`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { text: 'hello from playwright', visibility: 'room', channel_type: 'room' },
    });
    expect(postMessage.ok()).toBeTruthy();

    const poll = await request.get(`${HUB_URL}/share-sessions/${sessionId}/chat/messages?since=0`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(poll.ok()).toBeTruthy();
    const p = await poll.json() as any;
    const texts = (p?.messages || []).map((m: any) => String(m?.text || ''));
    expect(texts.some((t: string) => t.includes('hello from playwright'))).toBeTruthy();
  });
});
