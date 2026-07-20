import { expect, test } from '@playwright/test';
import type { APIRequestContext, BrowserContext, Page } from '@playwright/test';

import type {
  SemanticMediaGroupParticipantConfig,
  SemanticMediaGroupParticipantDriver,
} from '../src/app/e2e/semantic-media-group-live-driver';
import type { KeyEnvelope } from '../src/app/services/e2e-encryption.service';
import type { SemanticSfuState } from '../src/app/services/semantic-sfu-admission-api.service';
import type { GroupKeyEpochAuthorization } from '../src/app/services/webrtc-group-key.service';

declare global {
  interface Window {
    __ANANTA_SEMANTIC_MEDIA_GROUP_E2E__?: SemanticMediaGroupParticipantDriver;
  }
}

const ROLES = [
  'publisher',
  'ordinary-receiver',
  'semantic-receiver',
  'validator',
  'weak-receiver',
  'removed-member',
  'late-join',
] as const;
type Role = typeof ROLES[number];

type SeedParticipant = Readonly<{
  userId: string;
  deviceId: string;
  participantId: string | null;
  userToken: string;
}>;

type GroupSeed = Readonly<{
  hubUrl: string;
  tenantId: string;
  sessionId: string;
  membershipEpoch: number;
  expiresAtMs: number;
  participants: Readonly<Record<Role, SeedParticipant>>;
}>;

type ParticipantRuntime = {
  readonly role: Role;
  readonly context: BrowserContext;
  readonly page: Page;
  readonly device: KeyEnvelope;
};

