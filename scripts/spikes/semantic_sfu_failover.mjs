#!/usr/bin/env node
/**
 * Real browser/SFU failover probe.
 *
 * Productive Hub APIs issue every admission, signed epoch and compute lease.
 * This process only drives real browsers and asks the Python runner to change
 * isolated Hub/SFU process lifecycles through a content-free file protocol.
 */

import {
  createHash,
  generateKeyPairSync,
  randomBytes,
  sign,
} from 'node:crypto';
import {
  createReadStream,
  existsSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from 'node:fs';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(resolve(root, 'frontend-angular/package.json'));
const { chromium, firefox } = require('playwright');
const sdkPath = resolve(root, 'frontend-angular/node_modules/livekit-client/dist/livekit-client.umd.js');
const workerPath = resolve(root, 'frontend-angular/node_modules/livekit-client/dist/livekit-client.e2ee.worker.js');
const outputPath = resolve(process.env.ANANTA_SEMANTIC_MEDIA_SFU_FAILOVER_OUTPUT ?? 'semantic-sfu-failover.raw.json');
const controlDirectory = resolve(process.env.ANANTA_SEMANTIC_MEDIA_SFU_CONTROL_DIR ?? '');
const requestPath = resolve(controlDirectory, 'request.json');
const responsePath = resolve(controlDirectory, 'response.json');
const sfuUrl = process.env.ANANTA_SEMANTIC_MEDIA_SFU_PUBLIC_WS_URL ?? '';
const hubUrl = process.env.ANANTA_SEMANTIC_MEDIA_SFU_HUB_URL ?? '';
const fixturePath = resolve(process.env.ANANTA_SEMANTIC_MEDIA_SFU_HUB_FIXTURE ?? '');
const engines = { chromium, firefox };

if (!existsSync(sdkPath) || !existsSync(workerPath)) throw new Error('pinned livekit-client assets missing');
if (!sfuUrl.startsWith('ws://127.0.0.1:')) throw new Error('failover probe requires an isolated loopback SFU');
if (!hubUrl.startsWith('http://127.0.0.1:')) throw new Error('failover probe requires an isolated loopback Hub');
if (!controlDirectory) throw new Error('failover control directory missing');
if (!fixturePath || !existsSync(fixturePath)) throw new Error('failover Hub fixture missing');
const fixture = JSON.parse(readFileSync(fixturePath, 'utf8'));
const semanticControlDataType = 'application/vnd.ananta.semantic-media-control+json';
const semanticControlPurpose = 'semantic_media_control';
if (
  fixture.schema !== 'ananta.semantic-sfu-hub-fixture.v1'
  || !fixture.tokens
  || !fixture.sessions
  || !Array.isArray(fixture.identities)
) throw new Error('failover Hub fixture invalid');

const server = createServer((request, response) => {
  if (request.url === '/livekit.js') return stream(response, sdkPath, 'text/javascript');
  if (request.url === '/e2ee-worker.js') return stream(response, workerPath, 'text/javascript');
  response.writeHead(200, { 'content-type': 'text/html', 'cache-control': 'no-store' });
  response.end('<!doctype html><meta charset="utf-8"><title>Ananta SFU failover probe</title>');
});
await new Promise(resolveListen => server.listen(0, '127.0.0.1', resolveListen));
const address = server.address();
if (!address || typeof address === 'string') throw new Error('failover probe HTTP server unavailable');
const origin = `http://127.0.0.1:${address.port}`;

let controlSequence = 0;
const engineResults = [];

try {
  for (const engineName of ['chromium', 'firefox']) {
    engineResults.push(await runEngine(engineName, engines[engineName]));
  }
} finally {
  await new Promise(resolveClose => server.close(resolveClose));
}

const verdict = engineResults.length === 2 && engineResults.every(row => row.verdict === 'pass') ? 'pass' : 'fail';
const report = {
  schema: 'ananta.semantic-sfu-live-failover.v1',
  pinned: {
    server_version: '1.13.1',
    server_digest: 'sha256:2c6869d2d5ff6c9c0166f47be1c92dad6928bfecfa5e4060a6ece48db8accfa3',
    client_version: '2.20.1',
  },
  topology: {
    publishers: 1,
    required_receivers: 2,
    stale_key_probes: 1,
    browser_engines: ['chromium', 'firefox'],
  },
  authority: {
    kind: 'productive-hub-api',
    admission_api: 'semantic_sfu_admission_bp',
    compute_api: 'semantic_media_contracts_bp',
    state_repository: 'sql_cas',
    signature_algorithm: 'Ed25519',
    browser_mints_admission: false,
    epoch_transition: [1, 2],
    recovery_reason: 'hub_failover',
  },
  persisted_source_data: false,
  engines: engineResults,
  verdict,
};
writeFileSync(outputPath, `${JSON.stringify(report, null, 2)}\n`, { encoding: 'utf8' });
console.log(JSON.stringify({ verdict, engines: engineResults.map(row => row.engine) }));
if (verdict !== 'pass') process.exitCode = 1;

async function runEngine(engineName, engine) {
  const browser = await engine.launch({ headless: true });
  const session = fixture.sessions[engineName];
  const identities = [...fixture.identities];
  if (!session || identities.join(',') !== 'publisher,receiver-1,receiver-2,stale-key-probe') {
    throw new Error('engine Hub fixture missing');
  }
  const pages = [];
  const firstKey = randomBytes(32);
  const secondKey = randomBytes(32);
  const firstAdmission = await authorizeSfuGeneration(engineName, session, identities, 1);
  const computeAuthority = await prepareComputeAuthority(engineName, session, firstAdmission.roomId);
  const initialSchedule = await scheduleCompute(computeAuthority, {
    audience: 'receiver-1', sequenceStart: 0, sequenceEnd: 9, validatorCount: 1,
    idempotencyKey: `initial-schedule-${engineName}`,
  });
  let result;
  let browserClosed = false;
  try {
    for (const identity of identities) pages.push(await createPage(browser, identity));
    await connectAll(pages, {
      admission: firstAdmission,
      keyBytes: firstKey,
    });
    await publishVideo(pages[0].page);
    await waitForDecoded(pages.slice(1), 3);
    await delay(1_000);
    const before = await aggregateSfuMetrics(pages);
    requireInitialFlow(before);

    const kill = await requestContainerAction('sfu_kill');
    await waitForOutage(pages);
    await disconnectSfu(pages);
    const ordinary = await establishOrdinaryAudioFallback(pages.slice(0, 3));
    requireOrdinaryFlow(ordinary);

    const hubKill = await requestContainerAction('hub_kill');
    const hubUnavailable = await verifyHubUnavailable();
    const oldAuthorizationRejectedCount = await rejectStaleAuthorization(
      pages, firstAdmission.authorization, firstAdmission.hubPublicKeyB64,
    );
    if (oldAuthorizationRejectedCount !== identities.length) throw new Error('old epoch authorization remained usable');

    const hubRestart = await requestContainerAction('hub_start');
    const compute = await recoverComputeAuthority(computeAuthority, initialSchedule);
    const secondAdmission = await authorizeSfuGeneration(engineName, session, identities, 2);
    if (secondAdmission.authorization.reason !== 'hub_failover') throw new Error('Hub failover rekey reason missing');
    const ordinaryCleanup = await cleanupOrdinaryAudioFallback(pages.slice(0, 3));
    const restart = await requestContainerAction('sfu_start');
    await connectAll(pages, {
      admission: secondAdmission,
      keyBytes: secondKey,
      staleKeyBytes: firstKey,
    });
    await publishVideo(pages[0].page);
    await waitForDecoded(pages.slice(1, 3), 3);
    await delay(1_500);
    const recovered = await aggregateSfuMetrics(pages);
    requireRecoveredFlow(recovered);
    if (firstKey.equals(secondKey)) throw new Error('fresh epoch reused content key');

    const shutdown = await disconnectSfu(pages);
    result = {
      engine: engineName,
      pre_failure: {
        publisher_outbound_bytes: before.publisher.outbound_video_bytes,
        receiver_count: before.receivers.length,
        receiver_min_inbound_bytes: Math.min(...before.receivers.map(row => row.inbound_video_bytes)),
        receiver_min_decoded_samples: Math.min(...before.receivers.map(row => row.decoded_samples)),
        stale_probe_initial_inbound_bytes: before.stale.inbound_video_bytes,
        stale_probe_initial_decoded_samples: before.stale.decoded_samples,
      },
      outage: {
        sfu_sigkill_acknowledged: kill.ok === true,
        hub_sigkill_acknowledged: hubKill.ok === true,
        hub_api_unavailable_verified: hubUnavailable,
        reconnecting_client_count: shutdown.outageReconnectingCount,
        disconnected_client_count: shutdown.outageDisconnectedCount,
        semantic_room_count_during_fallback: ordinary.semanticRoomCount,
        ordinary_peer_connection_count: ordinary.peerConnectionCount,
        ordinary_receiver_count: ordinary.receiverCount,
        ordinary_min_outbound_bytes: ordinary.minOutboundBytes,
        ordinary_min_inbound_bytes: ordinary.minInboundBytes,
        controlled_mode: ordinary.mode,
      },
      recovery: {
        sfu_restart_acknowledged: restart.ok === true,
        hub_restart_acknowledged: hubRestart.ok === true,
        persistent_admission_state_resumed: secondAdmission.revisionBeforeMutation >= firstAdmission.revision,
        admission_revision_before_restart: firstAdmission.revision,
        admission_revision_after_restart: secondAdmission.revisionBeforeMutation,
        old_authorization_rejected_count: oldAuthorizationRejectedCount,
        fresh_admission_count: recovered.freshAdmissionCount,
        signature_verification_count: recovered.signatureVerificationCount,
        group_key_epoch: secondAdmission.authorization.epoch,
        previous_group_key_epoch: secondAdmission.authorization.previous_epoch,
        reason: secondAdmission.authorization.reason,
        fresh_key_distinct: true,
        receiver_count: recovered.receivers.length,
        receiver_min_inbound_bytes: Math.min(...recovered.receivers.map(row => row.inbound_video_bytes)),
        receiver_min_decoded_samples: Math.min(...recovered.receivers.map(row => row.decoded_samples)),
        stale_key_probe_inbound_bytes: recovered.stale.inbound_video_bytes,
        stale_key_probe_decoded_samples: recovered.stale.decoded_samples,
      },
      compute,
      cleanup: {
        ordinary_peer_connections_closed: ordinaryCleanup.closedPeerConnectionCount,
        ordinary_tracks_ended: ordinaryCleanup.audioTracksEnded,
        livekit_rooms_remaining: shutdown.roomsRemaining,
        livekit_workers_terminated: shutdown.workersTerminated,
        livekit_tracks_ended: shutdown.videoTracksEnded,
        browser_closed: false,
      },
      verdict: 'pass',
    };
  } finally {
    await cleanupOrdinaryAudioFallback(pages.slice(0, 3)).catch(() => undefined);
    await disconnectSfu(pages).catch(() => undefined);
    for (const { context } of pages) await context.close().catch(() => undefined);
    await browser.close();
    browserClosed = true;
  }
  if (!result || !browserClosed) throw new Error('browser cleanup failed');
  result.cleanup.browser_closed = true;
  return result;
}

async function hubRequest(identity, path, options = {}) {
  const tokenValue = fixture.tokens[identity];
  if (typeof tokenValue !== 'string' || tokenValue.length < 32) throw new Error('Hub user credential missing');
  const headers = {
    Authorization: `Bearer ${tokenValue}`,
    Accept: 'application/json',
    ...(options.body === undefined ? {} : { 'Content-Type': 'application/json' }),
    ...(options.idempotencyKey ? { 'Idempotency-Key': options.idempotencyKey } : {}),
    ...(options.ifMatch ? { 'If-Match': String(options.ifMatch) } : {}),
    ...(options.capabilityGrant
      ? { 'X-Semantic-Capability-Grant': options.capabilityGrant }
      : {}),
  };
  const response = await fetch(`${hubUrl}${path}`, {
    method: options.method ?? (options.body === undefined ? 'GET' : 'POST'),
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const value = await response.json().catch(() => ({}));
  const accepted = new Set(options.allowStatuses ?? []);
  if (!response.ok && !accepted.has(response.status)) {
    const code = value?.error?.code ?? value?.error ?? 'unknown';
    throw new Error(`productive Hub API rejected ${response.status}:${code}`);
  }
  return { status: response.status, value };
}

async function issueCapabilityGrant(engineName, session, {
  subjectId,
  capability,
  direction,
  scopeKind = 'session',
  scopeId = session.session_id,
  roomId,
}) {
  const idempotencyKey = [
    'semantic-grant', engineName, subjectId, capability, direction, scopeKind, scopeId,
  ].join('-');
  const { value } = await hubRequest('publisher', '/v1/semantic-media/capability-grants', {
    idempotencyKey,
    body: {
      session_id: session.session_id,
      ...(roomId ? { room_id: roomId } : {}),
      epoch: session.membership_epoch,
      subject_id: subjectId,
      subject_role: subjectId === 'publisher' ? 'participant' : 'compute_executor',
      capability,
      scope_kind: scopeKind,
      scope_id: scopeId,
      direction,
      data_type: semanticControlDataType,
      purpose: semanticControlPurpose,
      expires_at_ms: Date.now() + 300_000,
    },
  });
  const grant = value.grant;
  if (
    grant?.issuer !== 'hub'
    || grant?.subject_id !== subjectId
    || grant?.capability !== capability
    || grant?.scope_kind !== scopeKind
    || grant?.scope_id !== scopeId
    || grant?.direction !== direction
  ) throw new Error('productive Hub capability grant binding invalid');
  return grant.grant_id;
}

async function admissionState(identity, session) {
  const query = new URLSearchParams({
    session_id: session.session_id,
    membership_epoch: String(session.membership_epoch),
  });
  const { value } = await hubRequest(
    identity, `/v1/semantic-media/sfu/admissions/state?${query.toString()}`,
  );
  return value;
}

async function authorizeSfuGeneration(engineName, session, identities, generation) {
  if (typeof session.session_id !== 'string' || !Number.isInteger(session.membership_epoch)) {
    throw new Error('Hub session scope invalid');
  }
  const initialState = await admissionState(identities[0], session);
  const revisionBeforeMutation = initialState.revision;
  for (const identity of identities) {
    const current = await admissionState(identity, session);
    if (current.joined) continue;
    await hubRequest(identity, '/v1/semantic-media/sfu/admissions/join', {
      idempotencyKey: `sfu-join-${engineName}-${identity}`,
      body: {
        session_id: session.session_id,
        membership_epoch: session.membership_epoch,
        expected_revision: current.revision,
        idempotency_key: `sfu-join-${engineName}-${identity}`,
        strict_e2ee: true,
        e2ee_supported: true,
      },
    });
  }
  const publicationId = `failover-camera-${engineName}-${generation}`;
  const publisherState = await admissionState(identities[0], session);
  const publicationResponse = await hubRequest('publisher', '/v1/semantic-media/sfu/admissions/publications', {
    idempotencyKey: `sfu-publish-${engineName}-${generation}`,
    body: {
      session_id: session.session_id,
      membership_epoch: session.membership_epoch,
      expected_revision: publisherState.revision,
      idempotency_key: `sfu-publish-${engineName}-${generation}`,
      publication_id: publicationId,
      source: 'camera',
      kind: 'video',
      privacy: 'ordinary',
      audience_participant_id: null,
      authorized_subscriber_ids: identities.slice(1),
      constraints: {
        max_bitrate_bps: 1_000_000,
        max_width: 320,
        max_height: 180,
        max_fps: 15,
      },
    },
  });
  const accessTokens = { publisher: publicationResponse.value.access_token };
  let finalRevision = publicationResponse.value.revision;
  for (const identity of identities.slice(1)) {
    const current = await admissionState(identity, session);
    const subscriptionId = `failover-sub-${engineName}-${generation}-${identity}`;
    const subscription = await hubRequest(identity, '/v1/semantic-media/sfu/admissions/subscriptions', {
      idempotencyKey: `sfu-subscribe-${engineName}-${generation}-${identity}`,
      body: {
        session_id: session.session_id,
        membership_epoch: session.membership_epoch,
        expected_revision: current.revision,
        idempotency_key: `sfu-subscribe-${engineName}-${generation}-${identity}`,
        subscription_id: subscriptionId,
        publication_id: publicationId,
      },
    });
    accessTokens[identity] = subscription.value.access_token;
    finalRevision = subscription.value.revision;
  }
  const keyPackageRefs = Object.fromEntries(
    identities.map(identity => [identity, `failover-package-${engineName}-${generation}-${identity}`]),
  );
  const group = await hubRequest('publisher', '/v1/semantic-media/sfu/group-keys/epochs', {
    idempotencyKey: `sfu-group-${engineName}-${generation}`,
    body: {
      session_id: session.session_id,
      membership_epoch: session.membership_epoch,
      publication_id: publicationId,
      key_package_refs: keyPackageRefs,
      idempotency_key: `sfu-group-${engineName}-${generation}`,
    },
  });
  if (
    group.value.authorization?.epoch !== generation
    || group.value.authorization?.membership_epoch !== session.membership_epoch
    || group.value.authorization?.room_id !== publicationResponse.value.room_id
    || publicationResponse.value.server_url !== sfuUrl
  ) throw new Error('productive Hub SFU authority binding invalid');
  return {
    authorization: group.value.authorization,
    hubPublicKeyB64: group.value.hub_public_key_b64,
    accessTokens,
    roomId: publicationResponse.value.room_id,
    membershipEpoch: session.membership_epoch,
    revision: finalRevision,
    revisionBeforeMutation,
  };
}

function signedCapability(engineName, identity, session, roomId) {
  const { privateKey, publicKey } = generateKeyPairSync('ed25519');
  const publicJwk = publicKey.export({ format: 'jwk' });
  const publicBytes = Buffer.from(publicJwk.x, 'base64url');
  const keyId = `cap-${createHash('sha256').update(publicBytes).digest('hex').slice(0, 32)}`;
  const now = Date.now();
  const unsigned = {
    schema: 'ananta.semantic-capability-advertisement.v1',
    advertisement_id: `cap-${engineName}-${identity}`,
    session_id: session.session_id,
    room_id: roomId,
    epoch: session.membership_epoch,
    sender_id: identity,
    algorithms: ['heuristic-visual-v1', 'semantic-validator-v1'],
    roles: ['executor', 'validator'],
    task_types: ['visual_extract'],
    resource_profile: {
      cpu: 'medium', memory: 'medium', gpu: 'integrated', codec: 'hardware',
      battery: 'mains', network: 'normal',
    },
    measurements_expires_at_ms: now + 300_000,
    expires_at_ms: now + 300_000,
    max_delay_ms: 20_000,
    max_artifact_bytes: 1_048_576,
  };
  return {
    keyId,
    publicKeyB64: publicBytes.toString('base64'),
    advertisement: {
      ...unsigned,
      signature: {
        algorithm: 'ed25519',
        key_id: keyId,
        value: sign(null, Buffer.from(canonical(unsigned)), privateKey).toString('base64'),
      },
    },
  };
}

async function prepareComputeAuthority(engineName, session, roomId) {
  const advertisements = [];
  for (const identity of ['receiver-1', 'receiver-2', 'stale-key-probe']) {
    const computeSessionGrant = await issueCapabilityGrant(engineName, session, {
      subjectId: identity,
      capability: 'compute',
      direction: 'egress',
    });
    const computeRoomGrant = await issueCapabilityGrant(engineName, session, {
      subjectId: identity,
      capability: 'compute',
      direction: 'egress',
      scopeKind: 'room',
      scopeId: roomId,
      roomId,
    });
    const claim = signedCapability(engineName, identity, session, roomId);
    await hubRequest(identity, '/v1/semantic-media/compute/candidate-keys', {
      capabilityGrant: computeSessionGrant,
      body: {
        session_id: session.session_id,
        epoch: session.membership_epoch,
        key_id: claim.keyId,
        public_key_b64: claim.publicKeyB64,
        expires_at_ms: Date.now() + 300_000,
      },
    });
    await hubRequest(identity, '/v1/semantic-media/compute/capabilities', {
      capabilityGrant: computeRoomGrant,
      body: claim.advertisement,
    });
    advertisements.push(claim.advertisement);
  }
  const proposal = {
    profile: 'balanced',
    quality_level: 'standard',
    delay_ms: 20_000,
    security_mode: 'strict_e2ee',
    trusted_compute_grant: false,
    task_types: ['visual_extract'],
    max_artifact_bytes: 1_048_576,
    deadline_ms: 20_000,
    expires_at_ms: Date.now() + 300_000,
  };
  const publishRoomGrant = await issueCapabilityGrant(engineName, session, {
    subjectId: 'publisher',
    capability: 'publish',
    direction: 'egress',
    scopeKind: 'room',
    scopeId: roomId,
    roomId,
  });
  const publishSessionGrant = await issueCapabilityGrant(engineName, session, {
    subjectId: 'publisher', capability: 'publish', direction: 'egress',
  });
  const computeSessionGrant = await issueCapabilityGrant(engineName, session, {
    subjectId: 'publisher', capability: 'compute', direction: 'egress',
  });
  const subscribeSessionGrant = await issueCapabilityGrant(engineName, session, {
    subjectId: 'publisher', capability: 'subscribe', direction: 'ingress',
  });
  const offer = await hubRequest('publisher', '/v1/semantic-media/contracts/offers', {
    idempotencyKey: `compute-offer-${engineName}`,
    capabilityGrant: publishRoomGrant,
    body: {
      session_id: session.session_id,
      room_id: roomId,
      epoch: session.membership_epoch,
      policy_version: 'semantic-sfu-live-e2e-v1',
      consent_version: 1,
      proposal,
      advertisements: [],
    },
  });
  const contractId = offer.value.contract.contract_id;
  const accepted = await hubRequest('publisher', `/v1/semantic-media/contracts/${contractId}/accept`, {
    idempotencyKey: `compute-accept-${engineName}`,
    ifMatch: offer.value.contract.revision,
    capabilityGrant: publishSessionGrant,
    body: {
      session_id: session.session_id,
      epoch: session.membership_epoch,
      expected_revision: offer.value.contract.revision,
      consent_version: 1,
      proposal: {},
      advertisements,
    },
  });
  const active = await hubRequest('publisher', `/v1/semantic-media/contracts/${contractId}/activate`, {
    idempotencyKey: `compute-activate-${engineName}`,
    ifMatch: accepted.value.contract.revision,
    capabilityGrant: publishSessionGrant,
    body: {
      session_id: session.session_id,
      epoch: session.membership_epoch,
      expected_revision: accepted.value.contract.revision,
      consent_version: 1,
      proposal: {},
      advertisements,
    },
  });
  if (active.value.contract.status !== 'active' || active.value.contract.issuer !== 'hub') {
    throw new Error('productive Hub compute contract not active');
  }
  return {
    engineName,
    session,
    contractId,
    contractRevision: active.value.contract.revision,
    grants: {
      compute: computeSessionGrant,
      subscribe: subscribeSessionGrant,
    },
  };
}

async function scheduleCompute(authority, values, { allowStatuses = [] } = {}) {
  const response = await hubRequest(
    'publisher',
    `/v1/semantic-media/contracts/${authority.contractId}/schedule`,
    {
      idempotencyKey: values.idempotencyKey,
      ifMatch: authority.contractRevision,
      capabilityGrant: authority.grants.compute,
      allowStatuses,
      body: {
        session_id: authority.session.session_id,
        epoch: authority.session.membership_epoch,
        expected_revision: authority.contractRevision,
        task_type: 'visual_extract',
        audience: values.audience,
        sequence_start: values.sequenceStart,
        sequence_end: values.sequenceEnd,
        resource_budget: { cpu_ms: 100, memory_bytes: 1_048_576, artifact_bytes: 1_024 },
        deadline_epoch_ms: Date.now() + 19_000,
        validator_count: values.validatorCount,
        hot_standby: false,
      },
    },
  );
  return response.status === 201 ? response.value.schedule : response;
}

async function listComputeLeases(authority) {
  const query = new URLSearchParams({
    session_id: authority.session.session_id,
    epoch: String(authority.session.membership_epoch),
    limit: '100',
  });
  const { value } = await hubRequest(
    'publisher', `/v1/semantic-media/contracts/${authority.contractId}/leases?${query.toString()}`,
    { capabilityGrant: authority.grants.subscribe },
  );
  return value.leases.items;
}

async function recoverComputeAuthority(authority, initialSchedule) {
  const initial = initialSchedule.leases;
  const initialPrimary = initial.filter(item => item.role === 'primary');
  const initialValidator = initial.filter(item => item.role === 'validator');
  const persisted = await listComputeLeases(authority);
  const persistedIds = new Set(initial.map(item => item.lease_id));
  const persistedActive = persisted.filter(item => persistedIds.has(item.lease_id) && item.status === 'active');
  if (initialPrimary.length !== 1 || initialValidator.length !== 1 || persistedActive.length !== 2) {
    throw new Error('Hub compute lease state did not survive restart');
  }
  const primary = initialPrimary[0];
  const revokedResponse = await hubRequest(
    'publisher', `/v1/semantic-media/leases/${primary.lease_id}/revoke`,
    {
      idempotencyKey: `primary-revoke-${authority.engineName}`,
      capabilityGrant: authority.grants.compute,
      body: {
        session_id: authority.session.session_id,
        epoch: authority.session.membership_epoch,
        expected_version: primary.version,
        fencing_token: primary.fencing_token,
      },
    },
  );
  const replacement = await scheduleCompute(authority, {
    audience: 'receiver-1', sequenceStart: 0, sequenceEnd: 9, validatorCount: 0,
    idempotencyKey: `replacement-primary-${authority.engineName}`,
  });
  const replacementPrimary = replacement.leases.filter(item => item.role === 'primary');
  const conflictValues = suffix => ({
    audience: 'receiver-2', sequenceStart: 100, sequenceEnd: 109, validatorCount: 1,
    idempotencyKey: `validator-conflict-${authority.engineName}-${suffix}`,
  });
  const conflictResponses = await Promise.all([
    scheduleCompute(authority, conflictValues('a'), { allowStatuses: [409, 422] }),
    scheduleCompute(authority, conflictValues('b'), { allowStatuses: [409, 422] }),
  ]);
  const successful = conflictResponses.filter(item => Array.isArray(item?.leases));
  const rejected = conflictResponses.filter(item => [409, 422].includes(item?.status));
  const rejectionReason = rejected[0]?.value?.error?.code ?? '';
  const finalLeases = await listComputeLeases(authority);
  const active = finalLeases.filter(item => item.status === 'active');
  const conflictScope = active.filter(item => (
    item.audience === 'receiver-2' && item.sequence_start === 100 && item.sequence_end === 109
  ));
  const scopeCounts = new Map();
  for (const item of active) {
    const scope = `${item.role}|${item.audience}|${item.sequence_start}|${item.sequence_end}`;
    scopeCounts.set(scope, (scopeCounts.get(scope) ?? 0) + 1);
  }
  const duplicateActiveLeaseCount = [...scopeCounts.values()].reduce(
    (sum, count) => sum + Math.max(0, count - 1), 0,
  );
  const allAuthoritative = [initialSchedule, replacement, ...successful].every(
    item => item.authoritative_source === 'hub' && item.leases.every(lease => lease.authoritative_source === 'hub'),
  );
  return {
    initial_primary_lease_count: initialPrimary.length,
    initial_validator_lease_count: initialValidator.length,
    persisted_active_lease_count_after_restart: persistedActive.length,
    revoked_primary_lease_count: revokedResponse.value.lease?.status === 'revoked' ? 1 : 0,
    replacement_primary_lease_count: replacementPrimary.length,
    replacement_fencing_token_advanced: (
      replacementPrimary.length === 1 && replacementPrimary[0].fencing_token > primary.fencing_token
    ),
    validator_conflict_request_count: conflictResponses.length,
    validator_conflict_success_count: successful.length,
    validator_conflict_rejection_count: rejected.length,
    validator_conflict_reason: rejectionReason,
    conflict_scope_active_primary_count: conflictScope.filter(item => item.role === 'primary').length,
    conflict_scope_active_validator_count: conflictScope.filter(item => item.role === 'validator').length,
    duplicate_active_lease_count: duplicateActiveLeaseCount,
    hub_remained_sole_lease_authority: allAuthoritative,
  };
}

async function verifyHubUnavailable() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 2_000);
  try {
    await fetch(`${hubUrl}/healthz`, { signal: controller.signal });
    return false;
  } catch {
    return true;
  } finally {
    clearTimeout(timeout);
  }
}

async function createPage(browser, identity) {
  const context = await browser.newContext({ permissions: [] });
  const page = await context.newPage();
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
    window.__anantaSecurity = {
      latestEpoch: 0,
      signatureVerificationCount: 0,
      freshAdmissionCount: 0,
      staleAuthorizationRejections: 0,
      events: [],
      mode: 'disconnected',
    };
    window.__anantaSession = null;
    window.__anantaOrdinary = null;
    window.__anantaCleanup = { workersTerminated: 0, videoTracksEnded: 0 };
  });
  await page.goto(origin);
  await page.addScriptTag({ url: `${origin}/livekit.js` });
  return { identity, context, page };
}

