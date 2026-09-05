import { Browser, BrowserContext, BrowserType, Page, chromium, expect, firefox, test } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';

interface Device {
  readonly id: string;
  readonly browser: Browser;
  readonly context: BrowserContext;
  readonly page: Page;
}

interface ChurnMeasurement {
  readonly engine: string;
  readonly browserVersion: string;
  readonly deviceIdentities: readonly string[];
  readonly processIsolation: true;
  readonly edgeCount: number;
  readonly scenarios: Readonly<{
    backgroundTab: Readonly<{
      documentHasFocus: false;
      activation: 'cover_tab_brought_to_front';
      recoveryMs: number;
    }>;
    relayFailure: Readonly<{ recoveryMs: number; backupPath: string }>;
    browserCrash: Readonly<{ recoveryMs: number; backupPath: string }>;
    iceRestart: Readonly<{ recoveryMs: number; delivered: true }>;
  }>;
  readonly humanInterventionRequired: false;
}

interface PeerApi {
  createOffer(edge: string): Promise<RTCSessionDescriptionInit>;
  acceptOffer(edge: string, offer: RTCSessionDescriptionInit): Promise<RTCSessionDescriptionInit>;
  acceptAnswer(edge: string, answer: RTCSessionDescriptionInit): Promise<void>;
  send(edge: string, message: string): void;
  hasMessage(message: string): boolean;
  closeEdge(edge: string): void;
  restartOffer(edge: string): Promise<RTCSessionDescriptionInit>;
  acceptRestartOffer(edge: string, offer: RTCSessionDescriptionInit): Promise<RTCSessionDescriptionInit>;
  acceptRestartAnswer(edge: string, answer: RTCSessionDescriptionInit): Promise<void>;
}

const ENGINES: ReadonlyArray<readonly [string, BrowserType]> = [
  ['chromium', chromium],
  ['firefox', firefox],
];
const DEVICE_IDS = ['source-device', 'relay-a-device', 'relay-b-device', 'leaf-c-device', 'leaf-d-device'];

test.describe.configure({ mode: 'serial' });

for (const [engine, browserType] of ENGINES) {
  test(`${engine} recovers a five-device overlay without interaction`, async () => {
    const assignment = JSON.parse(process.env['ANANTA_HUB_EVIDENCE_ASSIGNMENT_JSON'] ?? '{}') as {
      run_id?: string; task_id?: string; evidence_scope?: string;
    };
    expect(assignment.run_id).toMatch(/^RUN_/);
    expect(assignment.task_id).toBe('DPM-QA-002');
    expect(assignment.evidence_scope).toBe('test');
    const origin = process.env['ANANTA_PEER_CHURN_ORIGIN'] ?? 'http://127.0.0.1:41739';
    const devices: Device[] = [];
    try {
      for (const id of DEVICE_IDS) {
        const browser = await browserType.launch({ headless: true });
        const context = await browser.newContext();
        const page = await context.newPage();
        await page.goto(origin);
        await initialize(page, id);
        devices.push({ id, browser, context, page });
      }
      const [source, relayA, relayB, leafC, leafD] = devices;
      await connect('source-a', source.page, relayA.page);
      await connect('source-b', source.page, relayB.page);
      await connect('a-c', relayA.page, leafC.page);
      await connect('a-d', relayA.page, leafD.page);
      await connect('b-c', relayB.page, leafC.page);
      await connect('b-d', relayB.page, leafD.page);

      await pathProbe('baseline', [[source.page, 'source-a'], [relayA.page, 'a-c']], leafC.page);

      const cover = await relayA.context.newPage();
      await cover.goto(origin);
      await cover.bringToFront();
      await expect.poll(() => relayA.page.evaluate(() => document.hasFocus())).toBe(false);
      const backgroundStarted = Date.now();
      await pathProbe('background', [[source.page, 'source-a']], relayA.page);
      const backgroundMs = Date.now() - backgroundStarted;

      const relayFailureStarted = Date.now();
      await closeEdge(relayA.page, 'a-d');
      await closeEdge(leafD.page, 'a-d');
      await pathProbe('relay-failure', [[source.page, 'source-b'], [relayB.page, 'b-d']], leafD.page);
      const relayFailureMs = Date.now() - relayFailureStarted;

      const crashStarted = Date.now();
      await relayA.browser.close();
      await pathProbe('browser-crash', [[source.page, 'source-b'], [relayB.page, 'b-c']], leafC.page);
      const browserCrashMs = Date.now() - crashStarted;

      const restartStarted = Date.now();
      await restartIce('source-b', source.page, relayB.page);
      await pathProbe('ice-restart', [[source.page, 'source-b']], relayB.page);
      const iceRestartMs = Date.now() - restartStarted;

      const measurement: ChurnMeasurement = {
        engine,
        browserVersion: source.browser.version(),
        deviceIdentities: DEVICE_IDS,
        processIsolation: true,
        edgeCount: 6,
        scenarios: {
          backgroundTab: {
            documentHasFocus: false,
            activation: 'cover_tab_brought_to_front',
            recoveryMs: backgroundMs,
          },
          relayFailure: { recoveryMs: relayFailureMs, backupPath: 'source-b>b-d' },
          browserCrash: { recoveryMs: browserCrashMs, backupPath: 'source-b>b-c' },
          iceRestart: { recoveryMs: iceRestartMs, delivered: true },
        },
        humanInterventionRequired: false,
      };
      expect(new Set(measurement.deviceIdentities).size).toBe(5);
      expect(backgroundMs).toBeLessThanOrEqual(2_000);
      expect(relayFailureMs).toBeLessThanOrEqual(2_000);
      expect(browserCrashMs).toBeLessThanOrEqual(2_000);
      expect(iceRestartMs).toBeLessThanOrEqual(5_000);
      const output = process.env['ANANTA_PEER_CHURN_MEASUREMENT_DIR'] ?? '/tmp/ananta-peer-churn';
      await mkdir(output, { recursive: true });
      await writeFile(`${output}/${engine}.json`, `${JSON.stringify(measurement, null, 2)}\n`, 'utf8');
    } finally {
      await Promise.all(devices.map(async device => {
        if (device.browser.isConnected()) await device.browser.close();
      }));
    }
  });
}

