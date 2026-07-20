import { chromium, expect, firefox, test } from '@playwright/test';
import type { APIRequestContext, Browser, BrowserContext, Page } from '@playwright/test';

type HubTransport = 'hub_relay' | 'webrtc';

type SemanticPeerHubFixture = {
  hubUrl: string;
  sessionId: string;
  epoch: number;
  senderId: string;
  recipientId: string;
  senderConsentDigest: string;
  recipientConsentDigest: string;
  scopeDigest: string;
  consentVersion: number;
  expiresAtMs: number;
  transport: HubTransport;
};

type SemanticPeerHubOffer = {
  offerId: string;
  inventoryRootDigest: string;
  groupId: string;
  groupB64: string;
  totalBytes: number;
  expiresAtMs: number;
  value: Record<string, unknown>;
  signedMessage: Record<string, unknown>;
  record: {
    groupPreviewDigest: string;
  } & Record<string, unknown>;
};

type SemanticPeerHubAcceptanceResult = {
  signedPreviewVisible: boolean;
  comparisonPreviewVisible: boolean;
  productAcceptPolicyUsed: boolean;
  productUiProjectionVisible: boolean;
};

type SemanticPeerHubTransferExerciseResult = {
  backendRouteCount: number;
  forcedRelayUsed: boolean;
  duplicateRejected: boolean;
  reorderRecovered: boolean;
};

type SemanticPeerHubCurationResult = {
  receiptVerified: boolean;
  state: string;
  acceptedGroupCount: number;
  curationTaskQueued: boolean;
  datasetReserved: boolean;
};

type SemanticVoiceObservationCursor = {
  hubUrl: string;
  profileId: string;
  streamId: string;
  liveRunId: string;
  partialLatencyMs: number;
  partialObserved: boolean;
};

type SemanticVoiceObservationResult = {
  partialLatencyMs: number;
  partialObservationCount: number;
  segmentModeCount: number;
  finalSegmentCount: number;
  correctionAfterFinalCount: number;
  acceleratedRotationCount: number;
  reconnectCount: number;
  stream404Count: number;
  stop409Count: number;
  chunk413Count: number;
  backpressureCount: number;
  ordinaryFallbackCount: number;
  transcriptContinuityCount: number;
};

type SemanticMediaPairHubProductDriver = {
  prepare(fixture: SemanticPeerHubFixture): Promise<void>;
  propose(fixture: SemanticPeerHubFixture, relayCanary: string): Promise<SemanticPeerHubOffer>;
  accept(
    fixture: SemanticPeerHubFixture,
    offer: SemanticPeerHubOffer,
  ): Promise<SemanticPeerHubAcceptanceResult>;
  exerciseTransfer(
    fixture: SemanticPeerHubFixture,
    offer: SemanticPeerHubOffer,
  ): Promise<SemanticPeerHubTransferExerciseResult>;
  recover(fixture: SemanticPeerHubFixture, offer: SemanticPeerHubOffer): Promise<boolean>;
  revoke(fixture: SemanticPeerHubFixture, offer: SemanticPeerHubOffer): Promise<boolean>;
  acknowledge(fixture: SemanticPeerHubFixture, offer: SemanticPeerHubOffer): Promise<void>;
  curate(
    fixture: SemanticPeerHubFixture,
    offer: SemanticPeerHubOffer,
  ): Promise<SemanticPeerHubCurationResult>;
  openDirect(fixture: SemanticPeerHubFixture, initiator: boolean): Promise<'webrtc'>;
  sendDirect(fixture: SemanticPeerHubFixture, directCanary: string): Promise<string>;
  receiveDirect(messageId: string): Promise<boolean>;
  closeDirect(): Promise<void>;
  startVoiceObservation(hubUrl: string): Promise<SemanticVoiceObservationCursor>;
  resumeVoiceObservation(cursor: SemanticVoiceObservationCursor): Promise<SemanticVoiceObservationResult>;
};

declare global {
  interface Window {
    __ANANTA_SEMANTIC_MEDIA_E2E__?: {
      pair: { hub: SemanticMediaPairHubProductDriver };
    };
  }
}

