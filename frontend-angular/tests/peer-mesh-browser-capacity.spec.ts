import { BrowserType, Page, chromium, expect, firefox, test } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';

type Profile = 'audio_only' | 'camera_720p' | 'screenshare';

interface BrowserMeasurement {
  readonly engine: string;
  readonly browserVersion: string;
  readonly profile: Profile;
  readonly participantCount: number;
  readonly connectionCount: number;
  readonly elapsedMs: number;
  readonly bytesSent: number;
  readonly packetsSent: number;
  readonly framesEncoded: number;
  readonly framesDropped: number;
  readonly maximumRoundTripTimeMs: number;
  readonly qualityLimitationReasons: readonly string[];
  readonly encoderFramesPerSecondProxy: number;
  readonly batteryUplinkBytesPerSecondProxy: number;
}

const ENGINES: ReadonlyArray<readonly [string, BrowserType]> = [
  ['chromium', chromium],
  ['firefox', firefox],
];
const PROFILES: readonly Profile[] = ['audio_only', 'camera_720p', 'screenshare'];

test.describe.configure({ mode: 'serial' });

for (const [engine, browserType] of ENGINES) {
  for (const profile of PROFILES) {
    test(`${engine} measures a four-identity ${profile} mesh`, async ({}, testInfo) => {
      test.skip(
        !process.env['ANANTA_HUB_EVIDENCE_ASSIGNMENT_JSON'],
        'requires a Hub-reserved synthetic test evidence assignment',
      );
      const assignment = JSON.parse(process.env['ANANTA_HUB_EVIDENCE_ASSIGNMENT_JSON'] ?? '{}') as {
        run_id?: string; task_id?: string; evidence_scope?: string;
      };
      expect(assignment.run_id).toMatch(/^RUN_/);
      expect(assignment.task_id).toBe('DPM-MESH-004');
      expect(assignment.evidence_scope).toBe('test');
      const browser = await browserType.launch({ headless: true });
      try {
        const browserVersion = browser.version();
        const pages = await Promise.all(Array.from({ length: 4 }, async (_, index) => {
          const context = await browser.newContext();
          const page = await context.newPage();
          await page.goto('about:blank');
          await initializeIdentity(page, `peer-${index + 1}`, profile);
          return page;
        }));
        const startedAt = Date.now();
        for (let left = 0; left < pages.length; left += 1) {
          for (let right = left + 1; right < pages.length; right += 1) {
            await connect(pages[left], pages[right], `peer-${left + 1}`, `peer-${right + 1}`);
          }
        }
        await Promise.all(pages.map(page => page.waitForFunction(() => {
          const mesh = (globalThis as typeof globalThis & { __mesh?: MeshState }).__mesh;
          return mesh && Object.values(mesh.peers).every(peer => peer.connectionState === 'connected');
        })));
        await Promise.all(pages.map(page => page.evaluate(() => {
          const mesh = (globalThis as typeof globalThis & { __mesh: MeshState }).__mesh;
          for (const channel of Object.values(mesh.channels)) {
            if (channel.readyState === 'open') channel.send(`ping:${mesh.identity}`);
          }
        })));
        await Promise.all(pages.map(page => page.waitForFunction(() => {
          const mesh = (globalThis as typeof globalThis & { __mesh?: MeshState }).__mesh;
          return Boolean(mesh && mesh.messages.length >= 3);
        })));
        await pages[0].waitForTimeout(1_500);
        const stats = await Promise.all(pages.map(readStats));
        const elapsedMs = Date.now() - startedAt;
        const measurement = summarize(engine, browserVersion, profile, elapsedMs, stats);
        expect(measurement.connectionCount).toBe(12);
        expect(measurement.bytesSent).toBeGreaterThan(0);
        expect(measurement.packetsSent).toBeGreaterThan(0);
        if (profile !== 'audio_only') expect(measurement.framesEncoded).toBeGreaterThan(0);
        const outputDirectory = process.env['ANANTA_PEER_MESH_MEASUREMENT_DIR']
          ?? '/tmp/ananta-peer-mesh-measurements';
        await mkdir(outputDirectory, { recursive: true });
        await writeFile(
          `${outputDirectory}/${engine}-${profile}.json`,
          `${JSON.stringify(measurement, null, 2)}\n`,
          { encoding: 'utf8' },
        );
        await testInfo.attach('peer-mesh-browser-measurement.json', {
          body: JSON.stringify(measurement, null, 2),
          contentType: 'application/json',
        });
      } finally {
        await browser.close();
      }
    });
  }
}

interface MeshState {
  readonly identity: string;
  readonly peers: Record<string, RTCPeerConnection>;
  readonly channels: Record<string, RTCDataChannel>;
  readonly messages: string[];
  readonly resources: Array<{ stop(): void }>;
}

interface PageStats {
  readonly connectionCount: number;
  readonly bytesSent: number;
  readonly packetsSent: number;
  readonly framesEncoded: number;
  readonly framesDropped: number;
  readonly maximumRoundTripTimeMs: number;
  readonly qualityLimitationReasons: readonly string[];
}