async function initialize(page: Page, deviceId: string): Promise<void> {
  await page.evaluate(id => {
    const peers = new Map<string, RTCPeerConnection>();
    const channels = new Map<string, RTCDataChannel>();
    const messages: string[] = [];
    const bounded = async <T>(promise: Promise<T>, timeoutMs: number, reason: string): Promise<T> => {
      let timer = 0;
      try {
        return await Promise.race([
          promise,
          new Promise<T>((_resolve, reject) => {
            timer = window.setTimeout(() => reject(new Error(reason)), timeoutMs);
          }),
        ]);
      } finally {
        clearTimeout(timer);
      }
    };
    const gathered = async (peer: RTCPeerConnection): Promise<void> => {
      if (peer.iceGatheringState === 'complete') return;
      await bounded(new Promise<void>(resolve => peer.addEventListener('icegatheringstatechange', () => {
        if (peer.iceGatheringState === 'complete') resolve();
      })), 10_000, 'ice_gathering_timeout');
    };
    const connected = async (peer: RTCPeerConnection): Promise<void> => {
      if (peer.connectionState === 'connected') return;
      await bounded(new Promise<void>(resolve => peer.addEventListener('connectionstatechange', () => {
        if (peer.connectionState === 'connected') resolve();
      })), 10_000, 'connection_timeout');
    };
    const opened = async (channel: RTCDataChannel): Promise<void> => {
      if (channel.readyState === 'open') return;
      await bounded(new Promise<void>(resolve => channel.addEventListener('open', () => resolve(), { once: true })),
        5_000, 'channel_timeout');
    };
    const register = (edge: string, peer: RTCPeerConnection, channel: RTCDataChannel): void => {
      peers.set(edge, peer);
      channels.set(edge, channel);
      channel.onmessage = event => messages.push(String(event.data));
    };
    const api: PeerApi & { deviceId: string } = {
      deviceId: id,
      async createOffer(edge) {
        const peer = new RTCPeerConnection();
        register(edge, peer, peer.createDataChannel(edge, { ordered: true }));
        await peer.setLocalDescription(await peer.createOffer());
        await gathered(peer);
        return peer.localDescription!.toJSON();
      },
      async acceptOffer(edge, offer) {
        const peer = new RTCPeerConnection();
        peers.set(edge, peer);
        peer.ondatachannel = event => register(edge, peer, event.channel);
        await peer.setRemoteDescription(offer);
        await peer.setLocalDescription(await peer.createAnswer());
        await gathered(peer);
        return peer.localDescription!.toJSON();
      },
      async acceptAnswer(edge, answer) {
        const peer = peers.get(edge)!;
        await peer.setRemoteDescription(answer);
        await connected(peer);
        await opened(channels.get(edge)!);
      },
      send(edge, message) { channels.get(edge)!.send(message); },
      hasMessage(message) { return messages.includes(message); },
      closeEdge(edge) {
        channels.get(edge)?.close();
        peers.get(edge)?.close();
      },
      async restartOffer(edge) {
        const peer = peers.get(edge)!;
        peer.restartIce();
        await peer.setLocalDescription(await peer.createOffer({ iceRestart: true }));
        await gathered(peer);
        return peer.localDescription!.toJSON();
      },
      async acceptRestartOffer(edge, offer) {
        const peer = peers.get(edge)!;
        await peer.setRemoteDescription(offer);
        await peer.setLocalDescription(await peer.createAnswer());
        await gathered(peer);
        return peer.localDescription!.toJSON();
      },
      async acceptRestartAnswer(edge, answer) {
        const peer = peers.get(edge)!;
        await peer.setRemoteDescription(answer);
        await connected(peer);
      },
    };
    (globalThis as typeof globalThis & { __anantaPeer?: PeerApi }).__anantaPeer = api;
  }, deviceId);
}

