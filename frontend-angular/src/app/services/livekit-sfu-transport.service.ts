import { Inject, Injectable, InjectionToken, OnDestroy, inject } from '@angular/core';
import {
  BehaviorSubject,
  Subject,
  filter,
  firstValueFrom,
  map,
  timeout,
  type Observable,
} from 'rxjs';

import { SFU_ROOM_SESSION_FACTORY } from './sfu-room-session.factory';
import { SFU_ALLOW_INSECURE_LOCALHOST_TRANSPORT, isAllowedSfuEndpoint } from './sfu-secure-endpoint.policy';
import type {
  SfuOpaqueDataPacket,
  SfuPublishedTrack,
  SfuRemoteTrack,
  SfuRemoteVideoHandle,
  SfuRoomSession,
  SfuRoomSessionFactory,
  SfuStatsBinding,
  SfuStatsPort,
  SfuSubscriptionLayerPort,
} from './sfu-room-session.ports';
import type { ValidatedSfuBroadcastContract } from './sfu-broadcast-contracts';
import { SfuBroadcastPublicationControllerService } from './sfu-broadcast-publication-controller.service';
import { SfuBroadcastCapabilityProbeService } from './sfu-broadcast-capability-probe.service';
import { SfuBrowserCapabilityApiService } from './sfu-browser-capability-api.service';
import {
  SfuLayerProjectionClientService,
  type SfuPublisherProjectionClientBinding,
  type SfuReceiverProjectionClientBinding,
} from './sfu-layer-projection-client.service';

export interface SfuPublicationGrant {
  readonly publication_id: string;
  readonly source: 'microphone' | 'camera' | 'screen';
  readonly kind: 'audio' | 'video';
  readonly privacy: 'ordinary' | 'private_recovery';
  readonly authorized_subscriber_ids: readonly string[];
}

export interface SfuClientAdmission {
  readonly server_url: string;
  readonly access_token: string;
  readonly room_id: string;
  readonly membership_epoch: number;
  readonly expires_at: number;
  readonly publications: readonly SfuPublicationGrant[];
  readonly subscription_publication_ids: readonly string[];
  readonly strict_e2ee: boolean;
  readonly layer_control_mode?: 'adaptive_stream' | 'manual_quality';
  readonly tenant_ref?: string;
  readonly admission_epoch?: number;
  readonly receiver_ref?: string;
  readonly projection_required?: boolean;
  readonly publisher_projection_bindings?: readonly Omit<SfuPublisherProjectionClientBinding, 'tenantRef' | 'roomRef' | 'membershipEpoch'>[];
  readonly receiver_projection_bindings?: readonly Omit<SfuReceiverProjectionClientBinding, 'tenantRef' | 'roomRef' | 'membershipEpoch' | 'receiverRef'>[];
}

export interface SfuTransportView {
  readonly status: 'idle' | 'connecting' | 'connected' | 'fallback' | 'failed';
  readonly roomId: string | null;
  readonly membershipEpoch: number | null;
  readonly reasonCode: string;
  readonly publicationCount: number;
}

/** Read-only UI projection; it exposes neither transport commands nor a session. */
export interface SfuTransportProjectionPort {
  readonly state$: Observable<SfuTransportView>;
  currentState(): SfuTransportView;
  authorizedSubscriberIds(): ReadonlySet<string>;
}

export const SFU_TRANSPORT_PROJECTION = new InjectionToken<SfuTransportProjectionPort>(
  'SFU_TRANSPORT_PROJECTION',
  {
    providedIn: 'root',
    factory: () => inject(LivekitSfuTransportService).projection,
  },
);

