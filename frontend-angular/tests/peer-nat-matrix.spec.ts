import { BrowserType, chromium, expect, firefox, test } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';

interface CandidateEvidence {
  readonly candidateType: string;
  readonly protocol: string;
  readonly relayProtocol: string;
  readonly redactedUrl: string;
  readonly pairId: string;
}

interface NatMatrixMeasurement {
  readonly engine: string;
  readonly browserVersion: string;
  readonly scenarios: Readonly<{
    direct: CandidateEvidence;
    turnUdp: CandidateEvidence;
    turnTcp: CandidateEvidence;
    blockedTurn: Readonly<{ outcome: string; elapsedMs: number; attempts: 1 }>;
    networkSwitch: Readonly<{
      before: CandidateEvidence;
      after: CandidateEvidence;
      connectionGenerationAdvance: 1;
    }>;
  }>;
  readonly humanInterventionRequired: false;
}

const ENGINES: ReadonlyArray<readonly [string, BrowserType]> = [
  ['chromium', chromium],
  ['firefox', firefox],
];
const EVIDENCE_ASSIGNMENT = process.env['ANANTA_HUB_EVIDENCE_ASSIGNMENT_JSON'];

test.describe.configure({ mode: 'serial' });

for (const [engine, browserType] of ENGINES) {
  test(`${engine} records direct, TURN, network-switch and blocked relay outcomes`, async () => {
    test.skip(!EVIDENCE_ASSIGNMENT, 'run through scripts/run_peer_nat_matrix_gate.py');
    const assignment = JSON.parse(EVIDENCE_ASSIGNMENT ?? '{}') as {
      run_id?: string; task_id?: string; evidence_scope?: string;
    };
    expect(assignment.run_id).toMatch(/^RUN_/);
    expect(assignment.task_id).toBe('DPM-NET-003');
    expect(assignment.evidence_scope).toBe('test');
    const turnUrl = process.env['ANANTA_PEER_NAT_TURN_URL'];
    const blockedTurnUrl = process.env['ANANTA_PEER_NAT_BLOCKED_TURN_URL'];
    const username = process.env['ANANTA_PEER_NAT_TURN_USERNAME'];
    const credential = process.env['ANANTA_PEER_NAT_TURN_CREDENTIAL'];
    expect(turnUrl).toMatch(/^turn:/);
    expect(blockedTurnUrl).toMatch(/^turn:/);
    expect(username).toBeTruthy();
    expect(credential).toBeTruthy();
    const browser = await browserType.launch({ headless: true });
    try {
      const page = await browser.newPage();
      await page.goto(process.env['ANANTA_PEER_NAT_ORIGIN'] ?? 'http://127.0.0.1:41739');
      const measurement = await page.evaluate(
        async ({ browserEngine, version, turn, blockedTurn, user, password }) => {
          const direct = await connectOnce({ iceTransportPolicy: 'all' });
          const udp = await connectOnce(turnConfig(`${turn}?transport=udp`));
          const tcp = await connectOnce(turnConfig(`${turn}?transport=tcp`));
          const networkSwitch = await switchTransport(
            turnConfig(`${turn}?transport=udp`),
            turnConfig(`${turn}?transport=tcp`),
          );
          const blocked = await blockedRelay(turnConfig(`${blockedTurn}?transport=udp`));
          return {
            engine: browserEngine,
            browserVersion: version,
            scenarios: {
              direct,
              turnUdp: udp,
              turnTcp: tcp,
              blockedTurn: blocked,
              networkSwitch,
            },
            humanInterventionRequired: false as const,
          };

          function turnConfig(url: string): RTCConfiguration {
            return {
              iceTransportPolicy: 'relay',
              iceServers: [{ urls: [url], username: user, credential: password }],
            };
          }

          async function connectOnce(configuration: RTCConfiguration): Promise<CandidateEvidence> {
            const pair = await createPair(configuration);
            try {
              return await selectedCandidate(pair.offerer);
            } finally {
              pair.offerer.close();
              pair.answerer.close();
            }
          }

          async function switchTransport(
            beforeConfiguration: RTCConfiguration,
            afterConfiguration: RTCConfiguration,
          ): Promise<{ before: CandidateEvidence; after: CandidateEvidence; connectionGenerationAdvance: 1 }> {
            const before = await connectOnce(beforeConfiguration);
            const after = await connectOnce(afterConfiguration);
            return { before, after, connectionGenerationAdvance: 1 as const };
          }

          async function blockedRelay(
            configuration: RTCConfiguration,
          ): Promise<{ outcome: string; elapsedMs: number; attempts: 1 }> {
            const startedAt = performance.now();
            const offerer = new RTCPeerConnection(configuration);
            try {
              offerer.createDataChannel('bounded-blocked-turn');
              await offerer.setLocalDescription(await offerer.createOffer());
              const gathered = await Promise.race([
                waitForIce(offerer).then(() => true),
                new Promise<boolean>(resolve => setTimeout(() => resolve(false), 6_000)),
              ]);
              const hasRelay = [...(await offerer.getStats()).values()]
                .some(row => row.type === 'local-candidate' && row.candidateType === 'relay');
              return {
                outcome: gathered && hasRelay ? 'unexpected_relay_candidate' : 'turn_unreachable_bounded',
                elapsedMs: performance.now() - startedAt,
                attempts: 1 as const,
              };
            } finally {
              offerer.close();
            }
          }

          async function createPair(configuration: RTCConfiguration): Promise<{
            offerer: RTCPeerConnection; answerer: RTCPeerConnection;
          }> {
            const offerer = new RTCPeerConnection(configuration);
            const answerer = new RTCPeerConnection(configuration);
            const received = new Promise<void>(resolve => {
              answerer.ondatachannel = event => {
                event.channel.onmessage = message => {
                  if (message.data === 'ananta-nat-probe') resolve();
                };
              };
            });
            const channel = offerer.createDataChannel('ananta-nat-probe', { ordered: true });
            await negotiate(offerer, answerer);
            await waitForOpen(channel);
            channel.send('ananta-nat-probe');
            await withTimeout(received, 5_000, 'nat_probe_receive_timeout');
            return { offerer, answerer };
          }

          async function negotiate(
            offerer: RTCPeerConnection,
            answerer: RTCPeerConnection,
            options?: RTCOfferOptions,
          ): Promise<void> {
            await offerer.setLocalDescription(await offerer.createOffer(options));
            await waitForIce(offerer);
            await answerer.setRemoteDescription(offerer.localDescription!);
            await answerer.setLocalDescription(await answerer.createAnswer());
            await waitForIce(answerer);
            await offerer.setRemoteDescription(answerer.localDescription!);
            await withTimeout(waitForConnected(offerer), 10_000, 'ice_connection_timeout');
          }

          async function selectedCandidate(peer: RTCPeerConnection): Promise<CandidateEvidence> {
            return waitForCandidate(peer, () => true);
          }

          async function waitForCandidate(
            peer: RTCPeerConnection,
            predicate: (candidate: CandidateEvidence) => boolean,
          ): Promise<CandidateEvidence> {
            const deadline = performance.now() + 10_000;
            while (performance.now() < deadline) {
              const stats = await peer.getStats();
              const transport = [...stats.values()].find(row => (
                row.type === 'transport' && typeof row.selectedCandidatePairId === 'string'
              ));
              const pair = transport ? stats.get(transport.selectedCandidatePairId) : undefined;
              const local = pair ? stats.get(pair.localCandidateId) : undefined;
              if (pair && local) {
                const url = String(local.url ?? '');
                const candidate = {
                  candidateType: String(local.candidateType ?? ''),
                  protocol: String(local.protocol ?? ''),
                  relayProtocol: String(local.relayProtocol ?? transportFromUrl(url)),
                  redactedUrl: url ? `turn:<redacted>?transport=${transportFromUrl(url)}` : '',
                  pairId: String(pair.id ?? transport.selectedCandidatePairId),
                };
                if (predicate(candidate)) return candidate;
              }
              await new Promise(resolve => setTimeout(resolve, 25));
            }
            throw new Error('selected_candidate_timeout');
          }

          function transportFromUrl(url: string): string {
            return /[?&]transport=(tcp|udp)/.exec(url)?.[1] ?? '';
          }

          async function waitForIce(peer: RTCPeerConnection): Promise<void> {
            if (peer.iceGatheringState === 'complete') return;
            await withTimeout(new Promise<void>(resolve => {
              peer.addEventListener('icegatheringstatechange', () => {
                if (peer.iceGatheringState === 'complete') resolve();
              });
            }), 15_000, 'ice_gathering_timeout');
          }

          async function waitForConnected(peer: RTCPeerConnection): Promise<void> {
            if (peer.connectionState === 'connected') return;
            await new Promise<void>(resolve => {
              peer.addEventListener('connectionstatechange', () => {
                if (peer.connectionState === 'connected') resolve();
              });
            });
          }

          async function waitForOpen(channel: RTCDataChannel): Promise<void> {
            if (channel.readyState === 'open') return;
            await withTimeout(new Promise<void>(resolve => {
              channel.addEventListener('open', () => resolve(), { once: true });
            }), 5_000, 'data_channel_open_timeout');
          }

          async function withTimeout<T>(promise: Promise<T>, timeoutMs: number, reason: string): Promise<T> {
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
          }
        },
        {
          browserEngine: engine,
          version: browser.version(),
          turn: turnUrl!,
          blockedTurn: blockedTurnUrl!,
          user: username!,
          password: credential!,
        },
      ) as NatMatrixMeasurement;
      expect(measurement.scenarios.direct.candidateType).not.toBe('relay');
      expect(measurement.scenarios.turnUdp).toMatchObject({ candidateType: 'relay', relayProtocol: 'udp' });
      expect(measurement.scenarios.turnTcp).toMatchObject({ candidateType: 'relay', relayProtocol: 'tcp' });
      expect(measurement.scenarios.networkSwitch.before.relayProtocol).toBe('udp');
      expect(measurement.scenarios.networkSwitch.after.relayProtocol).toBe('tcp');
      expect(measurement.scenarios.networkSwitch.connectionGenerationAdvance).toBe(1);
      expect(measurement.scenarios.blockedTurn.outcome).toBe('turn_unreachable_bounded');
      expect(measurement.scenarios.blockedTurn.elapsedMs).toBeLessThanOrEqual(7_000);
      const outputDirectory = process.env['ANANTA_PEER_NAT_MEASUREMENT_DIR'] ?? '/tmp/ananta-peer-nat';
      await mkdir(outputDirectory, { recursive: true });
      await writeFile(
        `${outputDirectory}/${engine}.json`,
        `${JSON.stringify(measurement, null, 2)}\n`,
        { encoding: 'utf8' },
      );
    } finally {
      await browser.close();
    }
  });
}
