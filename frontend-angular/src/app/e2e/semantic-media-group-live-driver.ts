import { firstValueFrom } from 'rxjs';

import { AgentDirectoryService } from '../services/agent-directory.service';
import { E2eEncryptionService, KeyEnvelope } from '../services/e2e-encryption.service';
import {
  LivekitSfuRoomFactory,
  SfuPublishedTrack,
  SfuRoomPort,
} from '../services/livekit-sfu-room.adapter';
import {
  SemanticSfuAdmissionApiService,
  SemanticSfuState,
  SemanticSfuToken,
} from '../services/semantic-sfu-admission-api.service';
import {
  ReceivedSfuGroupEpoch,
  SemanticSfuGroupContext,
  SemanticSfuGroupKeyService,
} from '../services/semantic-sfu-group-key.service';
import { UserAuthService } from '../services/user-auth.service';
import { GroupKeyEpochAuthorization, WebrtcGroupKeyService } from '../services/webrtc-group-key.service';

export interface SemanticMediaGroupParticipantConfig {
  readonly hubUrl: string;
  readonly tenantId: string;
  readonly sessionId: string;
  readonly membershipEpoch: number;
  readonly localPeerId: string;
}

export interface SemanticMediaGroupDriverDependencies {
  readonly admission: SemanticSfuAdmissionApiService;
  readonly agentDirectory: AgentDirectoryService;
  readonly authentication: UserAuthService;
  readonly deviceEncryption: E2eEncryptionService;
  readonly groupKeys: SemanticSfuGroupKeyService;
  readonly keyRegistry: WebrtcGroupKeyService;
}

export interface SemanticMediaGroupEpochSummary {
  readonly authorization: GroupKeyEpochAuthorization & { readonly membership_epoch: number };
  readonly acknowledgedMemberCount: number;
  readonly pendingMemberCount: number;
}

export interface SemanticMediaGroupParticipantDriver {
  deviceIdentity(): Promise<KeyEnvelope>;
  authenticate(hubUserToken: string): Promise<void>;
  configure(config: SemanticMediaGroupParticipantConfig): void;
  admissionState(): Promise<SemanticSfuState>;
  join(expectedRevision?: number): Promise<Readonly<{ roomId: string; revision: number }>>;
  authorizePublisher(
    audienceIds: readonly string[],
    generation: number,
  ): Promise<Readonly<{ publicationIds: readonly string[]; revision: number }>>;
  authorizeSubscriptions(
    publicationIds: readonly string[],
    generation: number,
  ): Promise<Readonly<{ revision: number }>>;
  preparePublisherEpoch(memberIds: readonly string[]): Promise<SemanticMediaGroupEpochSummary>;
  epochStatus(authorizationId: string): Promise<Readonly<{
    acknowledgedMemberCount: number; pendingMemberCount: number;
  }>>;
  receiveEpoch(): Promise<Readonly<{ installed: boolean; epoch: number; reason: string }>>;
  connect(): Promise<void>;
  connectWithoutAuthorizedEpoch(): Promise<void>;
  publish(audienceIds: readonly string[]): Promise<number>;
  frameCount(): number;
  audioPlayable(): boolean;
  waitForFrames(minimum: number, timeoutMs?: number): Promise<number>;
  framesRemainStable(stableMs?: number, timeoutMs?: number): Promise<boolean>;
  hasAuthorizedEpoch(authorization: GroupKeyEpochAuthorization): boolean;
  ordinaryFallback(): Promise<boolean>;
  disconnect(): Promise<void>;
  cleanup(): Promise<void>;
}