@Injectable({ providedIn: 'root' })
export class LivekitSfuTransportService implements OnDestroy {
  readonly state$ = new BehaviorSubject<SfuTransportView>(Object.freeze({
    status: 'idle', roomId: null, membershipEpoch: null, reasonCode: 'sfu_not_requested', publicationCount: 0,
  }));
  readonly remoteTrack$ = new Subject<SfuRemoteTrack>();
  readonly remoteVideo$ = new Subject<SfuRemoteVideoHandle>();
  readonly remoteVideoUnavailable$ = new Subject<Readonly<{
    handleId: string; reason: 'unsubscribed' | 'ended';
  }>>();
  readonly remoteVideoState$ = new Subject<Readonly<{
    handleId: string; state: 'active' | 'muted';
  }>>();
  readonly opaqueData$ = new Subject<SfuOpaqueDataPacket>();
  readonly subscriberLost$ = new Subject<Readonly<{ receiverId: string }>>();
  readonly projection: SfuTransportProjectionPort = Object.freeze({
    state$: this.state$.asObservable(),
    currentState: () => this.state$.value,
    authorizedSubscriberIds: () => this.authorizedSubscriberIds(),
  });
  private session: SfuRoomSession | null = null;
  private operationGeneration = 0;
  private admissionExpiryTimer: ReturnType<typeof setTimeout> | null = null;
  private admissionDeadlineMs = 0;
  private readonly allowInsecureLocalhost = inject(SFU_ALLOW_INSECURE_LOCALHOST_TRANSPORT);
  private admission: SfuClientAdmission | null = null;
  private readonly published = new Map<string, SfuPublishedTrack>();
  private readonly confirmedSubscribersByPublication = new Map<string, Set<string>>();
  private readonly subscriberConfirmed$ = new Subject<Readonly<{ publicationId: string; receiverId: string }>>();
  private readonly cleanupBySession = new Map<SfuRoomSession, (() => void)[]>();
  private readonly destructionBySession = new WeakMap<SfuRoomSession, Promise<void>>();
  private readonly publicationController = inject(SfuBroadcastPublicationControllerService);
  private readonly layerProjections = inject(SfuLayerProjectionClientService);
  private readonly capabilityProbe = inject(SfuBroadcastCapabilityProbeService);
  private readonly capabilityApi = inject(SfuBrowserCapabilityApiService);

  constructor(
    @Inject(SFU_ROOM_SESSION_FACTORY) private readonly sessions: SfuRoomSessionFactory,
  ) {}

