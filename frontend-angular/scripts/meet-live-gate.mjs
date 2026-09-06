/** Bounded, opt-in live Meet checks. Never prints credentials, invites or SDP. */
import assert from 'node:assert/strict';
import { lstatSync, readFileSync } from 'node:fs';
import { chromium } from 'playwright';

const origin = process.env.ANANTA_MEET_ORIGIN || 'https://webrtc.ananta.de';
const target = new URL(origin);
assert.equal(target.protocol, 'https:');
assert.equal(target.origin, origin);
const local = process.env.MEET_LIVE_LOCAL === '1';
const full = process.env.MEET_LIVE_FULL === '1';
const report = {
  schema: 'ananta.meet-live-gate.v1', classification: 'synthetic_test_observation',
  route: local ? 'local_caddy' : 'public_hostname_from_this_pc',
  preflight: 'not_run', login_media: 'not_run', hub_binding: 'not_run',
  turn: 'unverified', independent_external_nat: false, production_release_verified: false,
};
let browser;
let stage = 'startup';
const deadline = setTimeout(() => {
  console.log(JSON.stringify({ ...report, status: 'failed', reason: `deadline_${stage}` }));
  void browser?.close().finally(() => process.exit(1));
  setTimeout(() => process.exit(1), 3000).unref();
}, 180_000);

function credentials() {
  const path = process.env.MEET_LIVE_SECRET_FILE;
  if (!path) return null;
  const stat = lstatSync(path);
  assert.ok(stat.isFile() && !stat.isSymbolicLink() && !(stat.mode & 0o077) && stat.size <= 8192);
  const value = JSON.parse(readFileSync(path, 'utf8'));
  for (const key of ['username', 'password', 'username2', 'password2', 'hub_token', 'project_id']) {
    assert.ok(typeof value[key] === 'string' && value[key].length > 0);
  }
  assert.notEqual(value.username, value.username2);
  return value;
}

async function installObservers(context) {
  await context.addInitScript(() => {
    window.__meetGate = { capture: 0, peers: [], channels: [] };
    const NativePeer = window.RTCPeerConnection;
    window.RTCPeerConnection = class extends NativePeer {
      constructor(...args) {
        super(...args);
        window.__meetGate.peers.push(this);
        this.addEventListener('datachannel', event => window.__meetGate.channels.push(event.channel));
      }
      createDataChannel(...args) {
        const channel = super.createDataChannel(...args);
        window.__meetGate.channels.push(channel);
        return channel;
      }
    };
    for (const method of ['getUserMedia', 'getDisplayMedia']) {
      const native = navigator.mediaDevices?.[method]?.bind(navigator.mediaDevices);
      if (native) navigator.mediaDevices[method] = (...args) => {
        window.__meetGate.capture += 1;
        return native(...args);
      };
    }
  });
}

async function preflight(page) {
  const loaded = await page.goto(origin, { waitUntil: 'networkidle' });
  assert.equal(loaded.status(), 200);
  await page.locator('#login').waitFor();
  assert.equal(await page.evaluate(() => window.__meetGate.capture), 0);
  const result = await page.evaluate(async () => {
    const get = async path => {
      const response = await fetch(path, { redirect: 'error', signal: AbortSignal.timeout(8000) });
      if (response.status !== 200) throw new Error('preflight_http');
      return response.json();
    };
    const config = await get('/config');
    const health = await get('/healthz');
    const denied = await fetch('/api/rooms', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: 'room', title: 'Ananta integration negative test', visibility: 'private' }),
      redirect: 'error', signal: AbortSignal.timeout(8000),
    });
    return { health: health.status, auth: config.auth, e2ee: config.mediaE2ee?.mode, denied: denied.status };
  });
  assert.equal(result.health, 'ok');
  assert.equal(result.auth.mode, 'required');
  assert.equal(result.e2ee, 'required');
  assert.equal(result.denied, 401);
  // The local edge intentionally serves Meet only. Public Caddy owns the split.
  if (!local) {
    const service = await page.evaluate(async () => {
      const r = await fetch('/info', { signal: AbortSignal.timeout(8000), redirect: 'error' });
      return (await r.json()).service;
    });
    assert.equal(service, 'ananta-rendezvous');
  }
  report.preflight = 'passed';
  return result.auth;
}

async function login(page, auth, username, password) {
  const issuer = new URL(auth.issuer);
  assert.equal(issuer.protocol, 'https:');
  await page.locator('#login').click();
  await page.waitForURL(url => url.origin === issuer.origin);
  await page.locator('#username').fill(username);
  await page.locator('#password').fill(password);
  await page.locator('#kc-login').click();
  await page.waitForURL(url => url.origin === origin);
  await page.locator('#logout').waitFor();
  assert.equal(await page.evaluate(() => window.__meetGate.capture), 0);
}