test.describe('semantic media pair product-path conformance', () => {
  test.skip(
    process.env['RUN_SEMANTIC_MEDIA_LIVE_E2E'] !== '1',
    'live pair evidence is mandatory for release and is never simulated',
  );

  test('uses product WebRTC, facade and Hub routes across independent engines', async ({ request }, testInfo) => {
    const baseUrl = String(testInfo.project.use.baseURL || 'http://127.0.0.1:4200');
    const directSeed = await seedHubPeerSync(request, 'webrtc');
    const relaySeed = await seedHubPeerSync(request, 'hub_relay');
    const senderBrowser = await chromium.launch();
    const recipientBrowser = await firefox.launch();
    const engines = new Set([
      senderBrowser.browserType().name(),
      recipientBrowser.browserType().name(),
    ]);
    const routeStatuses: number[] = [];
    let directContexts: readonly BrowserContext[] = [];
    let relayContexts: readonly BrowserContext[] = [];
    try {
      directContexts = await contextsForSeed(senderBrowser, recipientBrowser, directSeed);
      const [directSender, directRecipient] = await pagesForContexts(directContexts, baseUrl);
      await expectDrivers(directSender, directRecipient);
      const receiverOpening = callHub(directRecipient, (hub, fixture) => hub.openDirect(fixture, false), directSeed.fixture);
      await directSender.waitForTimeout(100);
      const senderOpening = callHub(directSender, (hub, fixture) => hub.openDirect(fixture, true), directSeed.fixture);
      expect(await Promise.all([senderOpening, receiverOpening])).toEqual(['webrtc', 'webrtc']);
      const directMessageId = await callHub(
        directSender,
        (hub, value) => hub.sendDirect(value.fixture, value.canary),
        { fixture: directSeed.fixture, canary: requiredCanary('ANANTA_PAIR_DIRECT_CANARY', 'ANANTA_DIRECT_CANARY_') },
      );
      const directDelivered = await callHub(
        directRecipient,
        (hub, messageId) => hub.receiveDirect(messageId),
        directMessageId,
      );
      expect(directDelivered).toBe(true);
      await Promise.all([
        callHub(directSender, hub => hub.closeDirect(), null),
        callHub(directRecipient, hub => hub.closeDirect(), null),
      ]);

      relayContexts = await contextsForSeed(senderBrowser, recipientBrowser, relaySeed);
      for (const context of relayContexts) {
        context.on('response', response => {
          if (response.url().includes('/v1/voice/')) {
            routeStatuses.push(response.status());
          }
        });
      }
      const [relaySender, relayRecipient] = await pagesForContexts(relayContexts, baseUrl);
      await expectDrivers(relaySender, relayRecipient);
      await Promise.all([
        callHub(relaySender, (hub, fixture) => hub.prepare(fixture), relaySeed.fixture),
        callHub(relayRecipient, (hub, fixture) => hub.prepare(fixture), relaySeed.fixture),
      ]);
      const offer = await callHub(
        relaySender,
        (hub, value) => hub.propose(value.fixture, value.canary),
        { fixture: relaySeed.fixture, canary: requiredCanary('ANANTA_PAIR_RELAY_CANARY', 'ANANTA_RELAY_CANARY_') },
      );
      const acceptance = await callHub(
        relayRecipient,
        (hub, value) => hub.accept(value.fixture, value.offer),
        { fixture: relaySeed.fixture, offer },
      );
      expect(acceptance).toEqual({
        signedPreviewVisible: true,
        comparisonPreviewVisible: true,
        productAcceptPolicyUsed: true,
        productUiProjectionVisible: true,
      });
      const transfer = await callHub(
        relaySender,
        (hub, value) => hub.exerciseTransfer(value.fixture, value.offer),
        { fixture: relaySeed.fixture, offer },
      );
      expect(transfer).toEqual(expect.objectContaining({
        forcedRelayUsed: true,
        duplicateRejected: true,
        reorderRecovered: true,
      }));

      const voiceCursor = await callHub(
        relayRecipient,
        (hub, hubUrl) => hub.startVoiceObservation(hubUrl),
        relaySeed.hubUrl,
      );

      await relayRecipient.reload();
      await expectDriver(relayRecipient);
      const reconnectRecovered = await callHub(
        relayRecipient,
        (hub, value) => hub.recover(value.fixture, value.offer),
        { fixture: relaySeed.fixture, offer },
      );
      expect(reconnectRecovered).toBe(true);
      const voice = await callHub(
        relayRecipient,
        (hub, cursor) => hub.resumeVoiceObservation(cursor),
        voiceCursor,
      );
      await callHub(relayRecipient, (hub, fixture) => hub.prepare(fixture), relaySeed.fixture);
      await callHub(
        relayRecipient,
        (hub, value) => hub.acknowledge(value.fixture, value.offer),
        { fixture: relaySeed.fixture, offer },
      );
      const curation = await callHub(
        relayRecipient,
        (hub, value) => hub.curate(value.fixture, value.offer),
        { fixture: relaySeed.fixture, offer },
      );
      expect(curation.receiptVerified).toBe(true);
      expect(curation.curationTaskQueued).toBe(true);
      expect(curation.datasetReserved).toBe(true);
      expect(['admitted', 'dataset_published']).toContain(curation.state);
      const revoked = await callHub(
        relaySender,
        (hub, value) => hub.revoke(value.fixture, value.offer),
        { fixture: relaySeed.fixture, offer },
      );
      expect(revoked).toBe(true);

      // This annotation is derived from product services and observed Hub
      // responses. In particular, a runtime without a native incremental ASR
      // emits no partial observation and therefore keeps the release closed.
      testInfo.annotations.push({
        type: 'semantic-peer-product-path-v2',
        description: JSON.stringify({
          browser_processes: 2,
          browser_engines: engines.size,
          product_facade_count: Number(acceptance.productAcceptPolicyUsed),
          backend_route_count: routeStatuses.length,
          p2p_product_delivery_count: Number(directDelivered),
          forced_relay_delivery_count: Number(transfer.forcedRelayUsed),
          duplicate_rejection_count: Number(transfer.duplicateRejected),
          reorder_recovery_count: Number(transfer.reorderRecovered),
          reconnect_resume_count: Number(reconnectRecovered),
          signed_preview_visible_count: Number(acceptance.signedPreviewVisible),
          comparison_preview_visible_count: Number(acceptance.comparisonPreviewVisible),
          product_ui_projection_count: Number(acceptance.productUiProjectionVisible),
          hub_curation_count: Number(['admitted', 'dataset_published'].includes(curation.state)),
          hub_receipt_verified_count: Number(curation.receiptVerified),
          hub_dataset_reservation_count: Number(curation.datasetReserved),
          revoke_ack_count: Number(revoked),
          live_revision_continuity_count: voice.transcriptContinuityCount,
          plaintext_canary_probe_count: Number(directDelivered) + Number(transfer.forcedRelayUsed),
          synthetic_harness_count: 0,
          partial_latency_ms: voice.partialLatencyMs,
          partial_observation_count: voice.partialObservationCount,
          segment_mode_count: voice.segmentModeCount,
          final_segment_count: voice.finalSegmentCount,
          correction_after_final_count: voice.correctionAfterFinalCount,
          accelerated_rotation_count: voice.acceleratedRotationCount,
          voice_reconnect_count: voice.reconnectCount,
          observed_stream_404_count: voice.stream404Count,
          observed_stop_409_count: voice.stop409Count,
          observed_chunk_413_count: voice.chunk413Count,
          backpressure_count: voice.backpressureCount,
          ordinary_fallback_count: voice.ordinaryFallbackCount,
          observed_error_response_count: routeStatuses.filter(status => status >= 400).length,
        }),
      });
    } finally {
      await Promise.all([...directContexts, ...relayContexts].map(context => context.close().catch(() => undefined)));
      await Promise.all([senderBrowser.close(), recipientBrowser.close()]);
    }
  });
});