  async connect(
    admission: Readonly<SfuClientAdmission>,
    keyMaterial: Uint8Array,
    capability: 'supported' | 'unsupported' | 'unknown',
    nowSeconds = Math.trunc(Date.now() / 1000),
  ): Promise<'sfu' | 'ordinary'> {
    await this.disconnect('sfu_replaced');
    const operationGeneration = ++this.operationGeneration;
    validateAdmission(admission, keyMaterial, nowSeconds, this.allowInsecureLocalhost);
    const admissionDeadlineMs = resolveAdmissionDeadlineMs(admission.expires_at, nowSeconds);
    if (capability !== 'supported') {
      this.emit('fallback', admission, 'sfu_e2ee_capability_unknown');
      return 'ordinary';
    }
    this.emit('connecting', admission, 'sfu_connecting');
    let connectingSession: SfuRoomSession | null = null;
    try {
      if (admission.tenant_ref && admission.admission_epoch) {
        const observed = this.capabilityProbe.probe({
          tenantRef: admission.tenant_ref, roomRef: admission.room_id,
          admissionEpoch: admission.admission_epoch, membershipEpoch: admission.membership_epoch,
        });
        await this.capabilityApi.submit(admission.room_id, observed.observation);
        this.assertOperationCurrent(operationGeneration);
      } else if (admission.projection_required) {
        throw new Error('sfu_capability_scope_unavailable');
      }
      const roomProjection = admission.tenant_ref
        ? await this.layerProjections.loadRoom({
            tenantRef: admission.tenant_ref, roomRef: admission.room_id,
            membershipEpoch: admission.membership_epoch, admissionEpoch: admission.admission_epoch,
          })
        : null;
      this.assertOperationCurrent(operationGeneration);
      if (admission.projection_required && !roomProjection) throw new Error('sfu_room_projection_unavailable');
      const projectedMode = roomProjection
        ? String((roomProjection.contract.document as unknown as Record<string, unknown>)['layer_control_mode'])
        : null;
      const layerControlMode = projectedMode === 'manual_quality' || projectedMode === 'adaptive_stream'
        ? projectedMode
        : admission.layer_control_mode ?? 'adaptive_stream';
      const session = await this.sessions.create(keyMaterial, {
        layerControlMode,
      });
      connectingSession = session;
      this.assertOperationCurrent(operationGeneration);
      this.session = session;
      const cleanup: (() => void)[] = [];
      this.cleanupBySession.set(session, cleanup);
      if (admission.strict_e2ee && !session.lifecycle.e2eeSupported) {
        throw new Error('sfu_e2ee_unsupported');
      }
      session.publications.denySubscriptionsByDefault();
      cleanup.push(session.events.onRemotePublication(publication => {
        if (!this.isCurrentSession(operationGeneration, session)) return;
        const permitted = admission.subscription_publication_ids.includes(publication.publicationId);
        session.subscriptions.setRemotePublicationSubscribed(publication.publicationId, permitted);
      }));
      cleanup.push(session.events.onRemoteTrackSubscribed(value => {
        if (!this.isCurrentSession(operationGeneration, session)) return;
        const permitted = admission.subscription_publication_ids.includes(value.publicationId);
        if (permitted) this.remoteTrack$.next(Object.freeze({ ...value }));
      }));
      if (session.videoRender.onRemoteVideoAvailable) {
        cleanup.push(session.videoRender.onRemoteVideoAvailable(handle => {
          if (!this.isCurrentSession(operationGeneration, session)) return;
          if (admission.subscription_publication_ids.some(publicationId => (
            session.videoRender.matchesPublication?.(handle, publicationId) === true
          ))) {
            this.remoteVideo$.next(handle);
          }
        }));
      }
      if (session.videoRender.onRemoteVideoUnavailable) {
        cleanup.push(session.videoRender.onRemoteVideoUnavailable(value => {
          if (!this.isCurrentSession(operationGeneration, session)) return;
          this.remoteVideoUnavailable$.next(value);
        }));
      }
      if (session.videoRender.onRemoteVideoStateChanged) {
        cleanup.push(session.videoRender.onRemoteVideoStateChanged(value => {
          if (!this.isCurrentSession(operationGeneration, session)) return;
          this.remoteVideoState$.next(value);
        }));
      }
      cleanup.push(session.events.onLocalTrackSubscribed(publicationId => {
        if (!this.isCurrentSession(operationGeneration, session)) return;
        const grant = admission.publications.find(value => value.publication_id === publicationId);
        if (!grant || grant.authorized_subscriber_ids.length !== 1) return;
        const receiverId = grant.authorized_subscriber_ids[0];
        this.confirmedSubscribersByPublication.set(publicationId, new Set([receiverId]));
        this.subscriberConfirmed$.next(Object.freeze({ publicationId, receiverId }));
        if (this.admission) this.emit('connected', this.admission, 'sfu_subscriber_confirmed');
      }));
      cleanup.push(session.events.onRemoteParticipantDisconnected(receiverId => {
        if (!this.isCurrentSession(operationGeneration, session)) return;
        let removed = false;
        for (const [publicationId, confirmedIds] of this.confirmedSubscribersByPublication) {
          if (!confirmedIds.delete(receiverId)) continue;
          if (!confirmedIds.size) this.confirmedSubscribersByPublication.delete(publicationId);
          removed = true;
        }
        if (!removed) return;
        this.subscriberLost$.next(Object.freeze({ receiverId }));
        if (this.admission) this.emit('connected', this.admission, 'sfu_subscriber_disconnected');
      }));
      cleanup.push(session.events.onOpaqueDataReceived(packet => {
        if (!this.isCurrentSession(operationGeneration, session)) return;
        this.opaqueData$.next(Object.freeze({ ...packet }));
      }));
      cleanup.push(session.events.onDisconnected(() => {
        if (!this.isCurrentSession(operationGeneration, session)) return;
        const disconnectedAdmission = this.admission ?? admission;
        ++this.operationGeneration;
        this.clearAdmissionExpiry();
        this.admission = null;
        this.admissionDeadlineMs = 0;
        this.stopLocalTracks();
        this.emit('fallback', disconnectedAdmission, 'sfu_disconnected');
        void this.destroySession(session);
      }));
      await session.lifecycle.connect(admission.server_url, admission.access_token);
      this.assertCurrentSession(operationGeneration, session);
      if (Date.now() >= admissionDeadlineMs) throw new Error('sfu_admission_expired');
      session.subscriptions.applyRemoteSubscriptions(new Set(admission.subscription_publication_ids));
      this.admission = admission;
      this.admissionDeadlineMs = admissionDeadlineMs;
      if (admission.tenant_ref && admission.receiver_ref) {
        for (const binding of admission.receiver_projection_bindings ?? []) {
          if (!admission.subscription_publication_ids.includes(binding.publicationRef)) continue;
          const release = await this.layerProjections.bindReceiver({
            ...binding, tenantRef: admission.tenant_ref, roomRef: admission.room_id,
            receiverRef: admission.receiver_ref, membershipEpoch: admission.membership_epoch,
            admissionEpoch: admission.admission_epoch,
          }, session.subscriptions);
          this.assertCurrentSession(operationGeneration, session);
          if (Date.now() >= admissionDeadlineMs) throw new Error('sfu_admission_expired');
          cleanup.push(release);
        }
      }
      this.armAdmissionExpiry(operationGeneration, admission, admissionDeadlineMs);
      this.emit('connected', admission, 'sfu_connected');
      return 'sfu';
    } catch (error) {
      await this.destroySession(connectingSession);
      if (this.operationGeneration === operationGeneration) {
        this.clearAdmissionExpiry();
        this.admission = null;
        this.admissionDeadlineMs = 0;
        this.emit('fallback', admission, error instanceof Error ? error.message : 'sfu_connect_failed');
      }
      return 'ordinary';
    }
  }