async function fullGate(first, auth, secret) {
  const hub = new URL(process.env.MEET_LIVE_HUB_ORIGIN || 'http://127.0.0.1:5000');
  assert.ok(hub.protocol === 'https:' || (hub.protocol === 'http:' && hub.hostname === '127.0.0.1'));
  assert.equal(hub.href, hub.origin + '/');
  const path = `${hub.origin}/api/meet/v1/projects/${encodeURIComponent(secret.project_id)}/binding`;
  const hubRequest = async (method, body) => {
    const response = await fetch(path, {
      method, headers: { Authorization: `Bearer ${secret.hub_token}`, 'Content-Type': 'application/json' },
      ...(body ? { body: JSON.stringify(body) } : {}),
      redirect: 'error', signal: AbortSignal.timeout(15000),
    });
    assert.equal(response.status, 200);
    return response.json();
  };
  stage = 'hub_preflight';
  const initial = await hubRequest('GET');
  assert.equal(initial.invite_url, null, 'test_context_must_have_no_binding');
  assert.equal(initial.profile.origin, origin);
  const secondContext = await browser.newContext();
  await installObservers(secondContext);
  const second = await secondContext.newPage();
  second.setDefaultTimeout(20_000);
  await second.goto(origin);
  stage = 'oidc_login';
  await login(first, auth, secret.username, secret.password);
  await login(second, auth, secret.username2, secret.password2);
  let revision;
  try {
    stage = 'private_room_create';
    await first.locator('#new-room-title').fill('Ananta automated integration test');
    const created = first.waitForResponse(r => new URL(r.url()).pathname === '/api/rooms' && r.request().method() === 'POST');
    await first.locator('#create-room').click();
    const roomResponse = await created;
    assert.equal(roomResponse.status(), 201);
    const room = await roomResponse.json();
    assert.equal(room.visibility, 'private');
    assert.match(room.roomId, /^room-[a-f0-9]{18}$/);
    stage = 'hub_attach';
    const attached = await hubRequest('PUT', { expected_revision: initial.revision, invite_url: room.inviteUrl });
    revision = attached.revision;
    assert.equal(attached.invite_url, `${origin}/?room=${room.roomId}&mode=room`);
    const persisted = await hubRequest('GET');
    assert.equal(persisted.invite_url, attached.invite_url);
    assert.equal(persisted.membership_granted, false);
    stage = 'two_device_join';
    await second.goto(attached.invite_url);
    for (const page of [first, second]) {
      await page.locator('#join-room').click();
      await page.locator('#leave-room').first().waitFor();
      assert.equal(await page.evaluate(() => window.__meetGate.capture), 0);
    }
    stage = 'data_channel';
    for (const page of [first, second]) {
      await page.waitForFunction(() => window.__meetGate.channels.some(channel => channel.readyState === 'open'));
    }
    await first.locator('#chat-message').first().fill('ananta-automated-data-probe');
    await first.locator('#chat-form').first().evaluate(form => form.requestSubmit());
    await second.locator('#chat-log').filter({ hasText: 'ananta-automated-data-probe' }).first().waitFor();
    stage = 'synthetic_audio';
    await first.locator('#toggle-microphone').first().click();
    await second.waitForFunction(async () => {
      for (const pc of window.__meetGate.peers) {
        const stats = await pc.getStats();
        for (const row of stats.values()) {
          if (row.type === 'inbound-rtp' && row.kind === 'audio'
              && row.bytesReceived > 1000 && row.totalSamplesReceived > 1000) return true;
        }
      }
      return false;
    });
    await first.locator('#sframe-status', { hasText: 'active' }).waitFor();
    await second.locator('#sframe-status', { hasText: 'active' }).waitFor();
    report.login_media = 'passed_synthetic_audio_and_data';
    report.hub_binding = 'passed';
  } finally {
    stage = 'cleanup';
    for (const page of [first, second]) {
      const leave = page.locator('#leave-room').first();
      if (await leave.isVisible()) await leave.click();
    }
    if (revision !== undefined) {
      const removed = await hubRequest('DELETE', { expected_revision: revision });
      assert.equal(removed.invite_url, null);
    }
    await secondContext.close();
  }
}

try {
  stage = 'browser_launch';
  browser = await chromium.launch({ headless: true, args: [
    '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
    ...(local ? [`--host-resolver-rules=MAP ${target.hostname} 127.0.0.1`] : []),
  ] });
  const context = await browser.newContext();
  await installObservers(context);
  const page = await context.newPage();
  page.setDefaultTimeout(20_000);
  stage = 'preflight';
  const auth = await preflight(page);
  stage = 'credentials';
  const secret = full ? credentials() : null;
  if (full && !secret) {
    report.status = 'blocked';
    report.reason = 'meet_live_test_credentials_missing';
    process.exitCode = 2;
  } else if (full) {
    await fullGate(page, auth, secret);
    report.status = 'passed_test_only';
  } else {
    report.status = 'passed_preflight_only';
  }
} catch {
  report.status = 'failed';
  report.reason = `live_gate_${stage}`;
  process.exitCode = 1;
} finally {
  await browser?.close();
  clearTimeout(deadline);
  console.log(JSON.stringify(report));
}