type SemanticPeerHubSeed = {
  hubUrl: string;
  senderToken: string;
  recipientToken: string;
  fixture: SemanticPeerHubFixture;
};

async function seedHubPeerSync(
  request: APIRequestContext,
  transport: HubTransport,
): Promise<SemanticPeerHubSeed> {
  const hubUrl = String(process.env['E2E_HUB_URL'] || 'http://127.0.0.1:5500').replace(/\/+$/, '');
  const login = await request.post(`${hubUrl}/login`, {
    data: {
      username: process.env['E2E_ADMIN_USER'] || 'admin',
      password: process.env['E2E_ADMIN_PASSWORD'] || 'test123',
    },
  });
  if (!login.ok()) throw new Error(`semantic_peer_seed_login_failed_${login.status()}`);
  const loginBody = await login.json() as { data?: { access_token?: unknown }; access_token?: unknown };
  const adminToken = String(loginBody.data?.access_token || loginBody.access_token || '');
  const seeded = await request.post(`${hubUrl}/test/semantic-media/peer-sync-seed`, {
    headers: { Authorization: `Bearer ${adminToken}` },
    data: { transport },
  });
  if (!seeded.ok()) throw new Error(`semantic_peer_seed_request_failed_${seeded.status()}`);
  const body = await seeded.json() as { data?: Record<string, unknown> };
  const value = body.data || {};
  const fixture: SemanticPeerHubFixture = {
    hubUrl: String(value['hub_url'] || ''),
    sessionId: String(value['session_id'] || ''),
    epoch: Number(value['epoch']),
    senderId: String(value['sender_id'] || ''),
    recipientId: String(value['recipient_id'] || ''),
    senderConsentDigest: String(value['sender_consent_digest'] || ''),
    recipientConsentDigest: String(value['recipient_consent_digest'] || ''),
    scopeDigest: String(value['scope_digest'] || ''),
    consentVersion: Number(value['consent_version']),
    expiresAtMs: Number(value['expires_at_ms']),
    transport: String(value['transport'] || '') as HubTransport,
  };
  const senderToken = String(value['sender_token'] || '');
  const recipientToken = String(value['recipient_token'] || '');
  if (
    fixture.hubUrl !== hubUrl
    || fixture.transport !== transport
    || !fixture.sessionId
    || fixture.epoch < 1
    || !fixture.senderId
    || !fixture.recipientId
    || !/^[0-9a-f]{64}$/.test(fixture.senderConsentDigest)
    || !/^[0-9a-f]{64}$/.test(fixture.recipientConsentDigest)
    || !/^[0-9a-f]{64}$/.test(fixture.scopeDigest)
    || fixture.consentVersion !== 1
    || fixture.expiresAtMs <= Date.now()
    || !senderToken
    || !recipientToken
  ) throw new Error('semantic_peer_seed_response_invalid');
  return { hubUrl, senderToken, recipientToken, fixture };
}