async function connectAll(pages, options) {
  for (const entry of pages) {
    const stale = entry.identity === 'stale-key-probe' && options.staleKeyBytes;
    const admissionToken = options.admission.accessTokens[entry.identity];
    if (!admissionToken) throw new Error('productive Hub admission token missing');
    await entry.page.evaluate(async payload => {
      const security = window.__anantaSecurity;
      const stable = value => {
        if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`;
        if (value && typeof value === 'object') {
          return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${stable(value[key])}`).join(',')}}`;
        }
        return JSON.stringify(value);
      };
      const decode = value => {
        const binary = atob(value);
        return Uint8Array.from(binary, character => character.charCodeAt(0));
      };
      const verify = async authorization => {
        const signature = decode(authorization.signature_b64);
        const unsigned = { ...authorization };
        delete unsigned.signature_b64;
        const key = await crypto.subtle.importKey(
          'raw', decode(payload.hubPublicKeyB64), { name: 'Ed25519' }, false, ['verify'],
        );
        const valid = await crypto.subtle.verify(
          { name: 'Ed25519' }, key, signature, new TextEncoder().encode(stable(unsigned)),
        );
        security.signatureVerificationCount += 1;
        if (!valid) throw new Error('hub signature invalid');
        const now = Date.now();
        if (
          authorization.room_id !== payload.roomId
          || authorization.membership_epoch !== payload.membershipEpoch
        ) {
          throw new Error('authorization context invalid');
        }
        if (!authorization.member_ids.includes(payload.identity)) throw new Error('membership missing');
        if (authorization.valid_from_ms > now || authorization.expires_at_ms <= now) throw new Error('authorization expired');
        if (security.latestEpoch > 0 && (
          authorization.epoch <= security.latestEpoch || authorization.previous_epoch !== security.latestEpoch
        )) {
          security.staleAuthorizationRejections += 1;
          throw new Error('stale_group_key_epoch');
        }
        security.latestEpoch = authorization.epoch;
      };
      await verify(payload.authorization);
      const lk = window.LivekitClient;
      const provider = new lk.ExternalE2EEKeyProvider({ keySize: 256 });
      await provider.setKey(Uint8Array.from(payload.keyBytes).buffer);
      const worker = new Worker('/e2ee-worker.js');
      const room = new lk.Room({
        adaptiveStream: false,
        dynacast: true,
        singlePeerConnection: true,
        encryption: { keyProvider: provider, worker },
      });
      const session = {
        room,
        worker,
        provider,
        decoded: 0,
        subscribed: 0,
        videos: [],
        videoTrack: null,
        generation: crypto.randomUUID(),
        peerConnectionStart: window.__anantaPeerConnections.length,
      };
      window.__anantaSession = session;
      const events = security.events;
      room.on(lk.RoomEvent.Reconnecting, () => events.push('reconnecting'));
      room.on(lk.RoomEvent.SignalReconnecting, () => events.push('signalReconnecting'));
      room.on(lk.RoomEvent.Reconnected, () => events.push('reconnected'));
      room.on(lk.RoomEvent.Disconnected, () => events.push('disconnected'));
      room.on(lk.RoomEvent.TrackSubscribed, track => {
        session.subscribed += 1;
        if (track.kind !== 'video') return;
        const video = track.attach();
        video.muted = true;
        video.autoplay = true;
        document.body.append(video);
        session.videos.push(video);
        void video.play().catch(() => undefined);
        const generation = session.generation;
        const sample = () => {
          if (window.__anantaSession?.generation !== generation) return;
          if (video.readyState >= 2 && video.videoWidth > 0) session.decoded += 1;
          setTimeout(sample, 100);
        };
        sample();
      });
      await room.connect(payload.sfuUrl, payload.admissionToken, { autoSubscribe: payload.identity !== 'publisher' });
      await room.setE2EEEnabled(true);
      security.freshAdmissionCount += 1;
      security.mode = 'semantic_sfu';
    }, {
      authorization: options.admission.authorization,
      hubPublicKeyB64: options.admission.hubPublicKeyB64,
      identity: entry.identity,
      roomId: options.admission.roomId,
      membershipEpoch: options.admission.membershipEpoch,
      admissionToken,
      sfuUrl,
      keyBytes: [...(stale || options.keyBytes)],
    });
  }
}

async function publishVideo(page) {
  await page.evaluate(async () => {
    const session = window.__anantaSession;
    const canvas = document.createElement('canvas');
    canvas.width = 320;
    canvas.height = 180;
    document.body.append(canvas);
    const context = canvas.getContext('2d');
    let frame = 0;
    session.paint = setInterval(() => {
      context.fillStyle = `rgb(${frame % 255},${(frame * 3) % 255},${(frame * 7) % 255})`;
      context.fillRect(0, 0, canvas.width, canvas.height);
      frame += 1;
    }, 50);
    session.canvas = canvas;
    session.videoTrack = canvas.captureStream(15).getVideoTracks()[0];
    await session.room.localParticipant.publishTrack(session.videoTrack, {
      name: 'failover-camera',
      source: window.LivekitClient.Track.Source.Camera,
    });
  });
}

async function waitForDecoded(entries, minimum) {
  for (const { page } of entries) {
    await page.waitForFunction(value => window.__anantaSession?.decoded >= value, minimum, { timeout: 25_000 });
  }
}

async function waitForOutage(pages) {
  for (const { page } of pages) {
    await page.waitForFunction(() => {
      const events = window.__anantaSecurity?.events ?? [];
      return events.includes('reconnecting') || events.includes('signalReconnecting');
    }, null, { timeout: 25_000 });
  }
}

async function disconnectSfu(pages) {
  for (const { page } of pages) {
    await page.evaluate(async () => {
      const session = window.__anantaSession;
      if (!session) return;
      clearInterval(session.paint);
      if (session.videoTrack && session.videoTrack.readyState !== 'ended') {
        session.videoTrack.stop();
        window.__anantaCleanup.videoTracksEnded += 1;
      }
      for (const video of session.videos) video.remove();
      session.canvas?.remove();
      await session.room.disconnect(true);
      session.worker.terminate();
      window.__anantaCleanup.workersTerminated += 1;
      window.__anantaSession = null;
      window.__anantaSecurity.mode = 'disconnected';
    });
  }
  const states = await Promise.all(pages.map(({ page }) => page.evaluate(() => ({
    events: [...window.__anantaSecurity.events],
    hasSession: Boolean(window.__anantaSession),
    workersTerminated: window.__anantaCleanup.workersTerminated,
    videoTracksEnded: window.__anantaCleanup.videoTracksEnded,
  }))));
  return {
    outageReconnectingCount: states.filter(row => row.events.includes('reconnecting') || row.events.includes('signalReconnecting')).length,
    outageDisconnectedCount: states.filter(row => row.events.includes('disconnected')).length,
    roomsRemaining: states.filter(row => row.hasSession).length,
    workersTerminated: states.reduce((sum, row) => sum + row.workersTerminated, 0),
    videoTracksEnded: states.reduce((sum, row) => sum + row.videoTracksEnded, 0),
  };
}

async function establishOrdinaryAudioFallback(entries) {
  const publisher = entries[0].page;
  await publisher.evaluate(async () => {
    const context = new AudioContext();
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    const destination = context.createMediaStreamDestination();
    gain.gain.value = 0.001;
    oscillator.frequency.value = 440;
    oscillator.connect(gain).connect(destination);
    oscillator.start();
    window.__anantaOrdinary = {
      context,
      oscillator,
      audioTrack: destination.stream.getAudioTracks()[0],
      pcs: [],
      mode: 'ordinary_audio_fallback',
    };
    window.__anantaSecurity.mode = 'ordinary_audio_fallback';
  });
  for (const receiverEntry of entries.slice(1)) {
    const offer = await publisher.evaluate(async () => {
      const ordinary = window.__anantaOrdinary;
      const pc = new RTCPeerConnection();
      ordinary.pcs.push(pc);
      pc.addTrack(ordinary.audioTrack);
      await pc.setLocalDescription(await pc.createOffer());
      if (pc.iceGatheringState !== 'complete') {
        await new Promise(resolveIce => {
          const listener = () => {
            if (pc.iceGatheringState === 'complete') {
              pc.removeEventListener('icegatheringstatechange', listener);
              resolveIce();
            }
          };
          pc.addEventListener('icegatheringstatechange', listener);
        });
      }
      return pc.localDescription.toJSON();
    });
    const answer = await receiverEntry.page.evaluate(async remoteOffer => {
      const pc = new RTCPeerConnection();
      window.__anantaOrdinary = { pcs: [pc], audioElements: [], mode: 'ordinary_audio_fallback' };
      window.__anantaSecurity.mode = 'ordinary_audio_fallback';
      pc.ontrack = event => {
        const audio = document.createElement('audio');
        audio.autoplay = true;
        audio.muted = true;
        audio.srcObject = event.streams[0];
        document.body.append(audio);
        window.__anantaOrdinary.audioElements.push(audio);
        void audio.play().catch(() => undefined);
      };
      await pc.setRemoteDescription(remoteOffer);
      await pc.setLocalDescription(await pc.createAnswer());
      if (pc.iceGatheringState !== 'complete') {
        await new Promise(resolveIce => {
          const listener = () => {
            if (pc.iceGatheringState === 'complete') {
              pc.removeEventListener('icegatheringstatechange', listener);
              resolveIce();
            }
          };
          pc.addEventListener('icegatheringstatechange', listener);
        });
      }
      return pc.localDescription.toJSON();
    }, offer);
    await publisher.evaluate(async remoteAnswer => {
      const pcs = window.__anantaOrdinary.pcs;
      await pcs[pcs.length - 1].setRemoteDescription(remoteAnswer);
    }, answer);
  }
  for (const { page } of entries) {
    await page.waitForFunction(() => (
      window.__anantaOrdinary?.pcs?.length > 0
      && window.__anantaOrdinary.pcs.every(pc => ['connected', 'completed'].includes(pc.connectionState))
    ), null, { timeout: 15_000 });
  }
  await delay(1_500);
  const publisherBytes = await publisher.evaluate(async () => {
    const values = [];
    for (const pc of window.__anantaOrdinary.pcs) {
      let bytes = 0;
      (await pc.getStats()).forEach(row => {
        if (row.type === 'outbound-rtp' && (row.kind ?? row.mediaType) === 'audio') bytes += Number(row.bytesSent ?? 0);
      });
      values.push(Math.trunc(bytes));
    }
    return values;
  });
  const receiverBytes = [];
  for (const { page } of entries.slice(1)) {
    receiverBytes.push(await page.evaluate(async () => {
      let bytes = 0;
      for (const pc of window.__anantaOrdinary.pcs) {
        (await pc.getStats()).forEach(row => {
          if (row.type === 'inbound-rtp' && (row.kind ?? row.mediaType) === 'audio') bytes += Number(row.bytesReceived ?? 0);
        });
      }
      return Math.trunc(bytes);
    }));
  }
  return {
    mode: 'ordinary_audio_fallback',
    semanticRoomCount: await countSessions(entries),
    peerConnectionCount: publisherBytes.length + receiverBytes.length,
    receiverCount: receiverBytes.length,
    minOutboundBytes: Math.min(...publisherBytes),
    minInboundBytes: Math.min(...receiverBytes),
  };
}

async function cleanupOrdinaryAudioFallback(entries) {
  let closedPeerConnectionCount = 0;
  let audioTracksEnded = 0;
  for (const { page } of entries) {
    const cleaned = await page.evaluate(async () => {
      const ordinary = window.__anantaOrdinary;
      if (!ordinary) return { pcs: 0, ended: 0 };
      let ended = 0;
      if (ordinary.audioTrack && ordinary.audioTrack.readyState !== 'ended') {
        ordinary.audioTrack.stop();
        ended += 1;
      }
      ordinary.oscillator?.stop();
      await ordinary.context?.close();
      for (const pc of ordinary.pcs ?? []) pc.close();
      for (const audio of ordinary.audioElements ?? []) audio.remove();
      const pcs = ordinary.pcs?.length ?? 0;
      window.__anantaOrdinary = null;
      window.__anantaSecurity.mode = 'disconnected';
      return { pcs, ended };
    });
    closedPeerConnectionCount += cleaned.pcs;
    audioTracksEnded += cleaned.ended;
  }
  return { closedPeerConnectionCount, audioTracksEnded };
}

async function rejectStaleAuthorization(pages, authorizationValue, hubPublicKeyB64) {
  let rejected = 0;
  for (const { page } of pages) {
    rejected += await page.evaluate(async ({ authorization, publicKeyB64 }) => {
      const stable = value => {
        if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`;
        if (value && typeof value === 'object') {
          return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${stable(value[key])}`).join(',')}}`;
        }
        return JSON.stringify(value);
      };
      const binary = atob(authorization.signature_b64);
      const signature = Uint8Array.from(binary, character => character.charCodeAt(0));
      const publicBinary = atob(publicKeyB64);
      const publicKey = Uint8Array.from(publicBinary, character => character.charCodeAt(0));
      const unsigned = { ...authorization };
      delete unsigned.signature_b64;
      const key = await crypto.subtle.importKey('raw', publicKey, { name: 'Ed25519' }, false, ['verify']);
      const valid = await crypto.subtle.verify(
        { name: 'Ed25519' }, key, signature, new TextEncoder().encode(stable(unsigned)),
      );
      window.__anantaSecurity.signatureVerificationCount += 1;
      if (!valid) throw new Error('hub signature invalid during stale probe');
      if (authorization.epoch <= window.__anantaSecurity.latestEpoch) {
        window.__anantaSecurity.staleAuthorizationRejections += 1;
        return 1;
      }
      return 0;
    }, { authorization: authorizationValue, publicKeyB64: hubPublicKeyB64 });
  }
  return rejected;
}

