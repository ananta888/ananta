import { chromium, expect, firefox, test } from '@playwright/test';
import type { Browser, BrowserContext, Page, Response } from '@playwright/test';

import { PUBLIC_OIDC_ISSUER } from '../src/app/services/public-ananta-endpoints';

const LIVE_GATE = process.env['RUN_PUBLIC_PAIR_MEDIA_LIVE_E2E'] === '1';
const DEVICE_PEER_ID = /^peer:[a-f0-9]{64}$/;
const DEVICE_ID = /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/;
const PAGE_ERROR_CODES = new WeakMap<Page, string[]>();

interface LiveCredentials {
  readonly username: string;
  readonly password: string;
}

interface PairMutationEvidence {
  readonly sessionId: string;
  readonly inviteCode: string;
  readonly localPeerId: string;
}

test.describe('Public Pair media live canary', () => {
  test.skip(
    !LIVE_GATE,
    'Set RUN_PUBLIC_PAIR_MEDIA_LIVE_E2E=1 and provide the existing Keycloak test credentials.',
  );

  test('connects distinct same-account Chromium and Firefox peers with bilateral E2EE media', async ({}, testInfo) => {
    const credentials = requiredCredentials();
    const baseURL = requiredBaseURL(testInfo.project.use.baseURL);
    const browsers = await launchCanaryBrowsers();
    let ownerContext: BrowserContext | null = null;
    let guestContext: BrowserContext | null = null;
    let ownerPage: Page | null = null;
    let guestPage: Page | null = null;
    let sessionCreated = false;

    try {
      [ownerContext, guestContext] = await Promise.all([
        createMediaContext(browsers.chromium, baseURL),
        createMediaContext(browsers.firefox, baseURL),
      ]);
      ownerPage = await authenticatedPairPage(ownerContext, baseURL, credentials);
      guestPage = await authenticatedPairPage(guestContext, baseURL, credentials);
      await Promise.all([assertMediaRuntime(ownerPage), assertMediaRuntime(guestPage)]);

      const ownerDeviceId = await opaqueDeviceId(ownerPage);
      const guestDeviceId = await opaqueDeviceId(guestPage);
      requireCondition(ownerDeviceId !== guestDeviceId, 'pair_devices_must_be_distinct');

      const owner = await createFreshPublicSession(ownerPage);
      sessionCreated = true;
      const guest = await joinFreshPublicSession(guestPage, owner.inviteCode, owner.sessionId);
      requireCondition(owner.localPeerId !== guest.localPeerId, 'pair_peers_must_be_distinct');

      await Promise.all([
        expectPairSecurityReady(ownerPage, 'owner'),
        expectPairSecurityReady(guestPage, 'guest'),
      ]);
      await grantMediaPublicationBilateral(ownerPage, guestPage);
      await startBilateralCapture(ownerPage, guestPage);
      await expectBilateralReception(ownerPage, guestPage);

      await revokeAndRegrantOwnerPublication(ownerPage, guestPage);

      await endOwnerSession(ownerPage, owner.sessionId);
      sessionCreated = false;
      await bestEffortLeaveGuestSession(guestPage);
    } finally {
      if (sessionCreated && ownerPage) await bestEffortEndOwnerSession(ownerPage);
      if (guestPage) await bestEffortLeaveGuestSession(guestPage);
      await Promise.all([
        ownerContext?.close().catch(() => undefined),
        guestContext?.close().catch(() => undefined),
      ]);
      await Promise.all([
        browsers.chromium.close().catch(() => undefined),
        browsers.firefox.close().catch(() => undefined),
      ]);
    }
  });
});

function requiredCredentials(): LiveCredentials {
  const username = String(process.env['E2E_OIDC_USERNAME'] || '').trim();
  const password = String(process.env['E2E_OIDC_PASSWORD'] || '');
  requireCondition(Boolean(username && password), 'public_oidc_credentials_required');
  return Object.freeze({ username, password });
}