  async publish(publicationId: string, track: MediaStreamTrack): Promise<void> {
    const session = this.session;
    const admission = this.admission;
    if (!session || !admission || this.state$.value.status !== 'connected' || !this.admissionUsable()) {
      throw new Error('sfu_not_connected');
    }
    const grant = admission.publications.find(value => value.publication_id === publicationId);
    if (!grant || grant.privacy !== 'ordinary') throw new Error('sfu_publication_not_authorized');
    if (track.kind !== grant.kind) throw new Error('sfu_publication_kind_mismatch');
    if (this.published.has(publicationId)) throw new Error('sfu_publication_duplicate');
    const operationGeneration = this.operationGeneration;
    const publication = await session.publications.publish(publicationId, grant.source, track);
    if (!this.isCurrentSession(operationGeneration, session) || !this.admissionUsable()) {
      await session.publications.unpublish(publication).catch(() => undefined);
      safeStop(publication.track);
      throw new Error('sfu_publication_fenced');
    }
    this.published.set(publicationId, publication);
    this.applyAudiencePermissions();
    this.emit('connected', admission, 'sfu_connected');
    track.onended = () => { void this.unpublish(publicationId); };
  }

  async publishProjected(
    contract: Extract<ValidatedSfuBroadcastContract, {
      contractId: 'ananta.sfu-publisher-layer-projection.v1';
    }>,
    track: MediaStreamTrack,
  ): Promise<SfuPublishedTrack> {
    const session = this.session;
    const admission = this.admission;
    if (!session || !admission || this.state$.value.status !== 'connected' || !this.admissionUsable()) {
      throw new Error('sfu_not_connected');
    }
    const grant = admission.publications.find(value => (
      value.publication_id === contract.document.publication_ref
    ));
    if (!grant || grant.privacy !== 'ordinary' || grant.kind !== track.kind) {
      throw new Error('sfu_publication_not_authorized');
    }
    const publicationId = contract.document.publication_ref;
    if (this.published.has(publicationId)) throw new Error('sfu_publication_duplicate');
    const operationGeneration = this.operationGeneration;
    const publication = await this.publicationController.apply(session.publications, contract, track);
    if (!this.isCurrentSession(operationGeneration, session) || !this.admissionUsable()) {
      const stopped = await this.publicationController.stop(session.publications, publicationId)
        .catch(() => false);
      if (!stopped) await session.publications.unpublish(publication).catch(() => undefined);
      safeStop(publication.track);
      throw new Error('sfu_publication_fenced');
    }
    this.published.set(publicationId, publication);
    this.applyAudiencePermissions();
    this.emit('connected', admission, 'sfu_connected');
    track.onended = () => { void this.unpublish(publicationId); };
    return publication;
  }

  async publishFromHubProjection(publicationId: string, track: MediaStreamTrack): Promise<SfuPublishedTrack> {
    const admission = this.admission;
    if (!admission?.tenant_ref) throw new Error('sfu_publisher_projection_scope_unavailable');
    const binding = admission.publisher_projection_bindings?.find(item => item.publicationRef === publicationId);
    if (!binding) throw new Error('sfu_publisher_projection_binding_unavailable');
    const projection = await this.layerProjections.loadPublisher({
      ...binding, tenantRef: admission.tenant_ref, roomRef: admission.room_id,
      membershipEpoch: admission.membership_epoch, admissionEpoch: admission.admission_epoch,
    });
    if (!projection || projection.kind !== 'publisher') throw new Error('sfu_publisher_projection_unavailable');
    return this.publishProjected(projection.contract as Extract<ValidatedSfuBroadcastContract, {
      contractId: 'ananta.sfu-publisher-layer-projection.v1';
    }>, track);
  }