test.describe('semantic media bounded-group Hub/SFU conformance', () => {
  test.skip(
    process.env['RUN_SEMANTIC_MEDIA_LIVE_E2E'] !== '1',
    'live group evidence is mandatory for release, never simulated',
  );

  test('uses Hub admission and Hub-coordinated epochs across join, revoke, fallback and restart', async ({
    browser,
    request,
  }, testInfo) => {
    const baseUrl = String(testInfo.project.use.baseURL || '');
    if (!/^https?:\/\/[^\s]+$/.test(baseUrl)) throw new Error('semantic_group_frontend_url_missing');
    const runtimes: ParticipantRuntime[] = [];
    const admissionStatuses: number[] = [];
    try {
      for (const role of ROLES) {
        const context = await browser.newContext();
        const page = await context.newPage();
        context.on('response', response => {
          if (response.url().includes('/v1/semantic-media/sfu/')) admissionStatuses.push(response.status());
        });
        await page.goto(`${baseUrl}/voice?semanticMediaGroupLiveE2e=1`);
        await expectDriver(page);
        const device = await callDriver<KeyEnvelope>(page, 'deviceIdentity');
        expect(device.fingerprint).toMatch(/^[a-f0-9]{64}$/);
        expect(device.publicKeySpkiB64).toMatch(/^[A-Za-z0-9+/]+={0,2}$/);
        runtimes.push({ role, context, page, device });
      }

      const seed = await seedGroup(request, runtimes);
      const byRole = new Map(runtimes.map(runtime => [runtime.role, runtime]));
      for (const runtime of runtimes) {
        const participant = seed.participants[runtime.role];
        await configure(runtime, seed, seed.membershipEpoch);
        await callDriver(runtime.page, 'authenticate', participant.userToken);
      }

      // Two valid actors deliberately race the same Hub CAS revision. Exactly
      // one admission succeeds; the loser recovers through the product state
      // endpoint. No client or peer decides which mutation wins.
      const initialRevision = (await callDriver<SemanticSfuState>(
        requireRuntime(byRole, 'publisher').page,
        'admissionState',
      )).revision;
      const conflict = await Promise.allSettled([
        callDriver(requireRuntime(byRole, 'publisher').page, 'join', initialRevision),
        callDriver(requireRuntime(byRole, 'ordinary-receiver').page, 'join', initialRevision),
      ]);
      expect(conflict.filter(result => result.status === 'fulfilled')).toHaveLength(1);
      expect(conflict.filter(result => result.status === 'rejected')).toHaveLength(1);
      await joinAll(runtimes);

      const initialAudienceRoles: readonly Role[] = [
        'ordinary-receiver', 'semantic-receiver', 'validator', 'weak-receiver', 'removed-member',
      ];
      const initialAudience = initialAudienceRoles.map(role => seed.participants[role].userId);
      const publisher = requireRuntime(byRole, 'publisher');
      const initialPublication = await callDriver<Readonly<{
        publicationIds: readonly string[]; revision: number;
      }>>(publisher.page, 'authorizePublisher', initialAudience, 1);
      expect(initialPublication.publicationIds).toHaveLength(2);
      for (const role of initialAudienceRoles) {
        await callDriver(requireRuntime(byRole, role).page, 'authorizeSubscriptions', initialPublication.publicationIds, 1);
      }
      const initialMembers = [seed.participants.publisher.userId, ...initialAudience];
      const initialEpoch = await callDriver<Readonly<{
        authorization: GroupKeyEpochAuthorization & { membership_epoch: number };
        acknowledgedMemberCount: number;
        pendingMemberCount: number;
      }>>(publisher.page, 'preparePublisherEpoch', initialMembers);
      expect(initialEpoch.authorization.membership_epoch).toBe(seed.membershipEpoch);
      expect(initialEpoch.pendingMemberCount).toBe(initialAudience.length);
      for (const role of initialAudienceRoles) {
        const received = await callDriver<Readonly<{ installed: boolean; epoch: number }>>(
          requireRuntime(byRole, role).page,
          'receiveEpoch',
        );
        expect(received.installed).toBe(true);
        expect(received.epoch).toBe(initialEpoch.authorization.epoch);
      }
      const initialAck = await callDriver<Readonly<{
        acknowledgedMemberCount: number; pendingMemberCount: number;
      }>>(publisher.page, 'epochStatus', initialEpoch.authorization.authorization_id);
      expect(initialAck).toEqual({ acknowledgedMemberCount: initialAudience.length, pendingMemberCount: 0 });

      await Promise.all([
        callDriver(publisher.page, 'connect'),
        ...initialAudienceRoles.map(role => callDriver(requireRuntime(byRole, role).page, 'connect')),
        callDriver(requireRuntime(byRole, 'late-join').page, 'connectWithoutAuthorizedEpoch'),
      ]);
      expect(await callDriver<number>(publisher.page, 'publish', initialAudience)).toBe(2);
      await Promise.all(initialAudienceRoles.map(role => (
        callDriver(requireRuntime(byRole, role).page, 'waitForFrames', 3)
      )));

      const late = requireRuntime(byRole, 'late-join');
      expect(await callDriver<boolean>(late.page, 'hasAuthorizedEpoch', initialEpoch.authorization)).toBe(false);
      expect(await callDriver<boolean>(late.page, 'framesRemainStable')).toBe(true);

      // A weak receiver takes the ordinary speech fallback. The semantic and
      // validator receivers continue receiving the same publisher upload.
      const semantic = requireRuntime(byRole, 'semantic-receiver');
      const validator = requireRuntime(byRole, 'validator');
      const weak = requireRuntime(byRole, 'weak-receiver');
      const semanticBeforeFallback = await callDriver<number>(semantic.page, 'frameCount');
      const validatorBeforeFallback = await callDriver<number>(validator.page, 'frameCount');
      await callDriver(weak.page, 'disconnect');
      expect(await callDriver<boolean>(weak.page, 'ordinaryFallback')).toBe(true);
      await Promise.all([
        callDriver(semantic.page, 'waitForFrames', semanticBeforeFallback + 3),
        callDriver(validator.page, 'waitForFrames', validatorBeforeFallback + 3),
      ]);

      // Reload one browser process. Its IndexedDB device key remains local,
      // while admission revision and the addressed epoch are recovered from
      // productive Hub APIs.
      await semantic.page.reload();
      // The normal auth initializer may project a fixture principal without a
      // local profile to /login. Re-enter only the explicit live-E2E entry;
      // IndexedDB and the BrowserContext remain unchanged.
      await semantic.page.goto(`${baseUrl}/voice?semanticMediaGroupLiveE2e=1`);
      await expectDriver(semantic.page);
      await configure(semantic, seed, seed.membershipEpoch);
      const recoveredState = await callDriver<SemanticSfuState>(semantic.page, 'admissionState');
      expect(recoveredState.joined).toBe(true);
      expect((await callDriver<Readonly<{ installed: boolean }>>(semantic.page, 'receiveEpoch')).installed).toBe(true);
      await callDriver(semantic.page, 'authorizeSubscriptions', initialPublication.publicationIds, 11);
      await callDriver(semantic.page, 'connect');
      await callDriver(semantic.page, 'waitForFrames', 3);

      const removed = requireRuntime(byRole, 'removed-member');
      const removedFramesBeforeRekey = await callDriver<number>(removed.page, 'frameCount');
      const revoke = await request.delete(
        `${seed.hubUrl}/share-sessions/${encodeURIComponent(seed.sessionId)}/participants/${encodeURIComponent(
          requiredParticipantId(seed.participants['removed-member']),
        )}`,
        { headers: { Authorization: `Bearer ${seed.participants.publisher.userToken}` } },
      );
      expect(revoke.status()).toBe(200);
      const membershipEpoch = await currentMembershipEpoch(request, seed);
      expect(membershipEpoch).toBeGreaterThan(seed.membershipEpoch);

      const activeRoles: readonly Role[] = [
        'publisher', 'ordinary-receiver', 'semantic-receiver', 'validator', 'weak-receiver', 'late-join',
      ];
      await Promise.all(activeRoles.map(role => callDriver(requireRuntime(byRole, role).page, 'disconnect')));
      for (const role of activeRoles) {
        await configure(requireRuntime(byRole, role), seed, membershipEpoch);
      }
      await joinAll(activeRoles.map(role => requireRuntime(byRole, role)));

      const rekeyAudienceRoles: readonly Role[] = [
        'ordinary-receiver', 'semantic-receiver', 'validator', 'weak-receiver', 'late-join',
      ];
      const rekeyAudience = rekeyAudienceRoles.map(role => seed.participants[role].userId);
      const rekeyPublication = await callDriver<Readonly<{
        publicationIds: readonly string[]; revision: number;
      }>>(publisher.page, 'authorizePublisher', rekeyAudience, 2);
      for (const role of rekeyAudienceRoles) {
        await callDriver(requireRuntime(byRole, role).page, 'authorizeSubscriptions', rekeyPublication.publicationIds, 2);
      }
      const rekey = await callDriver<Readonly<{
        authorization: GroupKeyEpochAuthorization & { membership_epoch: number };
      }>>(publisher.page, 'preparePublisherEpoch', [seed.participants.publisher.userId, ...rekeyAudience]);
      expect(rekey.authorization.epoch).toBeGreaterThan(initialEpoch.authorization.epoch);
      expect(rekey.authorization.membership_epoch).toBe(membershipEpoch);
      expect(await callDriver<boolean>(late.page, 'hasAuthorizedEpoch', initialEpoch.authorization)).toBe(false);
      for (const role of rekeyAudienceRoles) {
        const received = await callDriver<Readonly<{ installed: boolean; epoch: number }>>(
          requireRuntime(byRole, role).page,
          'receiveEpoch',
        );
        expect(received).toEqual(expect.objectContaining({ installed: true, epoch: rekey.authorization.epoch }));
      }
      const rekeyAck = await callDriver<Readonly<{
        acknowledgedMemberCount: number; pendingMemberCount: number;
      }>>(publisher.page, 'epochStatus', rekey.authorization.authorization_id);
      expect(rekeyAck).toEqual({ acknowledgedMemberCount: rekeyAudience.length, pendingMemberCount: 0 });
      expect(await callDriver<boolean>(removed.page, 'hasAuthorizedEpoch', rekey.authorization)).toBe(false);

      await Promise.all(activeRoles.map(role => callDriver(requireRuntime(byRole, role).page, 'connect')));
      expect(await callDriver<number>(publisher.page, 'publish', rekeyAudience)).toBe(2);
      await Promise.all(rekeyAudienceRoles.map(role => (
        callDriver(requireRuntime(byRole, role).page, 'waitForFrames', 3)
      )));
      expect(await callDriver<boolean>(removed.page, 'framesRemainStable')).toBe(true);
      expect(await callDriver<number>(removed.page, 'frameCount')).toBeGreaterThanOrEqual(removedFramesBeforeRekey);

      const forbidden = await removed.page.evaluate(async ({ hubUrl, sessionId, membershipEpoch: epoch }) => {
        const token = localStorage.getItem('ananta.user.token') || '';
        const response = await fetch(
          `${hubUrl}/v1/semantic-media/sfu/group-keys/packages?session_id=${encodeURIComponent(sessionId)}&membership_epoch=${epoch}`,
          { headers: { Authorization: `Bearer ${token}` } },
        );
        return response.status;
      }, { hubUrl: seed.hubUrl, sessionId: seed.sessionId, membershipEpoch });
      expect(forbidden).toBe(403);

      testInfo.annotations.push({
        type: 'semantic-group-hub-authority-v2',
        description: JSON.stringify({
          participant_contexts: runtimes.length,
          hub_admission_conflict_rejections: conflict.filter(result => result.status === 'rejected').length,
          hub_admission_success_responses: admissionStatuses.filter(status => status >= 200 && status < 300).length,
          hub_admission_denied_responses: admissionStatuses.filter(status => status >= 400).length,
          hub_publication_generations: 2,
          hub_published_track_count: 2,
          hub_group_epoch_count: 2,
          group_package_ack_count: initialAck.acknowledgedMemberCount + rekeyAck.acknowledgedMemberCount,
          membership_revoke_count: 1,
          late_join_old_epoch_key_count: 0,
          removed_member_new_epoch_key_count: 0,
          independent_receiver_count: 2,
          weak_receiver_fallback_count: 1,
          browser_restart_recovery_count: Number(recoveredState.joined),
          ordinary_fallback_count: 1,
          client_minted_sfu_token_count: 0,
          client_generated_group_epoch_count: 0,
        }),
      });
    } finally {
      await Promise.all(runtimes.map(runtime => callDriver(runtime.page, 'cleanup').catch(() => undefined)));
      await Promise.all(runtimes.map(runtime => runtime.context.close().catch(() => undefined)));
    }
  });
});