function requiredBaseURL(value: unknown): string {
  const raw = String(value || '').trim();
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error('public_pair_media_base_url_invalid');
  }
  const loopback = parsed.hostname === '127.0.0.1' || parsed.hostname === 'localhost';
  requireCondition(parsed.protocol === 'https:' || loopback, 'public_pair_media_secure_context_required');
  requireCondition(parsed.username === '' && parsed.password === '', 'public_pair_media_base_url_credentials_forbidden');
  return parsed.origin;
}

async function launchCanaryBrowsers(): Promise<Readonly<{ chromium: Browser; firefox: Browser }>> {
  const headless = process.env['E2E_HEADED'] !== '1';
  const chromiumBrowser = await chromium.launch({
    headless,
    args: [
      '--autoplay-policy=no-user-gesture-required',
      '--use-fake-device-for-media-stream',
      '--use-fake-ui-for-media-stream',
    ],
  });
  try {
    const firefoxBrowser = await firefox.launch({
      headless,
      firefoxUserPrefs: {
        'media.autoplay.blocking_policy': 0,
        'media.autoplay.default': 0,
        'media.navigator.permission.disabled': true,
        'media.navigator.streams.fake': true,
      },
    });
    return Object.freeze({ chromium: chromiumBrowser, firefox: firefoxBrowser });
  } catch (error) {
    await chromiumBrowser.close();
    throw error;
  }
}

async function createMediaContext(browser: Browser, baseURL: string): Promise<BrowserContext> {
  const context = await browser.newContext({ baseURL });
  await context.addInitScript(installSyntheticDisplayCapture, { appOrigin: baseURL });
  return context;
}

function installSyntheticDisplayCapture({ appOrigin }: { appOrigin: string }): void {
  if (location.origin !== appOrigin) return;
  const devices = navigator.mediaDevices;
  if (!devices || typeof HTMLCanvasElement.prototype.captureStream !== 'function') return;
  Object.defineProperty(devices, 'getDisplayMedia', {
    configurable: true,
    value: async (): Promise<MediaStream> => {
      const canvas = document.createElement('canvas');
      canvas.width = 640;
      canvas.height = 360;
      const drawing = canvas.getContext('2d');
      if (!drawing) throw new Error('synthetic_display_canvas_unavailable');
      let frame = 0;
      const paint = () => {
        frame = (frame + 1) % 360;
        drawing.fillStyle = `hsl(${frame} 70% 35%)`;
        drawing.fillRect(0, 0, canvas.width, canvas.height);
        drawing.fillStyle = '#ffffff';
        drawing.fillRect((frame * 3) % canvas.width, 120, 80, 80);
      };
      paint();
      const timer = window.setInterval(paint, 100);
      const stream = canvas.captureStream(10);
      const track = stream.getVideoTracks()[0];
      if (!track) {
        window.clearInterval(timer);
        throw new Error('synthetic_display_track_unavailable');
      }
      const nativeStop = track.stop.bind(track);
      track.stop = () => {
        window.clearInterval(timer);
        nativeStop();
      };
      return stream;
    },
  });
}

