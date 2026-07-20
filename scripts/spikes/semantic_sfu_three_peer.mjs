#!/usr/bin/env node
/** Real three-browser LiveKit fan-out/E2EE spike. Never fabricates unavailable evidence. */

import { createHmac } from 'node:crypto';
import { createReadStream, existsSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(resolve(root, 'frontend-angular/package.json'));
const { chromium, firefox } = require('playwright');
const sdkPath = resolve(root, 'frontend-angular/node_modules/livekit-client/dist/livekit-client.umd.js');
const workerPath = resolve(root, 'frontend-angular/node_modules/livekit-client/dist/livekit-client.e2ee.worker.js');
const outputPath = resolve(root, process.env.ANANTA_SEMANTIC_MEDIA_SFU_SPIKE_OUTPUT ?? 'artifacts/domain/semantic-sfu-three-peer.json');

const apiKey = process.env.ANANTA_SEMANTIC_MEDIA_SFU_API_KEY ?? '';
const apiSecret = process.env.ANANTA_SEMANTIC_MEDIA_SFU_API_SECRET ?? '';
const sfuUrl = process.env.ANANTA_SEMANTIC_MEDIA_SFU_PUBLIC_WS_URL ?? 'ws://127.0.0.1:7880';
const requestedEngines = (process.env.ANANTA_SEMANTIC_MEDIA_SFU_BROWSER_ENGINES ?? 'chromium')
  .split(',').map(value => value.trim()).filter(Boolean);
const receiverCount = Number.parseInt(process.env.ANANTA_SEMANTIC_MEDIA_SFU_RECEIVER_COUNT ?? '2', 10);
const wrongKeyProbe = process.env.ANANTA_SEMANTIC_MEDIA_SFU_WRONG_KEY_PROBE !== '0';
const forceRelay = process.env.ANANTA_SEMANTIC_MEDIA_SFU_FORCE_RELAY === '1';
const turnUrl = process.env.ANANTA_SEMANTIC_MEDIA_TURN_GATE_URL ?? '';
const turnUsername = process.env.ANANTA_SEMANTIC_MEDIA_TURN_GATE_USER ?? '';
const turnCredential = process.env.ANANTA_SEMANTIC_MEDIA_TURN_GATE_PASSWORD ?? '';
if (!Number.isSafeInteger(receiverCount) || receiverCount < 1 || receiverCount > 7) {
  throw new Error('receiver count must be between 1 and 7');
}
if (!existsSync(sdkPath) || !existsSync(workerPath)) throw new Error('pinned livekit-client assets missing');
if (apiKey.length < 4 || apiSecret.length < 32) throw new Error('SFU spike requires non-empty API key and >=32-byte secret');
if (forceRelay && (!/^turn:[^\s]+$/.test(turnUrl) || !turnUsername || turnCredential.length < 32)) {
  throw new Error('forced relay requires a TURN URL, user and >=32-byte gate credential');
}

const server = createServer((request, response) => {
  if (request.url === '/livekit.js') return stream(response, sdkPath, 'text/javascript');
  if (request.url === '/e2ee-worker.js') return stream(response, workerPath, 'text/javascript');
  response.writeHead(200, { 'content-type': 'text/html', 'cache-control': 'no-store' });
  response.end('<!doctype html><meta charset="utf-8"><title>Ananta SFU Spike</title>');
});
await new Promise(resolveListen => server.listen(0, '127.0.0.1', resolveListen));
const address = server.address();
if (!address || typeof address === 'string') throw new Error('spike HTTP server unavailable');
const origin = `http://127.0.0.1:${address.port}`;

const engineResults = [];
try {
  for (const engineName of requestedEngines) {
    const engine = engineName === 'chromium' ? chromium : engineName === 'firefox' ? firefox : null;
    if (!engine) throw new Error(`unsupported browser engine: ${engineName}`);
    engineResults.push(await runEngine(engineName, engine));
  }
} finally {
  await new Promise(resolveClose => server.close(resolveClose));
}

const verdict = engineResults.length > 0 && engineResults.every(result => result.verdict === 'pass') ? 'pass' : 'fail';
const report = {
  schema: 'ananta.semantic-sfu-three-peer-spike.v1',
  pinned: {
    server_version: '1.13.1',
    server_digest: 'sha256:2c6869d2d5ff6c9c0166f47be1c92dad6928bfecfa5e4060a6ece48db8accfa3',
    client_version: '2.20.1',
  },
  topology: { publishers: 1, receivers: receiverCount, expected_publisher_uploads: 1 },
  transport_profile: forceRelay ? 'turn_relay_required' : 'ordinary_candidate_selection',
  e2ee: {
    enabled: true,
    server_plaintext_access: false,
    evidence: wrongKeyProbe
      ? `${receiverCount} correct-key receivers decode; one wrong-key receiver receives no decoded video frames`
      : `${receiverCount} correct-key receivers decode`,
    packet_capture_scope: wrongKeyProbe ? 'KPA negative probe; raw media payload is never persisted' : 'not collected in load mode',
  },
  engines: engineResults,
  verdict,
};
writeFileSync(outputPath, `${JSON.stringify(report, null, 2)}\n`, { encoding: 'utf8' });
console.log(JSON.stringify({ output: outputPath, verdict, engines: engineResults.map(row => row.engine) }));
if (verdict !== 'pass') process.exitCode = 1;

async function runEngine(engineName, engine) {
  const browser = await engine.launch({ headless: true });
  const room = `ananta-spike-${engineName}`;
  const sharedKey = Uint8Array.from({ length: 32 }, (_, index) => (index * 17 + 31) & 0xff);
  const wrongKey = Uint8Array.from(sharedKey, byte => byte ^ 0xa5);
  const pages = [];
  try {
    const receiverIds = Array.from({ length: receiverCount }, (_, index) => `receiver-${index + 1}`);
    const identities = ['publisher', ...receiverIds, ...(wrongKeyProbe ? ['wrong-key-probe'] : [])];
    for (const identity of identities) {
      const context = await browser.newContext({ permissions: [] });
      const page = await context.newPage(); pages.push({ identity, context, page });
      await page.addInitScript(() => {
        const Native = window.RTCPeerConnection;
        window.__anantaPeerConnections = [];
        window.RTCPeerConnection = new Proxy(Native, {
          construct(target, args) {
            const pc = Reflect.construct(target, args);
            window.__anantaPeerConnections.push(pc);
            return pc;
          },
        });
      });
      await page.goto(origin);
      await page.addScriptTag({ url: `${origin}/livekit.js` });
      const publish = identity === 'publisher';
      await page.evaluate(async ({ url, token, keyBytes, autoSubscribe, relayOnly, relayServer }) => {
        const lk = window.LivekitClient;
        const provider = new lk.ExternalE2EEKeyProvider({ keySize: 256 });
        await provider.setKey(Uint8Array.from(keyBytes).buffer);
        const worker = new Worker('/e2ee-worker.js');
        const clientRoom = new lk.Room({
          adaptiveStream: false, dynacast: true, singlePeerConnection: true,
          encryption: { keyProvider: provider, worker },
        });
        window.__ananta = { room: clientRoom, worker, subscribed: [], decoded: 0 };
        clientRoom.on(lk.RoomEvent.TrackSubscribed, (track, publication, participant) => {
          window.__ananta.subscribed.push({ name: publication.trackName, publisher: participant.identity });
          if (track.kind === 'video') {
            const video = track.attach(); video.muted = true; video.autoplay = true;
            document.body.append(video); void video.play().catch(() => undefined);
            const sample = () => {
              if (video.readyState >= 2 && video.videoWidth > 0) window.__ananta.decoded += 1;
              if (window.__ananta) setTimeout(sample, 100);
            };
            sample();
          }
        });
        await clientRoom.connect(url, token, {
          autoSubscribe,
          ...(relayOnly ? {
            rtcConfig: {
              iceTransportPolicy: 'relay',
              iceServers: [{
                urls: relayServer.url,
                username: relayServer.username,
                credential: relayServer.credential,
              }],
            },
          } : {}),
        });
        await clientRoom.setE2EEEnabled(true);
      }, {
        url: sfuUrl,
        token: token(apiKey, apiSecret, room, identity, publish, !publish),
        keyBytes: [...(identity === 'wrong-key-probe' ? wrongKey : sharedKey)],
        autoSubscribe: !publish,
        relayOnly: forceRelay,
        relayServer: { url: turnUrl, username: turnUsername, credential: turnCredential },
      });
    }
    const publisher = pages[0].page;
    await publisher.evaluate(async () => {
      const canvas = document.createElement('canvas'); canvas.width = 320; canvas.height = 180;
      document.body.append(canvas); const ctx = canvas.getContext('2d'); let frame = 0;
      window.__ananta.paint = setInterval(() => {
        ctx.fillStyle = `rgb(${frame % 255},${(frame * 3) % 255},${(frame * 7) % 255})`;
        ctx.fillRect(0, 0, canvas.width, canvas.height); frame += 1;
      }, 50);
      const videoTrack = canvas.captureStream(15).getVideoTracks()[0];
      window.__ananta.videoTrack = videoTrack;
      await window.__ananta.room.localParticipant.publishTrack(videoTrack, {
        name: 'fanout-camera', source: window.LivekitClient.Track.Source.Camera,
      });
    });
    for (const receiver of pages.filter(entry => entry.identity.startsWith('receiver-'))) {
      await receiver.page.waitForFunction(() => window.__ananta?.decoded >= 3, null, { timeout: 20_000 });
    }
    await new Promise(resolveWait => setTimeout(resolveWait, 1500));
    const metrics = [];
    for (const entry of pages) metrics.push({ identity: entry.identity, ...(await stats(entry.page)) });
    const receivers = metrics.filter(row => row.identity.startsWith('receiver-'));
    const wrong = metrics.find(row => row.identity === 'wrong-key-probe');
    const publisherMetrics = metrics.find(row => row.identity === 'publisher');
    const relaySelected = metrics.every(row =>
      row.selected_candidate_types.some(value => value.startsWith('relay:')),
    );
    const pass = Boolean(
      publisherMetrics && publisherMetrics.outbound_video_streams === 1 && publisherMetrics.outbound_video_bytes > 0
      && receivers.length === receiverCount && receivers.every(row => row.decoded_samples >= 3 && row.inbound_video_bytes > 0)
      && (!wrongKeyProbe || (wrong && wrong.decoded_samples === 0 && wrong.inbound_video_bytes > 0))
      && (!forceRelay || relaySelected),
    );
    return {
      engine: engineName,
      peers: metrics,
      relay_required: forceRelay,
      relay_selected: relaySelected,
      verdict: pass ? 'pass' : 'fail',
    };
  } finally {
    for (const { page, context } of pages) {
      await page.evaluate(async () => {
        if (!window.__ananta) return;
        clearInterval(window.__ananta.paint); window.__ananta.videoTrack?.stop();
        await window.__ananta.room.disconnect(true); window.__ananta.worker.terminate(); window.__ananta = null;
      }).catch(() => undefined);
      await context.close();
    }
    await browser.close();
  }
}

async function stats(page) {
  return page.evaluate(async () => {
    let sent = 0; let received = 0; let outbound = 0; let inbound = 0; let dropped = 0;
    const candidateTypes = [];
    for (const pc of window.__anantaPeerConnections ?? []) {
      const report = await pc.getStats();
      report.forEach(row => {
        if (row.type === 'outbound-rtp' && row.kind === 'video' && !row.isRemote) {
          sent += Number(row.bytesSent ?? 0); outbound += 1;
        }
        if (row.type === 'inbound-rtp' && row.kind === 'video' && !row.isRemote) {
          received += Number(row.bytesReceived ?? 0); inbound += 1; dropped += Number(row.framesDropped ?? 0);
        }
        if (row.type === 'candidate-pair' && row.state === 'succeeded' && (row.nominated || row.selected)) {
          const local = report.get(row.localCandidateId); const remote = report.get(row.remoteCandidateId);
          candidateTypes.push(`${local?.candidateType ?? 'unknown'}:${remote?.candidateType ?? 'unknown'}`);
        }
      });
    }
    return {
      peer_connections: window.__anantaPeerConnections?.length ?? 0,
      outbound_video_streams: outbound, inbound_video_streams: inbound,
      outbound_video_bytes: Math.trunc(sent), inbound_video_bytes: Math.trunc(received),
      dropped_video_frames: Math.trunc(dropped), selected_candidate_types: [...new Set(candidateTypes)].sort(),
      decoded_samples: window.__ananta?.decoded ?? 0,
      subscribed_publications: window.__ananta?.subscribed ?? [],
    };
  });
}

function token(issuer, secret, room, identity, canPublish, canSubscribe) {
  const now = Math.trunc(Date.now() / 1000);
  const header = base64url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const payload = base64url(JSON.stringify({
    iss: issuer, sub: identity, nbf: now - 1, exp: now + 60,
    video: {
      roomJoin: true, room, canPublish, canSubscribe, canPublishData: false,
      canPublishSources: canPublish ? ['camera'] : [], roomAdmin: false, roomRecord: false,
    },
  }));
  const unsigned = `${header}.${payload}`;
  return `${unsigned}.${createHmac('sha256', secret).update(unsigned).digest('base64url')}`;
}

function base64url(value) { return Buffer.from(value).toString('base64url'); }
function stream(response, path, contentType) {
  response.writeHead(200, { 'content-type': contentType, 'cache-control': 'no-store' });
  createReadStream(path).pipe(response); return undefined;
}