  subscriptionLayerPort(): SfuSubscriptionLayerPort | null {
    if (!this.session || !this.admission || this.state$.value.status !== 'connected' || !this.admissionUsable()) return null;
    return this.session.subscriptions;
  }

  statsPortFor(handle: SfuRemoteVideoHandle): SfuStatsPort | null {
    if (!this.session || !this.admission || this.state$.value.status !== 'connected' || !this.admissionUsable()) {
      return null;
    }

    const session = this.session;
    const admission = this.admission;
    const operationGeneration = this.operationGeneration;
    const publicationRef = admission.subscription_publication_ids.find(publicationId => (
      session.videoRender.matchesPublication?.(handle, publicationId) === true
    ));
    const projectionBinding = admission.receiver_projection_bindings?.find(binding => (
      binding.publicationRef === publicationRef
    ));
    if (!publicationRef || !projectionBinding || !admission.tenant_ref) return null;

    const remainsAuthorized = (): boolean => (
      this.session === session
      && this.admission === admission
      && this.operationGeneration === operationGeneration
      && this.state$.value.status === 'connected'
      && this.admissionUsable()
      && session.videoRender.matchesPublication?.(handle, publicationRef) === true
    );
    const read = session.stats.read;
    return Object.freeze({
      capability: session.stats.capability,
      ...(read
        ? {
            read: (candidate: SfuRemoteVideoHandle) => (
              candidate === handle && remainsAuthorized()
                ? read.call(session.stats, candidate)
                : Promise.resolve(null)
            ),
          }
        : {}),
      authorizesQualityBinding: (
        candidate: SfuRemoteVideoHandle,
        binding: SfuStatsBinding,
      ): boolean => (
        candidate === handle
        && remainsAuthorized()
        && binding.tenantRef === admission.tenant_ref
        && binding.roomRef === admission.room_id
        && binding.subscriptionRef === projectionBinding.subscriptionRef
        && binding.publicationRef === publicationRef
        && binding.routeEpoch === projectionBinding.routeEpoch
        && binding.membershipEpoch === admission.membership_epoch
      ),
    });
  }

  remoteVideoMatchesPublication(handle: SfuRemoteVideoHandle, publicationId: string): boolean {
    return Boolean(
      this.session
      && this.admission
      && this.state$.value.status === 'connected'
      && this.admissionUsable()
      && this.admission.subscription_publication_ids.includes(publicationId)
      && this.session.videoRender.matchesPublication?.(handle, publicationId),
    );
  }

  attachRemoteVideo(handle: SfuRemoteVideoHandle, target: HTMLVideoElement): () => void {
    if (!this.session || !this.admission || this.state$.value.status !== 'connected' || !this.admissionUsable()
        || !this.admission.subscription_publication_ids.some(publicationId => (
          this.session?.videoRender.matchesPublication?.(handle, publicationId) === true
        ))
        || !this.session.videoRender.attachRemoteVideo) {
      throw new Error('sfu_video_subscription_not_authorized');
    }
    return this.session.videoRender.attachRemoteVideo(handle, target);
  }

  async unpublish(publicationId: string): Promise<void> {
    const publication = this.published.get(publicationId);
    if (!publication) return;
    this.published.delete(publicationId);
    this.confirmedSubscribersByPublication.delete(publicationId);
    try {
      const session = this.session;
      const stoppedProjected = session
        ? await this.publicationController.stop(session.publications, publicationId)
        : false;
      if (!stoppedProjected) await session?.publications.unpublish(publication);
    } finally {
      safeStop(publication.track);
      this.applyAudiencePermissions();
      if (this.admission) this.emit('connected', this.admission, 'sfu_connected');
    }
  }

  async publishOpaqueData(
    payload: Uint8Array,
    topic: string,
    destinationIds: readonly string[],
  ): Promise<void> {
    if (!this.session || !this.admission || this.state$.value.status !== 'connected' || !this.admissionUsable()) {
      throw new Error('sfu_not_connected');
    }
    await this.session.data.publishOpaqueData(payload, topic, destinationIds);
  }

  /** Read-only delivery projection used by receiver-path UI. */
  authorizedSubscriberIds(): ReadonlySet<string> {
    if (!this.admission || this.state$.value.status !== 'connected' || !this.admissionUsable()) return new Set<string>();
    return new Set([...this.confirmedSubscribersByPublication.values()].flatMap(value => [...value]));
  }