async function aggregateSfuMetrics(pages) {
  const rows = [];
  for (const entry of pages) rows.push({ identity: entry.identity, ...(await sfuStats(entry.page)) });
  const security = await Promise.all(pages.map(({ page }) => page.evaluate(() => ({
    signatureVerificationCount: window.__anantaSecurity.signatureVerificationCount,
    freshAdmissionCount: window.__anantaSecurity.freshAdmissionCount,
  }))));
  return {
    publisher: rows.find(row => row.identity === 'publisher'),
    receivers: rows.filter(row => row.identity.startsWith('receiver-')),
    stale: rows.find(row => row.identity === 'stale-key-probe'),
    signatureVerificationCount: security.reduce((sum, row) => sum + row.signatureVerificationCount, 0),
    freshAdmissionCount: security.reduce((sum, row) => sum + row.freshAdmissionCount, 0),
  };
}

async function sfuStats(page) {
  return page.evaluate(async () => {
    let sent = 0;
    let received = 0;
    const start = window.__anantaSession?.peerConnectionStart ?? window.__anantaPeerConnections?.length ?? 0;
    for (const pc of (window.__anantaPeerConnections ?? []).slice(start)) {
      const report = await pc.getStats();
      report.forEach(row => {
        if (row.type === 'outbound-rtp' && (row.kind ?? row.mediaType) === 'video' && !row.isRemote) {
          sent += Number(row.bytesSent ?? 0);
        }
        if (row.type === 'inbound-rtp' && (row.kind ?? row.mediaType) === 'video' && !row.isRemote) {
          received += Number(row.bytesReceived ?? 0);
        }
      });
    }
    return {
      outbound_video_bytes: Math.trunc(sent),
      inbound_video_bytes: Math.trunc(received),
      decoded_samples: window.__anantaSession?.decoded ?? 0,
    };
  });
}