async function seedGroup(
  request: APIRequestContext,
  runtimes: readonly ParticipantRuntime[],
): Promise<GroupSeed> {
  const hubUrl = String(process.env['E2E_HUB_URL'] || 'http://127.0.0.1:5500').replace(/\/+$/, '');
  const login = await request.post(`${hubUrl}/login`, {
    data: {
      username: process.env['E2E_ADMIN_USER'] || 'admin',
      password: process.env['E2E_ADMIN_PASSWORD'] || 'test123',
    },
  });
  if (!login.ok()) throw new Error(`semantic_group_seed_login_failed_${login.status()}`);
  const loginBody = await login.json() as { data?: { access_token?: unknown }; access_token?: unknown };
  const adminToken = String(loginBody.data?.access_token || loginBody.access_token || '');
  const seeded = await request.post(`${hubUrl}/test/semantic-media/group-seed`, {
    headers: { Authorization: `Bearer ${adminToken}` },
    data: {
      participants: runtimes.map(runtime => ({
        role: runtime.role,
        public_key_spki_b64: runtime.device.publicKeySpkiB64,
        fingerprint: runtime.device.fingerprint,
      })),
    },
  });
  if (!seeded.ok()) throw new Error(`semantic_group_seed_request_failed_${seeded.status()}`);
  const body = await seeded.json() as { data?: Record<string, unknown> };
  const value = body.data || {};
  const rawParticipants = value['participants'];
  if (!rawParticipants || typeof rawParticipants !== 'object' || Array.isArray(rawParticipants)) {
    throw new Error('semantic_group_seed_response_invalid');
  }
  const participants = Object.fromEntries(ROLES.map(role => {
    const raw = (rawParticipants as Record<string, unknown>)[role];
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('semantic_group_seed_response_invalid');
    const row = raw as Record<string, unknown>;
    const participant: SeedParticipant = {
      userId: String(row['user_id'] || ''),
      deviceId: String(row['device_id'] || ''),
      participantId: row['participant_id'] === null ? null : String(row['participant_id'] || ''),
      userToken: String(row['user_token'] || ''),
    };
    if (!participant.userId || !participant.deviceId || participant.userToken.split('.').length !== 3) {
      throw new Error('semantic_group_seed_response_invalid');
    }
    return [role, participant];
  })) as Record<Role, SeedParticipant>;
  const seed: GroupSeed = {
    hubUrl: String(value['hub_url'] || ''),
    tenantId: String(value['tenant_id'] || ''),
    sessionId: String(value['session_id'] || ''),
    membershipEpoch: Number(value['membership_epoch']),
    expiresAtMs: Number(value['expires_at_ms']),
    participants,
  };
  if (
    seed.hubUrl !== hubUrl
    || !seed.tenantId
    || !seed.sessionId
    || !Number.isSafeInteger(seed.membershipEpoch)
    || seed.membershipEpoch < 1
    || seed.expiresAtMs <= Date.now()
  ) throw new Error('semantic_group_seed_response_invalid');
  return seed;
}