  confirmAuthorizedSubscriber(publicationId: string, receiverId: string): void {
    const admission = this.admission;
    const grant = admission?.publications.find(value => value.publication_id === publicationId);
    if (!admission || this.state$.value.status !== 'connected' || !this.admissionUsable() || !grant
        || !grant.authorized_subscriber_ids.includes(receiverId)) {
      throw new Error('sfu_subscriber_confirmation_not_authorized');
    }
    const confirmed = this.confirmedSubscribersByPublication.get(publicationId) ?? new Set<string>();
    confirmed.add(receiverId);
    this.confirmedSubscribersByPublication.set(publicationId, confirmed);
    this.subscriberConfirmed$.next(Object.freeze({ publicationId, receiverId }));
    this.emit('connected', admission, 'sfu_subscriber_confirmed');
  }

  async waitForSubscriber(publicationId: string, receiverId: string, timeoutMs = 15_000): Promise<boolean> {
    const admission = this.admission;
    const grant = admission?.publications.find(value => value.publication_id === publicationId);
    if (!admission || this.state$.value.status !== 'connected' || !this.admissionUsable() || !grant
        || !grant.authorized_subscriber_ids.includes(receiverId)) return false;
    if (this.confirmedSubscribersByPublication.get(publicationId)?.has(receiverId)) return true;
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 60_000) return false;
    try {
      return await firstValueFrom(this.subscriberConfirmed$.pipe(
        filter(value => value.publicationId === publicationId && value.receiverId === receiverId),
        map(() => true),
        timeout({ first: timeoutMs }),
      ));
    } catch {
      return false;
    }
  }

  async rotateKey(nextMembershipEpoch: number, keyMaterial: Uint8Array): Promise<void> {
    if (!this.session || !this.admission || !this.admissionUsable()) throw new Error('sfu_not_connected');
    if (!Number.isSafeInteger(nextMembershipEpoch) || nextMembershipEpoch <= this.admission.membership_epoch
        || keyMaterial.byteLength !== 32) throw new Error('sfu_rekey_invalid');
    for (const publication of this.published.values()) publication.track.enabled = false;
    try {
      await this.session.key.rotateKey(keyMaterial);
    } catch {
      await this.disconnect('sfu_rekey_failed');
      throw new Error('sfu_rekey_failed');
    }
    await this.disconnect('sfu_rekey_admission_required');
  }

  async disconnect(reasonCode = 'sfu_user_disconnect'): Promise<void> {
    const operationGeneration = ++this.operationGeneration;
    this.clearAdmissionExpiry();
    const previous = this.admission;
    const session = this.session;
    this.admission = null;
    this.admissionDeadlineMs = 0;
    this.stopLocalTracks();
    await this.destroySession(session);
    if (this.operationGeneration !== operationGeneration) return;
    this.confirmedSubscribersByPublication.clear();
    this.emit('idle', previous, reasonCode);
  }

  ngOnDestroy(): void {
    void this.disconnect('sfu_service_destroyed');
    this.subscriberConfirmed$.complete();
    this.remoteTrack$.complete();
    this.remoteVideo$.complete();
    this.remoteVideoUnavailable$.complete();
    this.remoteVideoState$.complete();
    this.opaqueData$.complete();
    this.subscriberLost$.complete();
    this.state$.complete();
  }

  private applyAudiencePermissions(): void {
    if (!this.session || !this.admission) return;
    const permissions = new Map<string, readonly string[]>();
    for (const [publicationId, publication] of this.published) {
      const grant = this.admission.publications.find(value => value.publication_id === publicationId);
      if (grant) permissions.set(publication.trackSid, grant.authorized_subscriber_ids);
    }
    this.session.publications.setTrackAudience(permissions);
  }

  private stopLocalTracks(): void {
    for (const publication of this.published.values()) safeStop(publication.track);
    this.published.clear();
    this.confirmedSubscribersByPublication.clear();
  }

  private destroySession(session: SfuRoomSession | null = this.session): Promise<void> {
    if (!session) return Promise.resolve();
    const existing = this.destructionBySession.get(session);
    if (existing) return existing;
    const destruction = this.destroySessionInOrder(session);
    this.destructionBySession.set(session, destruction);
    return destruction;
  }

  private async destroySessionInOrder(session: SfuRoomSession): Promise<void> {
    const ownsCurrentSession = this.session === session;
    const cleanup = this.cleanupBySession.get(session) ?? [];
    this.cleanupBySession.delete(session);
    if (ownsCurrentSession) this.session = null;
    for (const release of cleanup.reverse()) {
      try { release(); } catch { /* isolate listener cleanup */ }
    }
    try {
      if (ownsCurrentSession) {
        await this.publicationController.stopAll(session.publications).catch(() => undefined);
      }
    } finally {
      try { session.videoRender.clear(); } catch { /* deterministic cleanup */ }
      await session.lifecycle.destroy().catch(() => undefined);
    }
  }

  private assertOperationCurrent(operationGeneration: number): void {
    if (this.operationGeneration !== operationGeneration) throw new Error('sfu_connect_fenced');
  }

  private assertCurrentSession(operationGeneration: number, session: SfuRoomSession): void {
    if (!this.isCurrentSession(operationGeneration, session)) throw new Error('sfu_connect_fenced');
  }

  private isCurrentSession(operationGeneration: number, session: SfuRoomSession): boolean {
    return this.operationGeneration === operationGeneration && this.session === session;
  }

  private admissionUsable(): boolean {
    if (this.admission && this.admissionDeadlineMs > Date.now()) return true;
    if (this.admission) void this.disconnect('sfu_admission_expired');
    return false;
  }

  private armAdmissionExpiry(
    operationGeneration: number,
    admission: Readonly<SfuClientAdmission>,
    deadlineMs: number,
  ): void {
    this.clearAdmissionExpiry();
    this.admissionExpiryTimer = globalThis.setTimeout(() => {
      this.admissionExpiryTimer = null;
      if (this.operationGeneration === operationGeneration && this.admission === admission) {
        void this.disconnect('sfu_admission_expired');
      }
    }, Math.max(0, deadlineMs - Date.now()));
  }

  private clearAdmissionExpiry(): void {
    if (this.admissionExpiryTimer !== null) globalThis.clearTimeout(this.admissionExpiryTimer);
    this.admissionExpiryTimer = null;
  }

  private emit(
    status: SfuTransportView['status'],
    admission: Readonly<SfuClientAdmission> | null,
    reasonCode: string,
  ): void {
    this.state$.next(Object.freeze({
      status,
      roomId: admission?.room_id ?? null,
      membershipEpoch: admission?.membership_epoch ?? null,
      reasonCode,
      publicationCount: this.published.size,
    }));
  }
}