/** Install the live-only adapter around production admission and key services. */
export function installSemanticMediaGroupLiveDriver(
  dependencies: SemanticMediaGroupDriverDependencies,
): void {
  if (
    typeof window === 'undefined'
    || new URL(window.location.href).searchParams.get('semanticMediaGroupLiveE2e') !== '1'
  ) return;
  const runtime = new ProductGroupParticipantDriver(dependencies);
  const target = window as unknown as {
    __ANANTA_SEMANTIC_MEDIA_GROUP_E2E__?: SemanticMediaGroupParticipantDriver;
  };
  target.__ANANTA_SEMANTIC_MEDIA_GROUP_E2E__ = Object.freeze({
    deviceIdentity: () => runtime.deviceIdentity(),
    authenticate: token => runtime.authenticate(token),
    configure: config => runtime.configure(config),
    admissionState: () => runtime.admissionState(),
    join: revision => runtime.join(revision),
    authorizePublisher: (audience, generation) => runtime.authorizePublisher(audience, generation),
    authorizeSubscriptions: (publications, generation) => runtime.authorizeSubscriptions(publications, generation),
    preparePublisherEpoch: members => runtime.preparePublisherEpoch(members),
    epochStatus: authorizationId => runtime.epochStatus(authorizationId),
    receiveEpoch: () => runtime.receiveEpoch(),
    connect: () => runtime.connect(),
    connectWithoutAuthorizedEpoch: () => runtime.connectWithoutAuthorizedEpoch(),
    publish: audience => runtime.publish(audience),
    frameCount: () => runtime.frameCount(),
    audioPlayable: () => runtime.audioPlayable(),
    waitForFrames: (minimum, timeout) => runtime.waitForFrames(minimum, timeout),
    framesRemainStable: (stable, timeout) => runtime.framesRemainStable(stable, timeout),
    hasAuthorizedEpoch: authorization => runtime.hasAuthorizedEpoch(authorization),
    ordinaryFallback: () => runtime.ordinaryFallback(),
    disconnect: () => runtime.disconnect(),
    cleanup: () => runtime.cleanup(),
  });
}

class ProductGroupParticipantDriver implements SemanticMediaGroupParticipantDriver {
  private readonly factory = new LivekitSfuRoomFactory();
  private context: SemanticMediaGroupParticipantConfig | null = null;
  private room: SfuRoomPort | null = null;
  private admissionToken: SemanticSfuToken | null = null;
  private activeKey: Uint8Array | null = null;
  private desiredPublicationIds: readonly string[] = Object.freeze([]);
  private localPublicationIds: readonly string[] = Object.freeze([]);
  private audienceVendorIdentities: Readonly<Record<string, string>> = Object.freeze({});
  private published: SfuPublishedTrack[] = [];
  private releases: Array<() => void> = [];
  private mediaElements: HTMLMediaElement[] = [];
  private audioElements: HTMLAudioElement[] = [];
  private decodedFrames = 0;
  private videoFixture: ReturnType<typeof createVideoFixture> | null = null;
  private audioFixture: Awaited<ReturnType<typeof createAudioFixture>> | null = null;

  constructor(private readonly dependencies: SemanticMediaGroupDriverDependencies) {}

  deviceIdentity(): Promise<KeyEnvelope> {
    return this.dependencies.deviceEncryption.ensureLocalKeyPair();
  }

  async authenticate(hubUserToken: string): Promise<void> {
    if (typeof hubUserToken !== 'string' || hubUserToken.split('.').length !== 3) {
      throw new Error('semantic_group_hub_user_token_invalid');
    }
    await this.dependencies.authentication.setTokens(hubUserToken);
  }

  configure(config: SemanticMediaGroupParticipantConfig): void {
    validateConfig(config);
    this.context = Object.freeze({ ...config });
    // Product HTTP authentication deliberately resolves trust from the Hub
    // directory instead of attaching an arbitrary bearer to unknown origins.
    this.dependencies.agentDirectory.upsert({
      name: 'hub',
      role: 'hub',
      url: config.hubUrl,
      token: '',
    });
  }

  admissionState(): Promise<SemanticSfuState> {
    const context = this.requireContext();
    return firstValueFrom(this.dependencies.admission.state(
      context.hubUrl,
      context.sessionId,
      context.membershipEpoch,
    ));
  }

  async join(expectedRevision?: number): Promise<Readonly<{ roomId: string; revision: number }>> {
    const context = this.requireContext();
    const revision = expectedRevision ?? (await this.admissionState()).revision;
    const token = await firstValueFrom(this.dependencies.admission.join(
      context.hubUrl,
      mutation(context, revision, 'join'),
      true,
    ));
    this.admissionToken = token;
    return Object.freeze({ roomId: token.roomId, revision: token.revision });
  }