async function contextsForSeed(
  senderBrowser: Browser,
  recipientBrowser: Browser,
  seed: SemanticPeerHubSeed,
): Promise<readonly [BrowserContext, BrowserContext]> {
  const sender = await senderBrowser.newContext();
  const recipient = await recipientBrowser.newContext();
  await Promise.all([
    installHubIdentity(sender, seed.hubUrl, seed.senderToken),
    installHubIdentity(recipient, seed.hubUrl, seed.recipientToken),
  ]);
  return [sender, recipient];
}

async function installHubIdentity(context: BrowserContext, hubUrl: string, token: string): Promise<void> {
  await context.addInitScript(({ targetHub, userToken }) => {
    localStorage.setItem('ananta.user.token', userToken);
    localStorage.setItem('ananta.agents.v1', JSON.stringify([
      { name: 'hub', url: targetHub, token: '', role: 'hub' },
    ]));
  }, { targetHub: hubUrl, userToken: token });
}

async function pagesForContexts(
  contexts: readonly BrowserContext[],
  baseUrl: string,
): Promise<readonly [Page, Page]> {
  const pages = await Promise.all(contexts.map(context => context.newPage()));
  await Promise.all(pages.map(page => page.goto(`${baseUrl}/voice?semanticMediaLiveE2e=1`)));
  return [pages[0], pages[1]];
}

async function expectDrivers(...pages: readonly Page[]): Promise<void> {
  await Promise.all(pages.map(expectDriver));
}

async function expectDriver(page: Page): Promise<void> {
  await expect.poll(() => page.evaluate(() => Boolean(window.__ANANTA_SEMANTIC_MEDIA_E2E__?.pair.hub))).toBe(true);
}

async function callHub<TArg, TResult>(
  page: Page,
  operation: (hub: SemanticMediaPairHubProductDriver, value: TArg) => Promise<TResult>,
  value: TArg,
): Promise<TResult> {
  return page.evaluate(async ({ operationSource, argument }) => {
    const hub = window.__ANANTA_SEMANTIC_MEDIA_E2E__?.pair.hub;
    if (!hub) throw new Error('semantic_peer_product_driver_missing');
    const invoke = new Function('hub', 'value', `return (${operationSource})(hub, value);`) as (
      driver: SemanticMediaPairHubProductDriver,
      input: TArg,
    ) => Promise<TResult>;
    return invoke(hub, argument);
  }, { operationSource: operation.toString(), argument: value });
}

function requiredCanary(name: string, prefix: string): string {
  const value = String(process.env[name] || '');
  if (!value.startsWith(prefix) || !/^[A-Za-z0-9_]{48,128}$/.test(value)) {
    throw new Error('semantic_peer_privacy_canary_missing');
  }
  return value;
}
