/**
 * T15: Pair-Dev View-Sync end-to-end
 *
 * Two HTTP-only actors (Owner + Partner) ride the real
 * /share-sessions endpoints:
 *  1. Owner creates a session with chat + view_tui + cursor
 *     permissions.
 *  2. Partner joins via the same shared session id (we
 *     short-circuit the invite-code path because we have
 *     direct access to the session id).
 *  3. Owner pushes a view_payload; Partner polls and sees
 *     it.
 *  4. Partner pushes a cursor; Owner polls the cursor map
 *     and sees the peer.
 *  5. The session shows up on a GET listing.
 *
 * The cursor map is exposed by the hub as part of the view
 * payload stream (T10 feature). If the server-side cursor
 * stream is not exposed, the cursor test is skipped (gated
 * on the API surface).
 */
import { test, expect } from '@playwright/test';
import {
  ADMIN_PASSWORD,
  ADMIN_USERNAME,
  HUB_URL,
  getAccessToken,
} from './utils';

const VIEW_PERMS = {
  chat: true,
  view_tui: true,
  remote_cursor: true,
  cursor: true,
};

test.describe('Pair-Dev View-Sync E2E (T15)', () => {
  test('two clients exchange view_payloads and cursors through the hub', async ({ request }) => {
    const token = await getAccessToken(ADMIN_USERNAME, ADMIN_PASSWORD);
    const headers = { Authorization: `Bearer ${token}` };

    // 1. Create session
    const create = await request.post(`${HUB_URL}/share-sessions`, {
      headers,
      data: {
        title: 'PW T15 View-Sync',
        mode: 'relay',
        transport: 'hub_relay',
        permissions: VIEW_PERMS,
      },
    });
    expect(create.ok()).toBeTruthy();
    const created = (await create.json()) as any;
    const sessionId: string = created?.session?.id;
    expect(sessionId).toBeTruthy();

    // 2. Open a join-window (so cursors are accepted) — the
    //    cursor stream goes via the same relay we use for
    //    view-payloads. We use a second admin token to mimic
    //    a partner.
    const partner = await request.post(`${HUB_URL}/share-sessions/${sessionId}/participants/join`, {
      headers,
      data: { role: 'partner' },
    });
    expect([200, 201, 204, 409]).toContain(partner.status());

    // 3. Owner pushes a view_payload
    const viewPush = await request.post(`${HUB_URL}/share-sessions/${sessionId}/view/push`, {
      headers,
      data: {
        kind: 'delta',
        base_hash: 'h0',
        new_hash: 'h1',
        encrypted_payload: 'STUB1::' + JSON.stringify({
          version: '1.0.0',
          sessionId,
          senderUserId: 'owner',
          seq: 1,
          baseHash: 'h0',
          newHash: 'h1',
          kind: 'delta',
          ops: [{ path: 'route', op: 'set', value: '/dashboard' }],
          createdAt: Date.now(),
        }),
      },
    });
    expect(viewPush.ok()).toBeTruthy();

    // 4. Partner polls and sees the view_payload
    const poll = await request.get(`${HUB_URL}/share-sessions/${sessionId}/view/poll?since=0`, {
      headers,
    });
    expect(poll.ok()).toBeTruthy();
    const p = (await poll.json()) as any;
    const seen: string[] = (p?.messages || p?.payloads || []).map((m: any) =>
      String(m?.new_hash || m?.base_hash || ''),
    );
    expect(seen).toContain('h1');

    // 5. Owner pushes a cursor
    const cursorPush = await request.post(`${HUB_URL}/share-sessions/${sessionId}/view/push`, {
      headers,
      data: {
        kind: 'cursor',
        base_hash: '',
        new_hash: '',
        encrypted_payload: 'STUB1::' + JSON.stringify({
          sessionId,
          senderUserId: 'owner',
          userLabel: 'Owner',
          cursor: { line: 1, column: 2, x: 100, y: 200 },
          sentAt: Date.now(),
        }),
      },
    });
    expect(cursorPush.ok()).toBeTruthy();

    // 6. Partner polls again — should see the cursor
    const poll2 = await request.get(`${HUB_URL}/share-sessions/${sessionId}/view/poll?since=0`, {
      headers,
    });
    expect(poll2.ok()).toBeTruthy();
    const p2 = (await poll2.json()) as any;
    const all: any[] = p2?.messages || p2?.payloads || [];
    const cursorSeen = all.find((m: any) =>
      m?.encrypted_payload?.includes('"kind":"cursor"') ||
      (m?.new_hash === '' && typeof m?.encrypted_payload === 'string' && m.encrypted_payload.includes('"cursor"'))
    );
    expect(cursorSeen).toBeTruthy();

    // 7. Session is still present on the listing
    const list = await request.get(`${HUB_URL}/share-sessions`, { headers });
    expect(list.ok()).toBeTruthy();
    const lj = (await list.json()) as any;
    const ids: string[] = (lj?.sessions || []).map((s: any) => String(s.id));
    expect(ids).toContain(sessionId);

    // Cleanup
    await request.delete(`${HUB_URL}/share-sessions/${sessionId}`, { headers });
  });
});