function requireInitialFlow(metrics) {
  if (!metrics.publisher || metrics.publisher.outbound_video_bytes <= 0) throw new Error('initial publisher flow missing');
  if (metrics.receivers.length !== 2 || metrics.receivers.some(row => row.inbound_video_bytes <= 0 || row.decoded_samples < 3)) {
    throw new Error('initial receiver flow missing');
  }
  if (!metrics.stale || metrics.stale.inbound_video_bytes <= 0 || metrics.stale.decoded_samples < 3) {
    throw new Error('initial stale-key baseline missing');
  }
}

function requireOrdinaryFlow(metrics) {
  if (metrics.mode !== 'ordinary_audio_fallback' || metrics.semanticRoomCount !== 0) {
    throw new Error('ordinary fallback mode not exclusive');
  }
  if (metrics.receiverCount !== 2 || metrics.peerConnectionCount !== 4) throw new Error('ordinary fallback topology invalid');
  if (metrics.minOutboundBytes <= 0 || metrics.minInboundBytes <= 0) throw new Error('ordinary audio flow missing');
}

function requireRecoveredFlow(metrics) {
  if (!metrics.publisher || metrics.publisher.outbound_video_bytes <= 0) throw new Error('recovered publisher flow missing');
  if (metrics.receivers.length !== 2 || metrics.receivers.some(row => row.inbound_video_bytes <= 0 || row.decoded_samples < 3)) {
    throw new Error('recovered receiver flow missing');
  }
  if (!metrics.stale || metrics.stale.inbound_video_bytes <= 0 || metrics.stale.decoded_samples !== 0) {
    throw new Error('stale key decoded recovery media');
  }
  if (metrics.freshAdmissionCount !== 8 || metrics.signatureVerificationCount < 12) {
    throw new Error('fresh Hub admission evidence incomplete');
  }
}

async function requestContainerAction(action) {
  controlSequence += 1;
  const request = { version: 1, sequence: controlSequence, action };
  const temporary = `${requestPath}.${process.pid}.tmp`;
  writeFileSync(temporary, `${JSON.stringify(request)}\n`, { encoding: 'utf8' });
  renameSync(temporary, requestPath);
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    if (existsSync(responsePath)) {
      const response = JSON.parse(readFileSync(responsePath, 'utf8'));
      if (response.sequence === controlSequence) {
        if (response.ok !== true || response.action !== action) throw new Error(`container ${action} rejected`);
        return response;
      }
    }
    await delay(100);
  }
  throw new Error(`container ${action} timed out`);
}

async function countSessions(entries) {
  const values = await Promise.all(entries.map(({ page }) => page.evaluate(() => Boolean(window.__anantaSession))));
  return values.filter(Boolean).length;
}

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

function delay(milliseconds) { return new Promise(resolveDelay => setTimeout(resolveDelay, milliseconds)); }
function stream(response, path, contentType) {
  response.writeHead(200, { 'content-type': contentType, 'cache-control': 'no-store' });
  createReadStream(path).pipe(response);
  return undefined;
}