async function authenticatedPairPage(
  context: BrowserContext,
  baseURL: string,
  credentials: LiveCredentials,
): Promise<Page> {
  const page = await context.newPage();
  const pageErrorCodes: string[] = [];
  PAGE_ERROR_CODES.set(page, pageErrorCodes);
  page.on('pageerror', error => pageErrorCodes.push(classifyPageError(error)));
  await page.goto(`${baseURL}/login?sphere=oidc`, { waitUntil: 'domcontentloaded' });
  const keycloakButton = page.getByRole('button', { name: /Bei Keycloak anmelden/i });
  if (!await keycloakButton.isEnabled().catch(() => false)) {
    const optIn = page.getByRole('button', { name: /Öffentlichen Pair-\/WebRTC-Zugang aktivieren/i });
    await expect(optIn).toBeVisible();
    await optIn.click();
    await page.waitForFunction(() => (
      localStorage.getItem('ananta.network-profile-selection.v1') === 'public-ananta'
    ));
    await page.reload({ waitUntil: 'domcontentloaded' });
  }
  await expect(keycloakButton).toBeVisible();
  await expect(keycloakButton).toBeEnabled();
  await keycloakButton.click();
  const issuer = String(process.env['E2E_OIDC_ISSUER'] || PUBLIC_OIDC_ISSUER).replace(/\/$/, '');
  await page.waitForURL(url => url.href.startsWith(`${issuer}/`), { timeout: 120_000 });
  await submitKeycloakCredentials(page, credentials);

  const oidcReturn = page.waitForURL(url => url.origin === baseURL, { timeout: 180_000 })
    .then(() => 'returned' as const);
  const loginRejected = page.locator('#input-error, #kc-error-message, .alert-error').first()
    .waitFor({ state: 'visible', timeout: 180_000 })
    .then(() => 'rejected' as const);
  requireCondition(await Promise.race([oidcReturn, loginRejected]) === 'returned', 'public_oidc_login_rejected');
  await page.waitForFunction(() => Boolean(localStorage.getItem('ananta.oidc.access_token')), undefined, {
    timeout: 60_000,
  });
  await page.waitForURL(url => url.origin === baseURL && url.pathname === '/pair-dev', {
    timeout: 60_000,
  });
  await openPairPanel(page);
  return page;
}

async function submitKeycloakCredentials(authPage: Page, credentials: LiveCredentials): Promise<void> {
  const username = authPage.locator('#username, input[name="username"]').first();
  const password = authPage.locator('#password, input[name="password"]').first();
  await expect(username).toBeVisible();
  await expect(password).toBeVisible();
  await username.fill(credentials.username);
  await password.fill(credentials.password);
  const submit = authPage.locator('#kc-login, button[type="submit"], input[type="submit"]').first();
  await expect(submit).toBeVisible();
  await submit.click();
}