  async authorizePublisher(
    audienceIds: readonly string[],
    generation: number,
  ): Promise<Readonly<{ publicationIds: readonly string[]; revision: number }>> {
    const context = this.requireContext();
    const audience = normalizedMembers(audienceIds, false);
    if (audience.includes(context.localPeerId)) throw new Error('semantic_group_audience_invalid');
    if (!Number.isSafeInteger(generation) || generation < 1) throw new Error('semantic_group_generation_invalid');
    const publicationIds = [
      `group-camera-${generation}-${crypto.randomUUID()}`,
      `group-microphone-${generation}-${crypto.randomUUID()}`,
    ];
    const requests = [
      {
        publicationId: publicationIds[0], source: 'camera' as const, kind: 'video' as const,
        constraints: { max_bitrate_bps: 900_000, max_width: 320, max_height: 180, max_fps: 15 },
      },
      {
        publicationId: publicationIds[1], source: 'microphone' as const, kind: 'audio' as const,
        constraints: { max_bitrate_bps: 96_000, max_width: 0, max_height: 0, max_fps: 0 },
      },
    ];
    let revision = (await this.admissionState()).revision;
    for (const request of requests) {
      const token = await firstValueFrom(this.dependencies.admission.authorizePublication(
        context.hubUrl,
        mutation(context, revision, `publish-${generation}`),
        {
          ...request,
          privacy: 'ordinary',
          audienceParticipantId: null,
          authorizedSubscriberIds: audience,
        },
      ));
      if (!token.publication || token.publication.publication_id !== request.publicationId) {
        throw new Error('semantic_group_publication_admission_missing');
      }
      this.admissionToken = token;
      this.audienceVendorIdentities = Object.freeze({
        ...this.audienceVendorIdentities,
        ...token.authorizedSubscriberLivekitIdentities,
      });
      revision = token.revision;
    }
    this.localPublicationIds = Object.freeze(publicationIds);
    return Object.freeze({ publicationIds: this.localPublicationIds, revision });
  }

  async authorizeSubscriptions(
    publicationIds: readonly string[],
    generation: number,
  ): Promise<Readonly<{ revision: number }>> {
    const context = this.requireContext();
    const publications = normalizedMembers(publicationIds, false);
    if (publications.length !== 2 || !Number.isSafeInteger(generation) || generation < 1) {
      throw new Error('semantic_group_subscription_request_invalid');
    }
    let revision = (await this.admissionState()).revision;
    for (const publicationId of publications) {
      const token = await firstValueFrom(this.dependencies.admission.authorizeSubscription(
        context.hubUrl,
        mutation(context, revision, `subscribe-${generation}`),
        `group-sub-${generation}-${crypto.randomUUID()}`,
        publicationId,
      ));
      if (!token.subscription || token.subscription.publication_id !== publicationId) {
        throw new Error('semantic_group_subscription_admission_missing');
      }
      this.admissionToken = token;
      revision = token.revision;
    }
    this.desiredPublicationIds = Object.freeze([...publications]);
    return Object.freeze({ revision });
  }

  async preparePublisherEpoch(memberIds: readonly string[]): Promise<SemanticMediaGroupEpochSummary> {
    const context = this.groupContext();
    const members = normalizedMembers(memberIds, true);
    if (!members.includes(context.localPeerId) || this.localPublicationIds.length !== 2) {
      throw new Error('semantic_group_epoch_context_invalid');
    }
    const prepared = await this.dependencies.groupKeys.createPublisherEpoch(
      context,
      this.localPublicationIds[0],
      members,
    );
    await this.installKey(prepared.keyMaterial);
    const status = await this.dependencies.groupKeys.status(
      context,
      prepared.authorization.authorization_id,
    );
    return Object.freeze({
      authorization: prepared.authorization,
      acknowledgedMemberCount: status.acknowledgedMemberIds.length,
      pendingMemberCount: status.pendingMemberIds.length,
    });
  }

  async epochStatus(authorizationId: string): Promise<Readonly<{
    acknowledgedMemberCount: number; pendingMemberCount: number;
  }>> {
    const status = await this.dependencies.groupKeys.status(this.groupContext(), authorizationId);
    return Object.freeze({
      acknowledgedMemberCount: status.acknowledgedMemberIds.length,
      pendingMemberCount: status.pendingMemberIds.length,
    });
  }