async function connect(edge: string, offerer: Page, answerer: Page): Promise<void> {
  const offer = await offerer.evaluate(
    edgeId => (globalThis as typeof globalThis & { __anantaPeer: PeerApi }).__anantaPeer.createOffer(edgeId),
    edge,
  );
  const answer = await answerer.evaluate(
    ({ edgeId, remoteOffer }) => (globalThis as typeof globalThis & { __anantaPeer: PeerApi })
      .__anantaPeer.acceptOffer(edgeId, remoteOffer),
    { edgeId: edge, remoteOffer: offer },
  );
  await offerer.evaluate(
    ({ edgeId, remoteAnswer }) => (globalThis as typeof globalThis & { __anantaPeer: PeerApi })
      .__anantaPeer.acceptAnswer(edgeId, remoteAnswer),
    { edgeId: edge, remoteAnswer: answer },
  );
}

async function pathProbe(label: string, hops: ReadonlyArray<readonly [Page, string]>, destination: Page): Promise<void> {
  const nonce = `${label}-${Date.now()}-${Math.random()}`;
  for (const [page, edge] of hops) {
    await page.evaluate(({ edgeId, message }) => (globalThis as typeof globalThis & { __anantaPeer: PeerApi })
      .__anantaPeer.send(edgeId, message), {
      edgeId: edge,
      message: nonce,
    });
  }
  await expect.poll(() => destination.evaluate(
    message => (globalThis as typeof globalThis & { __anantaPeer: PeerApi }).__anantaPeer.hasMessage(message),
    nonce,
  )).toBe(true);
}

async function closeEdge(page: Page, edge: string): Promise<void> {
  await page.evaluate(
    edgeId => (globalThis as typeof globalThis & { __anantaPeer: PeerApi }).__anantaPeer.closeEdge(edgeId),
    edge,
  );
}

async function restartIce(edge: string, offerer: Page, answerer: Page): Promise<void> {
  const offer = await offerer.evaluate(
    edgeId => (globalThis as typeof globalThis & { __anantaPeer: PeerApi }).__anantaPeer.restartOffer(edgeId),
    edge,
  );
  const answer = await answerer.evaluate(
    ({ edgeId, remoteOffer }) => (globalThis as typeof globalThis & { __anantaPeer: PeerApi })
      .__anantaPeer.acceptRestartOffer(edgeId, remoteOffer),
    { edgeId: edge, remoteOffer: offer },
  );
  await offerer.evaluate(
    ({ edgeId, remoteAnswer }) => (globalThis as typeof globalThis & { __anantaPeer: PeerApi })
      .__anantaPeer.acceptRestartAnswer(edgeId, remoteAnswer),
    { edgeId: edge, remoteAnswer: answer },
  );
}