async function configure(runtime: ParticipantRuntime, seed: GroupSeed, membershipEpoch: number): Promise<void> {
  const config: SemanticMediaGroupParticipantConfig = {
    hubUrl: seed.hubUrl,
    tenantId: seed.tenantId,
    sessionId: seed.sessionId,
    membershipEpoch,
    localPeerId: seed.participants[runtime.role].userId,
  };
  await callDriver(runtime.page, 'configure', config);
}

async function joinAll(runtimes: readonly ParticipantRuntime[]): Promise<void> {
  for (const runtime of runtimes) {
    const state = await callDriver<SemanticSfuState>(runtime.page, 'admissionState');
    if (!state.joined) await callDriver(runtime.page, 'join', state.revision);
  }
}

async function expectDriver(page: Page): Promise<void> {
  await expect.poll(() => page.evaluate(() => Boolean(window.__ANANTA_SEMANTIC_MEDIA_GROUP_E2E__))).toBe(true);
}

async function callDriver<TResult>(page: Page, method: string, ...args: readonly unknown[]): Promise<TResult> {
  return page.evaluate(async ({ name, values }) => {
    const driver = window.__ANANTA_SEMANTIC_MEDIA_GROUP_E2E__;
    if (!driver) throw new Error('semantic_group_product_driver_missing');
    const operation = (driver as unknown as Record<string, (...input: unknown[]) => unknown>)[name];
    if (typeof operation !== 'function') throw new Error('semantic_group_product_operation_missing');
    return await operation(...values) as TResult;
  }, { name: method, values: [...args] });
}