  async receiveEpoch(): Promise<Readonly<{ installed: boolean; epoch: number; reason: string }>> {
    const context = this.groupContext();
    const page = await this.dependencies.groupKeys.receiveAvailable(context);
    if (!page.installed) return Object.freeze({ installed: false, epoch: 0, reason: 'no_addressed_epoch' });
    await this.installReceived(page.installed);
    return Object.freeze({
      installed: true,
      epoch: page.installed.authorization.epoch,
      reason: page.installed.authorization.reason,
    });
  }

  async connect(): Promise<void> {
    if (!this.activeKey) throw new Error('semantic_group_authorized_epoch_required');
    await this.connectWithKey(this.activeKey);
  }

  async connectWithoutAuthorizedEpoch(): Promise<void> {
    const probe = crypto.getRandomValues(new Uint8Array(32));
    try {
      await this.connectWithKey(probe);
    } finally {
      probe.fill(0);
    }
  }

  async publish(audienceIds: readonly string[]): Promise<number> {
    const room = this.requireRoom();
    if (this.localPublicationIds.length !== 2) throw new Error('semantic_group_publications_missing');
    const audience = normalizedMembers(audienceIds, false);
    const vendorAudience = audience.map(participantId => {
      const vendorIdentity = this.audienceVendorIdentities[participantId];
      if (!vendorIdentity) throw new Error('semantic_group_vendor_audience_missing');
      return vendorIdentity;
    });
    this.videoFixture = createVideoFixture();
    this.audioFixture = await createAudioFixture();
    this.published.push(await room.publish(this.localPublicationIds[0], 'camera', this.videoFixture.track));
    this.published.push(await room.publish(this.localPublicationIds[1], 'microphone', this.audioFixture.track));
    room.setTrackAudience(new Map(this.published.map(publication => [publication.trackSid, vendorAudience])));
    return this.published.length;
  }

  frameCount(): number { return this.decodedFrames; }

  audioPlayable(): boolean {
    return this.audioElements.some(element => {
      const track = (element.srcObject as MediaStream | null)?.getAudioTracks()[0];
      return Boolean(track && track.readyState === 'live' && element.currentTime >= 0.1);
    });
  }

  async waitForFrames(minimum: number, timeoutMs = 20_000): Promise<number> {
    if (!Number.isSafeInteger(minimum) || minimum < 1) throw new Error('semantic_group_frame_target_invalid');
    await waitUntil(() => this.decodedFrames >= minimum && this.audioPlayable(), timeoutMs, 'group_media_timeout');
    return this.decodedFrames;
  }

  framesRemainStable(stableMs = 1_500, timeoutMs = 6_000): Promise<boolean> {
    return remainsStable(() => this.decodedFrames, stableMs, timeoutMs);
  }

  hasAuthorizedEpoch(authorization: GroupKeyEpochAuthorization): boolean {
    try {
      this.dependencies.keyRegistry.getKey(
        authorization.room_id,
        authorization.publication_id,
        authorization.epoch,
      );
      return true;
    } catch {
      return false;
    }
  }

  ordinaryFallback(): Promise<boolean> { return verifyOrdinaryBrowserFallback(); }

  async disconnect(): Promise<void> {
    await this.releaseTransport();
  }

  async cleanup(): Promise<void> {
    await this.releaseTransport();
    this.dependencies.groupKeys.clear();
    this.activeKey?.fill(0);
    this.activeKey = null;
    this.admissionToken = null;
    this.localPublicationIds = Object.freeze([]);
    this.desiredPublicationIds = Object.freeze([]);
    this.audienceVendorIdentities = Object.freeze({});
  }

  private async installReceived(installed: ReceivedSfuGroupEpoch): Promise<void> {
    await this.dependencies.groupKeys.acknowledge(this.groupContext(), installed);
    await this.installKey(installed.keyMaterial);
  }

  private async installKey(material: Uint8Array): Promise<void> {
    if (material.byteLength !== 32) throw new Error('semantic_group_content_key_invalid');
    if (this.room) await this.room.rotateKey(material);
    this.activeKey?.fill(0);
    this.activeKey = Uint8Array.from(material);
    material.fill(0);
  }