async function initializeIdentity(page: Page, identity: string, profile: Profile): Promise<void> {
  await page.evaluate(({ identity: peerId, profile: mediaProfile }) => {
    const scope = globalThis as typeof globalThis & { __mesh: MeshState };
    const resources: Array<{ stop(): void }> = [];
    let track: MediaStreamTrack;
    if (mediaProfile === 'audio_only') {
      const audio = new AudioContext();
      const oscillator = audio.createOscillator();
      const destination = audio.createMediaStreamDestination();
      oscillator.connect(destination);
      oscillator.start();
      track = destination.stream.getAudioTracks()[0];
      resources.push({ stop: () => { oscillator.stop(); void audio.close(); } });
    } else {
      const canvas = document.createElement('canvas');
      canvas.width = 1280;
      canvas.height = 720;
      const context = canvas.getContext('2d')!;
      let frame = 0;
      const timer = setInterval(() => {
        context.fillStyle = `hsl(${frame++ % 360} 80% 40%)`;
        context.fillRect(0, 0, canvas.width, canvas.height);
      }, 50);
      const stream = canvas.captureStream(20);
      track = stream.getVideoTracks()[0];
      resources.push({ stop: () => { clearInterval(timer); track.stop(); } });
    }
    scope.__mesh = {
      identity: peerId,
      peers: {},
      channels: {},
      messages: [],
      resources,
    };
    (scope.__mesh as MeshState & { track: MediaStreamTrack }).track = track;
  }, { identity, profile });
}

async function connect(offerPage: Page, answerPage: Page, offerer: string, answerer: string): Promise<void> {
  const offer = await offerPage.evaluate(async ({ remoteId }) => {
    const mesh = (globalThis as typeof globalThis & { __mesh: MeshState & { track: MediaStreamTrack } }).__mesh;
    const peer = new RTCPeerConnection();
    mesh.peers[remoteId] = peer;
    peer.addTrack(mesh.track);
    const channel = peer.createDataChannel('ananta-mesh-control');
    mesh.channels[remoteId] = channel;
    channel.onmessage = event => mesh.messages.push(String(event.data));
    await peer.setLocalDescription(await peer.createOffer());
    if (peer.iceGatheringState !== 'complete') await new Promise<void>(resolve => {
      peer.addEventListener('icegatheringstatechange', () => {
        if (peer.iceGatheringState === 'complete') resolve();
      });
    });
    return peer.localDescription!.toJSON();
  }, { remoteId: answerer });
  const answer = await answerPage.evaluate(async ({ remoteId, remoteOffer }) => {
    const mesh = (globalThis as typeof globalThis & { __mesh: MeshState }).__mesh;
    const peer = new RTCPeerConnection();
    mesh.peers[remoteId] = peer;
    peer.ondatachannel = event => {
      mesh.channels[remoteId] = event.channel;
      event.channel.onmessage = message => mesh.messages.push(String(message.data));
      event.channel.onopen = () => event.channel.send(`ping:${mesh.identity}`);
    };
    await peer.setRemoteDescription(remoteOffer);
    await peer.setLocalDescription(await peer.createAnswer());
    if (peer.iceGatheringState !== 'complete') await new Promise<void>(resolve => {
      peer.addEventListener('icegatheringstatechange', () => {
        if (peer.iceGatheringState === 'complete') resolve();
      });
    });
    return peer.localDescription!.toJSON();
  }, { remoteId: offerer, remoteOffer: offer });
  await offerPage.evaluate(
    async ({ remoteId, remoteAnswer }) => {
      const mesh = (globalThis as typeof globalThis & { __mesh: MeshState }).__mesh;
      await mesh.peers[remoteId].setRemoteDescription(remoteAnswer);
    },
    { remoteId: answerer, remoteAnswer: answer },
  );
}

async function readStats(page: Page): Promise<PageStats> {
  return page.evaluate(async () => {
    const mesh = (globalThis as typeof globalThis & { __mesh: MeshState }).__mesh;
    const totals = {
      connectionCount: Object.keys(mesh.peers).length,
      bytesSent: 0,
      packetsSent: 0,
      framesEncoded: 0,
      framesDropped: 0,
      maximumRoundTripTimeMs: 0,
      qualityLimitationReasons: [] as string[],
    };
    for (const peer of Object.values(mesh.peers)) {
      for (const report of (await peer.getStats()).values()) {
        if (report.type === 'outbound-rtp' && !report.isRemote) {
          totals.bytesSent += Number(report.bytesSent ?? 0);
          totals.packetsSent += Number(report.packetsSent ?? 0);
          totals.framesEncoded += Number(report.framesEncoded ?? 0);
          totals.framesDropped += Number(report.framesDropped ?? 0);
          if (report.qualityLimitationReason) {
            totals.qualityLimitationReasons.push(String(report.qualityLimitationReason));
          }
        }
        if (report.type === 'candidate-pair' && report.state === 'succeeded') {
          totals.maximumRoundTripTimeMs = Math.max(
            totals.maximumRoundTripTimeMs,
            Number(report.currentRoundTripTime ?? 0) * 1_000,
          );
        }
      }
    }
    return totals;
  });
}

function summarize(
  engine: string,
  browserVersion: string,
  profile: Profile,
  elapsedMs: number,
  stats: readonly PageStats[],
): BrowserMeasurement {
  const sum = (select: (value: PageStats) => number) => stats.reduce((total, value) => total + select(value), 0);
  const bytesSent = sum(value => value.bytesSent);
  const framesEncoded = sum(value => value.framesEncoded);
  return Object.freeze({
    engine,
    browserVersion,
    profile,
    participantCount: stats.length,
    connectionCount: sum(value => value.connectionCount),
    elapsedMs,
    bytesSent,
    packetsSent: sum(value => value.packetsSent),
    framesEncoded,
    framesDropped: sum(value => value.framesDropped),
    maximumRoundTripTimeMs: Math.max(...stats.map(value => value.maximumRoundTripTimeMs)),
    qualityLimitationReasons: Object.freeze([...new Set(stats.flatMap(value => value.qualityLimitationReasons))].sort()),
    encoderFramesPerSecondProxy: framesEncoded * 1_000 / elapsedMs,
    batteryUplinkBytesPerSecondProxy: bytesSent * 1_000 / elapsedMs,
  });
}