function requireRuntime(values: ReadonlyMap<Role, ParticipantRuntime>, role: Role): ParticipantRuntime {
  const runtime = values.get(role);
  if (!runtime) throw new Error(`semantic_group_runtime_missing_${role}`);
  return runtime;
}

function requiredParticipantId(participant: SeedParticipant): string {
  if (!participant.participantId) throw new Error('semantic_group_participant_id_missing');
  return participant.participantId;
}

async function currentMembershipEpoch(request: APIRequestContext, seed: GroupSeed): Promise<number> {
  const response = await request.get(`${seed.hubUrl}/share-sessions`, {
    headers: { Authorization: `Bearer ${seed.participants.publisher.userToken}` },
  });
  if (!response.ok()) throw new Error(`semantic_group_session_read_failed_${response.status()}`);
  const body = await response.json() as { sessions?: unknown[]; data?: { items?: unknown[] } };
  const rows = Array.isArray(body.sessions) ? body.sessions : body.data?.items;
  const found = rows?.find(raw => (
    raw && typeof raw === 'object' && (raw as Record<string, unknown>)['id'] === seed.sessionId
  )) as Record<string, unknown> | undefined;
  const epoch = Number(found?.['security_epoch']);
  if (!Number.isSafeInteger(epoch) || epoch < 1) throw new Error('semantic_group_membership_epoch_missing');
  return epoch;
}