  private async connectWithKey(material: Uint8Array): Promise<void> {
    const token = this.admissionToken;
    if (!token) throw new Error('semantic_group_hub_admission_required');
    await this.releaseTransport();
    const room = await this.factory.create(material);
    this.room = room;
    this.bindRoom(room);
    await room.connect(token.serverUrl, token.accessToken);
    if (this.desiredPublicationIds.length) {
      room.applyRemoteSubscriptions(new Set(this.desiredPublicationIds));
    }
  }

  private bindRoom(room: SfuRoomPort): void {
    this.releases.push(room.onRemotePublication(publication => {
      publication.setSubscribed(this.desiredPublicationIds.includes(publication.publicationId));
    }));
    this.releases.push(room.onRemoteTrackSubscribed(remote => {
      if (!this.desiredPublicationIds.includes(remote.publicationId)) return;
      if (remote.track.kind === 'audio') {
        const audio = document.createElement('audio');
        audio.muted = true;
        audio.autoplay = true;
        audio.srcObject = new MediaStream([remote.track]);
        document.body.append(audio);
        this.audioElements.push(audio);
        this.mediaElements.push(audio);
        void audio.play().catch(() => undefined);
        return;
      }
      if (remote.track.kind !== 'video') return;
      const video = document.createElement('video');
      video.muted = true;
      video.autoplay = true;
      video.playsInline = true;
      video.srcObject = new MediaStream([remote.track]);
      document.body.append(video);
      this.mediaElements.push(video);
      void video.play().catch(() => undefined);
      if (typeof video.requestVideoFrameCallback === 'function') {
        const observe: VideoFrameRequestCallback = () => {
          if (!video.isConnected) return;
          this.decodedFrames += 1;
          video.requestVideoFrameCallback(observe);
        };
        video.requestVideoFrameCallback(observe);
      } else {
        let previous = 0;
        const observe = () => {
          if (!video.isConnected) return;
          const current = video.getVideoPlaybackQuality().totalVideoFrames;
          this.decodedFrames += Math.max(0, current - previous);
          previous = current;
          window.setTimeout(observe, 100);
        };
        observe();
      }
    }));
  }

  private async releaseTransport(): Promise<void> {
    for (const release of this.releases.splice(0)) release();
    for (const publication of this.published.splice(0)) {
      await this.room?.unpublish(publication).catch(() => undefined);
    }
    await this.room?.disconnect().catch(() => undefined);
    this.room = null;
    for (const element of this.mediaElements.splice(0)) {
      element.pause();
      element.srcObject = null;
      element.remove();
    }
    this.audioElements = [];
    this.videoFixture?.stop();
    this.videoFixture = null;
    this.audioFixture?.stop();
    this.audioFixture = null;
  }

  private requireContext(): SemanticMediaGroupParticipantConfig {
    if (!this.context) throw new Error('semantic_group_context_missing');
    return this.context;
  }

  private groupContext(): SemanticSfuGroupContext {
    const context = this.requireContext();
    return {
      hubUrl: context.hubUrl,
      tenantId: context.tenantId,
      sessionId: context.sessionId,
      membershipEpoch: context.membershipEpoch,
      localPeerId: context.localPeerId,
    };
  }

  private requireRoom(): SfuRoomPort {
    if (!this.room) throw new Error('semantic_group_room_missing');
    return this.room;
  }
}

function mutation(
  context: SemanticMediaGroupParticipantConfig,
  expectedRevision: number,
  operation: string,
) {
  return {
    sessionId: context.sessionId,
    membershipEpoch: context.membershipEpoch,
    expectedRevision,
    idempotencyKey: `group-${operation}-${context.membershipEpoch}-${crypto.randomUUID()}`,
  };
}

function validateConfig(value: SemanticMediaGroupParticipantConfig): void {
  if (
    !/^https?:\/\/[^\s]+$/.test(value.hubUrl)
    || !identifier(value.tenantId)
    || !identifier(value.sessionId)
    || !identifier(value.localPeerId)
    || !Number.isSafeInteger(value.membershipEpoch)
    || value.membershipEpoch < 1
  ) throw new Error('semantic_group_context_invalid');
}