function validateAdmission(
  value: Readonly<SfuClientAdmission>,
  key: Uint8Array,
  nowSeconds: number,
  allowInsecureLocalhost: boolean,
): void {
  if (!isAllowedSfuEndpoint(value.server_url, 'websocket', allowInsecureLocalhost)) {
    throw new Error('sfu_url_invalid');
  }
  if (value.layer_control_mode !== undefined
      && !['adaptive_stream', 'manual_quality'].includes(value.layer_control_mode)) {
    throw new Error('sfu_layer_control_mode_invalid');
  }
  if (!value.access_token || !/^sfu-[a-f0-9]{32}$/.test(value.room_id)
      || !Number.isSafeInteger(value.membership_epoch) || value.membership_epoch < 1
      || !Number.isSafeInteger(value.expires_at) || value.expires_at <= nowSeconds
      || value.expires_at > nowSeconds + 60 || key.byteLength !== 32) {
    throw new Error('sfu_admission_invalid');
  }
  const ids = new Set<string>();
  for (const grant of value.publications) {
    if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(grant.publication_id)
        || ids.has(grant.publication_id)) {
      throw new Error('sfu_publication_grant_invalid');
    }
    ids.add(grant.publication_id);
    if (grant.privacy === 'private_recovery') continue;
    if (grant.kind !== (grant.source === 'microphone' ? 'audio' : 'video')) {
      throw new Error('sfu_publication_grant_invalid');
    }
  }
}

function resolveAdmissionDeadlineMs(expiresAtSeconds: number, validatedNowSeconds: number): number {
  const wallNowMs = Date.now();
  const wallNowSeconds = Math.trunc(wallNowMs / 1000);
  return Math.abs(wallNowSeconds - validatedNowSeconds) <= 1
    ? expiresAtSeconds * 1000
    : wallNowMs + ((expiresAtSeconds - validatedNowSeconds) * 1000);
}

function safeStop(track: MediaStreamTrack): void {
  try { track.stop(); } catch { /* deterministic cleanup */ }
}