async function openPairPanel(page: Page): Promise<void> {
  if (new URL(page.url()).pathname !== '/pair-dev') {
    await page.goto('/pair-dev', { waitUntil: 'domcontentloaded' });
  }
  await expect(page.getByTestId('public-pair-page')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Audio und Video für Pair Dev' })).toBeVisible();
}

async function opaqueDeviceId(page: Page): Promise<string> {
  const value = await page.evaluate(() => localStorage.getItem('ananta.pair-device-id.v1') || '');
  requireCondition(DEVICE_ID.test(value), 'pair_device_id_invalid');
  return value;
}

async function assertMediaRuntime(page: Page): Promise<void> {
  const supported = await page.evaluate(() => {
    const sender = globalThis.RTCRtpSender;
    const receiver = globalThis.RTCRtpReceiver;
    const transceiver = globalThis.RTCRtpTransceiver;
    return typeof globalThis.RTCRtpScriptTransform === 'function'
      && typeof globalThis.Worker === 'function'
      && typeof globalThis.TransformStream === 'function'
      && Boolean(sender && 'transform' in sender.prototype)
      && Boolean(receiver && 'transform' in receiver.prototype)
      && typeof transceiver?.prototype.setCodecPreferences === 'function'
      && sender.getCapabilities?.('audio')?.codecs.some(codec => codec.mimeType.toLowerCase() === 'audio/opus') === true
      && sender.getCapabilities?.('video')?.codecs.some(codec => codec.mimeType.toLowerCase() === 'video/vp8') === true;
  });
  requireCondition(supported, 'public_media_runtime_unsupported');
}

async function createFreshPublicSession(page: Page): Promise<PairMutationEvidence> {
  const share = sharePanel(page);
  const createButton = share.getByRole('button', { name: /Session erstellen/i });
  if (!await waitVisible(createButton, 30_000)) {
    const [pageCount, routeHostCount, shareHostCount, nestedCount, panelCount, activeCount] = await Promise.all([
      page.getByTestId('public-pair-page').count(),
      page.locator('app-public-pair-page').count(),
      page.locator('app-ai-snake-share-panel').count(),
      share.count(),
      share.locator('.share-panel').count(),
      share.locator('.share-badge.active').count(),
    ]);
    const pageErrors = PAGE_ERROR_CODES.get(page) ?? [];
    const pathCode = new URL(page.url()).pathname.replace(/[^A-Za-z0-9/_-]/g, '').replaceAll('/', '_') || '_';
    throw new Error(
      `public_share_create_unavailable_path_${pathCode}_page_${pageCount}_route_${routeHostCount}`
      + `_share_${shareHostCount}_nested_${nestedCount}_panel_${panelCount}_active_${activeCount}`
      + `_page_errors_${pageErrors.join('-') || 'none'}`,
    );
  }
  await createButton.click();
  await share.getByLabel('Titel').fill(`public-media-canary-${Date.now()}`);
  await share.getByLabel('Ablauf').selectOption('3600');
  const response = page.waitForResponse(isCreateMutation);
  await share.getByRole('button', { name: 'Erstellen', exact: true }).click();
  return validateMutation(await response, 'create');
}

async function joinFreshPublicSession(
  page: Page,
  inviteCode: string,
  expectedSessionId: string,
): Promise<PairMutationEvidence> {
  const share = sharePanel(page);
  await share.getByRole('button', { name: 'Code eingeben', exact: true }).click();
  await share.getByLabel('Invite-Code').fill(inviteCode);
  const response = page.waitForResponse(isJoinMutation);
  await share.getByRole('button', { name: 'Beitreten', exact: true }).click();
  const evidence = await validateMutation(await response, 'join');
  requireCondition(evidence.sessionId === expectedSessionId, 'public_join_session_mismatch');
  return evidence;
}

function isCreateMutation(response: Response): boolean {
  return response.request().method() === 'POST'
    && new URL(response.url()).pathname === '/rendezvous/sessions';
}

function isJoinMutation(response: Response): boolean {
  return response.request().method() === 'POST'
    && new URL(response.url()).pathname === '/rendezvous/sessions/join';
}

async function validateMutation(
  response: Response,
  kind: 'create' | 'join',
): Promise<PairMutationEvidence> {
  requireCondition(response.ok(), `public_${kind}_request_failed`);
  let requestBody: Record<string, unknown> | null = null;
  try {
    const raw = response.request().postDataJSON();
    if (raw && typeof raw === 'object' && !Array.isArray(raw)) requestBody = raw as Record<string, unknown>;
  } catch { /* malformed request evidence fails closed below */ }
  requireCondition(requestBody?.['identity_binding_version'] === 2, `public_${kind}_v2_request_required`);
  requireCondition(requestBody?.['public_media_e2ee_version'] === 2, `public_${kind}_media_v2_request_required`);
  requireCondition(exactMediaCapabilities(requestBody?.['public_media_capabilities']), `public_${kind}_media_capabilities_invalid`);

  const payload = await response.json().catch(() => null) as Record<string, unknown> | null;
  const session = payload?.['session'] as Record<string, unknown> | undefined;
  const sessionId = String(session?.['id'] || '');
  const inviteCode = String(session?.['invite_code'] || '');
  const localPeerId = String(payload?.['local_peer_id'] || '');
  const createdAtMs = Number(session?.['created_at']) * 1_000;
  requireCondition(payload?.['ok'] === true, `public_${kind}_response_invalid`);
  requireCondition(Boolean(sessionId && inviteCode), `public_${kind}_session_invalid`);
  requireCondition(DEVICE_PEER_ID.test(localPeerId), `public_${kind}_peer_invalid`);
  requireCondition(session?.['local_peer_id'] === localPeerId, `public_${kind}_peer_binding_invalid`);
  requireCondition(session?.['identity_binding_version'] === 2, `public_${kind}_identity_binding_invalid`);
  requireCondition(session?.['security_contract_version'] === 1, `public_${kind}_security_version_invalid`);
  requireCondition(session?.['security_mode'] === 'strict_e2ee', `public_${kind}_security_mode_invalid`);
  requireCondition(session?.['mode'] === 'p2p' && session?.['transport'] === 'webrtc', `public_${kind}_transport_invalid`);
  requireCondition(session?.['revoked_at'] === null, `public_${kind}_session_revoked`);
  requireCondition(Number.isFinite(createdAtMs) && Math.abs(Date.now() - createdAtMs) < 120_000, `public_${kind}_session_not_fresh`);
  return Object.freeze({ sessionId, inviteCode, localPeerId });
}

function exactMediaCapabilities(value: unknown): boolean {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>;
  const keys = Object.keys(row).sort();
  return keys.join(',') === 'frame_format,grants,transform,version'
    && row['version'] === 2
    && row['transform'] === 'RTCRtpScriptTransform'
    && row['frame_format'] === 'ananta.public-pair.media-frame.v2'
    && Array.isArray(row['grants'])
    && row['grants'].join(',') === 'microphone-opus,camera-vp8,screen-vp8';
}

async function expectPairSecurityReady(page: Page, peerRole: 'owner' | 'guest'): Promise<void> {
  await expect(page, `${peerRole}_pair_route_missing`).toHaveURL(/\/pair-dev$/);
  await expect(page.getByTestId('public-pair-page'), `${peerRole}_pair_page_missing`).toBeVisible();
  await expect(sharePanel(page), `${peerRole}_share_panel_missing`).toBeVisible();
  await expect(
    sharePanel(page).locator('.share-badge.active'),
    `${peerRole}_share_session_inactive`,
  ).toBeVisible();
  await expect(
    sharePanel(page).getByTestId('share-security-status'),
    `${peerRole}_share_security_missing`,
  ).toHaveClass(/\bready\b/);
  await expect(
    page.getByTestId('public-pair-webrtc-status'),
    `${peerRole}_webrtc_not_connected`,
  ).toHaveText(/WebRTC:\s*connected/i);
  await expect(
    page.getByTestId('public-pair-datachannel-status'),
    `${peerRole}_datachannel_not_open`,
  ).toHaveText(/DataChannel:\s*open/i);
}

async function grantMediaPublicationBilateral(owner: Page, guest: Page): Promise<void> {
  const peers = [
    ['owner', owner],
    ['guest', guest],
  ] as const;
  const readiness = await Promise.all(peers.map(([, page]) => (
    waitVisible(page.getByTestId('public-media-e2ee-ready'), 60_000)
  )));
  if (readiness.some(ready => !ready)) {
    const diagnostics = await Promise.all(peers.map(async ([role, page]) => (
      `${role}_${await mediaActivationDiagnostic(page)}`
    )));
    throw new Error(`public_media_activation_failed_${diagnostics.join('_')}`);
  }
  const buttons = peers.map(([, page]) => publicationConsent(page)
    .getByRole('button', { name: 'Einwilligen und Medien aktivieren', exact: true }));
  await Promise.all(buttons.map(button => expect(button).toBeEnabled()));
  await Promise.all(buttons.map(button => button.click()));
  await Promise.all(peers.map(([, page]) => expect(publicationConsent(page)
    .getByTestId('public-pair-publication-consent-status'))
    .toContainText('Eigene Medien sind freigegeben bis')));
}

async function mediaActivationDiagnostic(page: Page): Promise<string> {
  const capabilityState = await publicationConsent(page)
    .getByTestId('public-pair-publication-consent-status')
    .textContent().catch(() => 'missing');
  const capabilityReason = await publicationConsent(page).locator('code')
    .textContent().catch(() => 'missing');
  const operationReason = await mediaPanel(page).getByTestId('ordinary-media-operation-status')
    .locator('code').textContent().catch(() => 'media_panel_unavailable');
  const pendingCount = await page.getByTestId('public-media-e2ee-pending').count();
  const webrtc = await page.getByTestId('public-pair-webrtc-status').textContent().catch(() => 'missing');
  const dataChannel = await page.getByTestId('public-pair-datachannel-status')
    .textContent().catch(() => 'missing');
  const pageErrors = (PAGE_ERROR_CODES.get(page) ?? []).slice(-4);
  return `cap_${safeStatusCode(capabilityState)}_cap_reason_${safeReasonCode(capabilityReason)}`
    + `_operation_${safeReasonCode(operationReason)}_pending_${pendingCount}`
    + `_webrtc_${safeStatusCode(webrtc)}_datachannel_${safeChannelState(dataChannel)}`
    + `_page_errors_${pageErrors.join('-') || 'none'}`;
}

async function startBilateralCapture(owner: Page, guest: Page): Promise<void> {
  const pages = [owner, guest] as const;
  await Promise.all(pages.map(page => mediaPanel(page).getByRole('button', { name: 'Mikrofon freigeben' }).click()));
  await Promise.all(pages.map(page => expect(mediaPanel(page).locator('article[data-source="microphone"]'))
    .toHaveAttribute('data-status', 'active')));
  await Promise.all(pages.map(page => mediaPanel(page).getByRole('button', { name: 'Kamera freigeben' }).click()));
  await Promise.all(pages.map(page => expect(mediaPanel(page).locator('article[data-source="camera"]'))
    .toHaveAttribute('data-status', 'active')));
  await expectBilateralRenderedMedia(owner, guest, false);
  await Promise.all(pages.map(page => mediaPanel(page).getByRole('button', { name: 'Bildschirm freigeben' }).click()));
  for (const [index, page] of pages.entries()) {
    const screen = mediaPanel(page).locator('article[data-source="screen"]');
    if (!await waitVisible(screen, 60_000)) {
      const reason = await mediaPanel(page).getByTestId('ordinary-media-operation-status')
        .locator('code').textContent().catch(() => 'media_panel_unavailable');
      throw new Error(`public_screen_capture_missing_peer_${index}_${safeReasonCode(reason)}`);
    }
    await expect(screen).toHaveAttribute('data-status', 'active');
  }
}

async function expectBilateralReception(owner: Page, guest: Page): Promise<void> {
  await expectBilateralRenderedMedia(owner, guest, true);
}

async function expectBilateralRenderedMedia(owner: Page, guest: Page, requireTwoVideos: boolean): Promise<void> {
  await Promise.all(([
    ['owner', owner],
    ['guest', guest],
  ] as const).map(async ([role, page]) => {
    const remoteVideo = mediaPanel(page).locator('article[data-source="remote_video"][data-status="active"]');
    if (requireTwoVideos) await expect(remoteVideo).toHaveCount(2);
    await expect(page.getByText('Ordinary Audio empfangen.', { exact: true })).toBeVisible();
    const requiredVideoCount = requireTwoVideos ? 2 : 1;
    try {
      await expect.poll(() => page.locator('app-webrtc-remote-video video').evaluateAll((elements, required) => (
        elements.length >= required && elements.filter(element => {
          const video = element as HTMLVideoElement;
          return video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA && video.currentTime > 0;
        }).length >= required
      ), requiredVideoCount)).toBe(true);
    } catch {
      throw new Error(`public_remote_video_unavailable_${role}_${await remoteVideoDiagnostic(page)}`);
    }
    try {
      await expect.poll(() => page.locator('app-semantic-remote-audio audio').evaluate(element => {
        const audio = element as HTMLAudioElement;
        return audio.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA && audio.currentTime > 0;
      })).toBe(true);
    } catch {
      throw new Error(`public_remote_audio_unavailable_${role}_${await remoteAudioDiagnostic(page)}`);
    }
  }));
}

async function remoteVideoDiagnostic(page: Page): Promise<string> {
  const videoStates = await page.locator('app-webrtc-remote-video video').evaluateAll(elements => elements.map(element => {
    const video = element as HTMLVideoElement;
    const tracks = video.srcObject instanceof MediaStream ? video.srcObject.getVideoTracks() : [];
    return [
      video.readyState,
      video.paused ? 1 : 0,
      video.currentTime > 0 ? 1 : 0,
      tracks.length,
      tracks.some(track => !track.muted) ? 1 : 0,
      tracks.every(track => track.readyState === 'live') ? 1 : 0,
    ].join('');
  })).catch(() => [] as string[]);
  const remoteStatuses = await mediaPanel(page).locator('article[data-source="remote_video"]')
    .evaluateAll(elements => elements.map(element => element.getAttribute('data-status') || 'none'))
    .catch(() => [] as string[]);
  const reason = await mediaPanel(page).getByTestId('ordinary-media-operation-status')
    .locator('code').textContent().catch(() => 'media_panel_unavailable');
  const capabilityState = await publicationConsent(page)
    .getByTestId('public-pair-publication-consent-status')
    .textContent().catch(() => 'missing');
  const capabilityReason = await publicationConsent(page).locator('code')
    .textContent().catch(() => 'missing');
  const readyCount = await page.getByTestId('public-media-e2ee-ready').count();
  const webrtc = await page.getByTestId('public-pair-webrtc-status').textContent().catch(() => 'missing');
  return `videos_${videoStates.join('-') || 'none'}_rows_${remoteStatuses.join('-') || 'none'}`
    + `_reason_${safeReasonCode(reason)}_cap_${safeStatusCode(capabilityState)}`
    + `_cap_reason_${safeReasonCode(capabilityReason)}_ready_${readyCount}`
    + `_webrtc_${safeStatusCode(webrtc)}`;
}

async function remoteAudioDiagnostic(page: Page): Promise<string> {
  const state = await page.locator('app-semantic-remote-audio audio').evaluate(element => {
    const audio = element as HTMLAudioElement;
    const tracks = audio.srcObject instanceof MediaStream ? audio.srcObject.getAudioTracks() : [];
    return [audio.readyState, audio.paused ? 1 : 0, audio.currentTime > 0 ? 1 : 0, tracks.length,
      tracks.some(track => !track.muted) ? 1 : 0, tracks.every(track => track.readyState === 'live') ? 1 : 0].join('');
  }).catch(() => 'missing');
  return `audio_${state}`;
}

async function revokeAndRegrantOwnerPublication(owner: Page, guest: Page): Promise<void> {
  await publicationConsent(owner)
    .getByRole('button', { name: 'Eigene Freigabe deaktivieren', exact: true }).click();
  await expect(publicationConsent(owner).getByTestId('public-pair-publication-consent-status'))
    .toContainText('Eigene Medien sind deaktiviert');
  await expect(mediaPanel(owner).locator('article[data-source="microphone"]'))
    .toHaveAttribute('data-status', 'idle');
  await Promise.all([owner, guest].map(page => expect(page.getByTestId('public-media-e2ee-ready')).toBeVisible()));
  await Promise.all([owner, guest].map(page => expect(page.getByTestId('public-pair-webrtc-status'))
    .toHaveText(/WebRTC:\s*connected/i)));
  await Promise.all([owner, guest].map(page => expect(page.getByTestId('public-pair-datachannel-status'))
    .toHaveText(/DataChannel:\s*open/i)));

  const grant = publicationConsent(owner)
    .getByRole('button', { name: 'Einwilligen und Medien aktivieren', exact: true });
  await expect(grant).toBeEnabled();
  await grant.click();
  await expect(publicationConsent(owner).getByTestId('public-pair-publication-consent-status'))
    .toContainText('Eigene Medien sind freigegeben bis');
  await mediaPanel(owner).getByRole('button', { name: 'Mikrofon freigeben' }).click();
  await expect(mediaPanel(owner).locator('article[data-source="microphone"]'))
    .toHaveAttribute('data-status', 'active');
  await mediaPanel(owner).getByRole('button', { name: 'Kamera freigeben' }).click();
  await expect(mediaPanel(owner).locator('article[data-source="camera"]'))
    .toHaveAttribute('data-status', 'active');
}

async function endOwnerSession(page: Page, sessionId: string): Promise<void> {
  const response = page.waitForResponse(candidate => candidate.request().method() === 'DELETE'
    && new URL(candidate.url()).pathname === `/rendezvous/sessions/${encodeURIComponent(sessionId)}`);
  page.once('dialog', dialog => void dialog.accept());
  await sharePanel(page).getByRole('button', { name: 'Session beenden', exact: true }).click();
  requireCondition((await response).ok(), 'public_session_cleanup_failed');
  await expect(sharePanel(page).getByRole('button', { name: /Session erstellen/i })).toBeVisible();
}

async function bestEffortEndOwnerSession(page: Page): Promise<void> {
  const button = sharePanel(page).getByRole('button', { name: 'Session beenden', exact: true });
  if (!await button.isVisible().catch(() => false)) return;
  page.once('dialog', dialog => void dialog.accept());
  await button.click({ timeout: 5_000 }).catch(() => undefined);
}

async function bestEffortLeaveGuestSession(page: Page): Promise<void> {
  const button = sharePanel(page).getByRole('button', { name: 'Verlassen', exact: true });
  if (await button.isVisible().catch(() => false)) {
    await button.click({ timeout: 5_000 }).catch(() => undefined);
  }
}

function sharePanel(page: Page) {
  return page.locator('app-public-pair-page app-ai-snake-share-panel');
}

function publicationConsent(page: Page) {
  return page.getByTestId('public-pair-publication-consent');
}

function mediaPanel(page: Page) {
  return page.locator('app-public-pair-page app-webrtc-media-panel');
}

function requireCondition(condition: unknown, reasonCode: string): asserts condition {
  if (!condition) throw new Error(reasonCode);
}

function safeReasonCode(value: string | null): string {
  const normalized = String(value || '').trim();
  return /^[a-z][a-z0-9_]{2,119}$/.test(normalized) ? normalized : 'unknown';
}

function safeStatusCode(value: string | null): string {
  const normalized = String(value || '').toLowerCase();
  for (const state of ['authoritatively_active', 'degraded', 'failed', 'revoked', 'expired', 'locally_desired']) {
    if (normalized.includes(state)) return state;
  }
  if (normalized.includes('connected')) return 'connected';
  if (normalized.includes('failed')) return 'failed';
  if (normalized.includes('disconnected')) return 'disconnected';
  return 'unknown';
}

function safeChannelState(value: string | null): string {
  const normalized = String(value || '').toLowerCase();
  for (const state of ['open', 'connecting', 'closing', 'closed', 'absent']) {
    if (normalized.includes(state)) return state;
  }
  return 'unknown';
}

async function waitVisible(locator: ReturnType<Page['locator']>, timeout: number): Promise<boolean> {
  return locator.waitFor({ state: 'visible', timeout }).then(() => true).catch(() => false);
}

function classifyPageError(error: Error): string {
  const message = String(error.message || '');
  return message.match(/\bNG\d{3,}\b/)?.[0]
    ?? message.match(/\b(?:public|pair|webrtc|media|oidc)_[a-z0-9_]+\b/)?.[0]
    ?? error.name.replace(/[^A-Za-z0-9_-]/g, '').slice(0, 40)
    ?? 'unknown';
}