function normalizedMembers(values: readonly string[], requireTwo: boolean): readonly string[] {
  const normalized = [...new Set(values)].sort();
  const minimum = requireTwo ? 2 : 1;
  if (
    normalized.length < minimum
    || normalized.length > 8
    || normalized.length !== values.length
    || normalized.some(value => !identifier(value))
  ) throw new Error('semantic_group_member_set_invalid');
  return Object.freeze(normalized);
}

function identifier(value: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value);
}

async function verifyOrdinaryBrowserFallback(): Promise<boolean> {
  const sender = new RTCPeerConnection({ iceServers: [] });
  const receiver = new RTCPeerConnection({ iceServers: [] });
  const fixture = await createAudioFixture();
  try {
    sender.addTrack(fixture.track, new MediaStream([fixture.track]));
    sender.onicecandidate = event => { if (event.candidate) void receiver.addIceCandidate(event.candidate); };
    receiver.onicecandidate = event => { if (event.candidate) void sender.addIceCandidate(event.candidate); };
    await sender.setLocalDescription(await sender.createOffer());
    await receiver.setRemoteDescription(sender.localDescription);
    await receiver.setLocalDescription(await receiver.createAnswer());
    await sender.setRemoteDescription(receiver.localDescription);
    await waitUntil(
      () => ['connected', 'completed'].includes(receiver.iceConnectionState),
      8_000,
      'ordinary_fallback_timeout',
    );
    await delay(750);
    const reports = await receiver.getStats();
    let bytes = 0;
    reports.forEach(row => {
      if (row.type === 'inbound-rtp' && (row.kind ?? row.mediaType) === 'audio') {
        bytes += Number(row.bytesReceived ?? 0);
      }
    });
    return bytes > 0;
  } finally {
    sender.close();
    receiver.close();
    fixture.stop();
  }
}

async function createAudioFixture(): Promise<{ track: MediaStreamTrack; stop(): void }> {
  const context = new AudioContext();
  const oscillator = context.createOscillator();
  const gain = context.createGain();
  const destination = context.createMediaStreamDestination();
  gain.gain.value = 0.02;
  oscillator.frequency.value = 440;
  oscillator.connect(gain);
  gain.connect(destination);
  oscillator.start();
  if (context.state === 'suspended') await context.resume();
  const track = destination.stream.getAudioTracks()[0];
  if (!track) throw new Error('semantic_group_audio_track_unavailable');
  return {
    track,
    stop: () => {
      try { oscillator.stop(); } catch { /* deterministic cleanup */ }
      track.stop();
      void context.close();
    },
  };
}

function createVideoFixture(): { track: MediaStreamTrack; stop(): void } {
  const canvas = document.createElement('canvas');
  canvas.width = 320;
  canvas.height = 180;
  const context = canvas.getContext('2d');
  if (!context || typeof canvas.captureStream !== 'function') {
    throw new Error('semantic_group_canvas_capture_unavailable');
  }
  let frame = 0;
  const draw = () => {
    context.fillStyle = `rgb(${frame % 255},${(frame * 3) % 255},${(frame * 7) % 255})`;
    context.fillRect(0, 0, canvas.width, canvas.height);
    frame += 1;
  };
  draw();
  const interval = window.setInterval(draw, 50);
  const track = canvas.captureStream(15).getVideoTracks()[0];
  if (!track) throw new Error('semantic_group_video_track_unavailable');
  return {
    track,
    stop: () => {
      window.clearInterval(interval);
      track.stop();
      canvas.remove();
    },
  };
}

function delay(milliseconds: number): Promise<void> {
  return new Promise(resolve => window.setTimeout(resolve, milliseconds));
}

async function waitUntil(predicate: () => boolean, timeoutMs: number, reason: string): Promise<void> {
  const deadline = performance.now() + timeoutMs;
  while (!predicate()) {
    if (performance.now() >= deadline) throw new Error(reason);
    await delay(50);
  }
}

async function remainsStable(
  current: () => number,
  stableMs: number,
  timeoutMs: number,
): Promise<boolean> {
  const deadline = performance.now() + timeoutMs;
  let observed = current();
  let stableSince = performance.now();
  while (performance.now() < deadline) {
    await delay(100);
    const next = current();
    if (next !== observed) {
      observed = next;
      stableSince = performance.now();
    }
    if (performance.now() - stableSince >= stableMs) return true;
  }
  return false;
}
