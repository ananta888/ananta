/**
 * T05 / T07 / T12: PairViewSyncService — sends and projects view-sync.
 *
 * One service, three jobs:
 *  1. Subscribe to SharedViewStateService and emit minimal deltas
 *     (debounced, rate-limited) over WebrtcTransportService.
 *  2. Subscribe to WebrtcTransportService.message$ and project validated,
 *     authenticated peer envelopes into separate read models and cursors.
 *  3. Enforce control default-deny: a control permission must be
 *     explicitly granted in the active session before any control
 *     message is acted on. Grant tokens are session-scoped and
 *     never persisted.
 *
 * T12 invariant: control grant never follows from view_tui or cursor. Until an
 * explicit approval workflow exists, inbound control requests fail closed.
 */
import { Injectable, OnDestroy, inject } from '@angular/core';
import { BehaviorSubject, Subject, Subscription } from 'rxjs';

import { WebrtcTransportService } from './webrtc-transport.service';
import { SharedViewStateService } from './shared-view-state.service';
import { ViewDeltaService } from './view-delta.service';
import { ShareSessionService } from './share-session.service';
import { hasPermission } from './permission-labels';
import { PAIR_VIEW_CRYPTO, PairViewCryptoPort } from './pair-view-crypto.service';
import { PairSecureSequenceService } from './pair-secure-sequence.service';
import {
  ControlMessage,
  CursorPos,
  PermissionKey,
  RelayEnvelope,
  RemoteViewProjection,
  SharedViewState,
  ViewStateDelta,
} from './pair-view-sync.types';
import {
  isControlMessage,
  isCursorPos,
  isViewStateDelta,
  MAX_DATACHANNEL_BYTES,
  MAX_ENCRYPTED_PAYLOAD_BYTES,
  SNAPSHOT_WARN_BYTES,
} from './pair-view-sync.validators';
import { PairSyncStats, PairSyncStatsRecorder } from './pair-view-sync-stats';
import { PairControlGrantState, isSnapshotRequestFor } from './pair-view-sync-control';
import { CursorMessage, PairPeerCursorPresence, PeerCursor, parseCursorMessage } from './pair-view-sync-peer-cursors';
import {
  cloneViewState,
  deltaSetsArtifactReference,
  emptyRemoteViewBase,
  newPairMessageId,
  projectOutgoingViewState,
  redactRemoteArtifacts,
} from './pair-view-sync-projection';

export type { PairSyncStats } from './pair-view-sync-stats';
export type { CursorMessage, PeerCursor } from './pair-view-sync-peer-cursors';

const VIEW_DELTA_DEBOUNCE_MS = 80;
const MAX_DELTAS_PER_SECOND = 5;
const CURSOR_INTERVAL_MS = 50;
const SNAPSHOT_RESPONSE_INTERVAL_MS = 500;
const SNAPSHOT_REQUEST_COOLDOWN_MS = 500;
const SNAPSHOT_REQUEST_TIMEOUT_MS = 5000;

@Injectable({ providedIn: 'root' })
export class PairViewSyncService implements OnDestroy {
  private transport = inject(WebrtcTransportService);
  private view = inject(SharedViewStateService);
  private delta = inject(ViewDeltaService);
  private share = inject(ShareSessionService);
  private cryptoPort: PairViewCryptoPort = inject(PAIR_VIEW_CRYPTO);
  private secureSequences = inject(PairSecureSequenceService);

  private readonly _followMode$ = new Subject<'active' | 'paused'>();
  readonly followMode$ = this._followMode$.asObservable();

  private readonly statsRecorder = new PairSyncStatsRecorder();
  readonly stats$ = this.statsRecorder.stats$;

  // ── Peer cursor presence (T10 overlay read model) ─────────────────
  private readonly peerCursorPresence = new PairPeerCursorPresence();
  readonly peerCursors$ = this.peerCursorPresence.peerCursors$;
  private readonly _remoteViews$ = new BehaviorSubject<ReadonlyMap<string, RemoteViewProjection>>(new Map());
  readonly remoteViews$ = this._remoteViews$.asObservable();
  private readonly _localCompactSharing$ = new BehaviorSubject<Readonly<{
    view: boolean;
    cursor: boolean;
    pending: boolean;
  }>>(
    Object.freeze({ view: false, cursor: false, pending: false }),
  );
  readonly localCompactSharing$ = this._localCompactSharing$.asObservable();
  /** Cursor-overlay rendering is on by default. Toggle via setCursorOverlayEnabled. */
  get cursorOverlayEnabled(): boolean { return this.peerCursorPresence.cursorOverlayEnabled; }
  setCursorOverlayEnabled(enabled: boolean): void {
    this.peerCursorPresence.setCursorOverlayEnabled(enabled);
  }

  /** Live counters; kept as a property seam for diagnostics and specs. */
  private get stats(): PairSyncStats { return this.statsRecorder.counters; }

  /** Active session; set via bindSession() / cleared in unbind(). */
  private sessionId = '';
  private ownerUserId = '';
  private active: boolean = false;
  private followMode: 'active' | 'paused' = 'paused';
  private readonly controlGrant = new PairControlGrantState();
  private securityEpoch = 0;

  // ── Throttle state ────────────────────────────────────────────────
  private deltaTimestamps: number[] = [];
  private rateLimitedState: SharedViewState | null = null;
  private rateLimitRetryHandle: ReturnType<typeof setTimeout> | null = null;
  private lastSeqSent = 0;
  private lastSentState: SharedViewState | null = null;
  private snapshotHashInFlight = '';
  private lastSnapshotHashSent = '';
  private viewSendInFlight = false;
  private pendingViewState: SharedViewState | null = null;
  private pendingViewForceSnapshot = false;
  private viewSendGeneration = 0;
  private readonly pendingSnapshotRequests = new Map<string, { requestedAt: number; expiresAt: number }>();
  private permissionsSignature = '';
  private cursorSendInFlight = false;
  private pendingCursorMessage: CursorMessage | null = null;
  private cursorSendGeneration = 0;
  private lastCursorDispatchAt = 0;
  private cursorDispatchHandle: ReturnType<typeof setTimeout> | null = null;
  private lastSnapshotResponseAt = 0;
  private localCursorSharingEnabled = false;
  private localViewSharingEnabled = false;
  private pendingFirstReadyCompactSessionId = '';
  private pendingFirstReadyCompactOwnerUserId = '';

  // ── Subscriptions ─────────────────────────────────────────────────
  private viewSub: Subscription | null = null;
  private msgSub: Subscription | null = null;
  private viewTransportStateSub: Subscription | null = null;
  private lastReadyTransportContext = '';
  private debounceHandle: ReturnType<typeof setTimeout> | null = null;

  bindSession(sessionId: string, ownerUserId: string, securityEpoch = 0): void {
    this.unbindSession();
    this.sessionId = sessionId;
    this.ownerUserId = ownerUserId;
    this.active = true;
    this.followMode = 'paused';
    this.controlGrant.reset();
    this.clearLocalViewSendState();
    this.permissionsSignature = '';
    this.cancelCursorSend();
    this.lastSnapshotResponseAt = 0;
    this.localCursorSharingEnabled = false;
    this.localViewSharingEnabled = false;
    this.pendingFirstReadyCompactSessionId = '';
    this.pendingFirstReadyCompactOwnerUserId = '';
    this.lastReadyTransportContext = '';
    this.publishLocalCompactSharing();
    this.securityEpoch = Number.isSafeInteger(securityEpoch) && securityEpoch > 0 ? securityEpoch : 0;
    this.peerCursorPresence.clearSilently();
    this._remoteViews$.next(new Map());
    this.peerCursorPresence.startReaping();
    this.view.bindToSession(sessionId, ownerUserId);
    this.subscribeToView();
    this.subscribeToTransport();
  }

  unbindSession(): void {
    const previousSessionId = this.sessionId;
    this.active = false;
    this.sessionId = '';
    this.ownerUserId = '';
    this.securityEpoch = 0;
    if (previousSessionId) {
      this.secureSequences.clearScope(previousSessionId);
      this.cryptoPort.clear(previousSessionId);
    }
    this.view.unbindFromSession();
    this.peerCursorPresence.stopReaping();
    this.peerCursorPresence.clearAndPublish();
    this._remoteViews$.next(new Map());
    // Session-bound send state: debounce/rate-limit timers, in-flight
    // snapshot/delta fences, snapshot requests and the cursor pipeline.
    this.clearLocalViewSendState();
    this.permissionsSignature = '';
    this.cancelCursorSend();
    this.lastSnapshotResponseAt = 0;
    this.localCursorSharingEnabled = false;
    this.localViewSharingEnabled = false;
    this.pendingFirstReadyCompactSessionId = '';
    this.pendingFirstReadyCompactOwnerUserId = '';
    this.publishLocalCompactSharing();
    this.controlGrant.cancelRequest();
    this.viewSub?.unsubscribe();
    this.viewSub = null;
    this.msgSub?.unsubscribe();
    this.msgSub = null;
    this.viewTransportStateSub?.unsubscribe();
    this.viewTransportStateSub = null;
    this.lastReadyTransportContext = '';
  }

  setFollowMode(mode: 'active' | 'paused'): void {
    this.followMode = mode;
    this._followMode$.next(mode);
  }

  getFollowMode(): 'active' | 'paused' {
    return this.followMode;
  }

  /** Returns true when a control grant is currently active. */
  hasControlGrant(): boolean {
    return this.controlGrant.hasGrant;
  }

  get isLocalViewSharingEnabled(): boolean { return this.localViewSharingEnabled; }
  get isLocalCursorSharingEnabled(): boolean { return this.localCursorSharingEnabled; }
  get isLocalCompactSharingPending(): boolean {
    return this.active
      && this.pendingFirstReadyCompactSessionId === this.sessionId
      && this.pendingFirstReadyCompactOwnerUserId === this.ownerUserId
      && this.currentShareUserId() === this.pendingFirstReadyCompactOwnerUserId;
  }

  /**
   * One-shot creator intent for the first confirmed peer epoch.
   *
   * Creating a Public Pair starts at an epoch that changes as soon as the
   * first peer joins. The click is therefore retained only as a RAM-local,
   * exact-session intent and becomes effective once for the first confirmed
   * peer binding. It is never carried into a later rekey.
   */
  armLocalCompactSharingOnFirstPeerReady(sessionId: string): boolean {
    if (
      !this.active
      || sessionId !== this.sessionId
      || this.share.state$.value.session?.id !== sessionId
      || this.share.state$.value.role !== 'owner'
      || this.currentShareUserId() !== this.ownerUserId
    ) return false;
    this.pendingFirstReadyCompactSessionId = sessionId;
    this.pendingFirstReadyCompactOwnerUserId = this.ownerUserId;
    this.publishLocalCompactSharing();
    this.consumePendingFirstReadyCompactSharing();
    return true;
  }

  cancelPendingLocalCompactSharing(sessionId: string): boolean {
    if (!this.active || sessionId !== this.pendingFirstReadyCompactSessionId) return false;
    this.pendingFirstReadyCompactSessionId = '';
    this.pendingFirstReadyCompactOwnerUserId = '';
    this.publishLocalCompactSharing();
    return true;
  }

  /** Exact-session/epoch consent; session permissions alone never share local UI. */
  setLocalCompactSharing(
    sessionId: string,
    securityEpoch: number,
    selection: Readonly<{ view: boolean; cursor: boolean }>,
  ): boolean {
    if (
      !this.active
      || sessionId !== this.sessionId
      || securityEpoch !== this.securityEpoch
      || securityEpoch < 1
    ) return false;
    this.pendingFirstReadyCompactSessionId = '';
    this.pendingFirstReadyCompactOwnerUserId = '';
    const nextView = selection.view && hasPermission(this.share.currentPermissions(), 'view_tui');
    const nextCursor = selection.cursor && hasPermission(this.share.currentPermissions(), 'remote_cursor');
    const startView = nextView && !this.localViewSharingEnabled;
    this.localViewSharingEnabled = nextView;
    this.localCursorSharingEnabled = nextCursor;
    if (!nextCursor) this.cancelCursorSend();
    if (!nextView) this.clearLocalViewSendState();
    if (startView) {
      this.clearLocalViewSendState();
      this.lastSeqSent = this.view.current.seq;
      this.sendSnapshot(this.view.current);
    }
    // Publish only after generation fences and the initial snapshot state are
    // installed. Subscribers may synchronously capture viewport state.
    this.publishLocalCompactSharing();
    return true;
  }

  /** Resume the initial snapshot only after signed peer-key confirmation. */
  onCryptoReady(): void {
    if (!this.active || !this.cryptoPort.ready(this.sessionId, this.securityEpoch)) return;
    const activated = this.consumePendingFirstReadyCompactSharing();
    if (!activated && this.localViewSharingEnabled) this.sendSnapshot(this.view.current);
  }

  updateSecurityEpoch(epoch: number): void {
    if (!Number.isSafeInteger(epoch) || epoch < 1 || epoch === this.securityEpoch) return;
    this.securityEpoch = epoch;
    this.resetForSecurityEpochChange();
  }

  /**
   * Public: ask the peer owner for a control grant. The
   * actual handshake is routed over the transport; the
   * service tracks the request state. Per T12, the request
   * is dropped server-side if the session does not have
   * the `control` permission granted.
   */
  requestControl(): void {
    if (!this.active || !this.sessionId) return;
    this.controlGrant.markRequested();
    const request: ControlMessage = {
      sessionId: this.sessionId,
      senderUserId: this.ownerUserId,
      kind: 'request',
      grantToken: null,
      createdAt: Date.now(),
    };
    void this.sendSecurePayload(
      'control', request, 'pair.control', 'control', 'remote_control',
    ).then(sent => {
      if (!sent) this.controlGrant.cancelRequest();
    });
  }

  // ── Outgoing: state -> transport ──────────────────────────────────

  private subscribeToView(): void {
    this.viewSub = this.view.state$.subscribe((state) => {
      if (!this.active || !this.localViewSharingEnabled) return;
      // If the seq didn't move (the state service short-circuited
      // an unchanged state), there is nothing to send.
      if (state.seq === this.lastSeqSent) return;
      const outgoing = this.outgoingProjection(state);
      // Snapshot on first emission after bind, or on major changes
      if (this.lastSeqSent === 0) {
        this.sendSnapshot(outgoing);
      } else {
        this.scheduleDelta(outgoing);
      }
      this.lastSeqSent = state.seq;
    });
  }

  private scheduleDelta(state: SharedViewState): void {
    if (this.debounceHandle !== null) clearTimeout(this.debounceHandle);
    this.debounceHandle = setTimeout(() => {
      this.debounceHandle = null;
      this.maybeSendDelta(state);
    }, VIEW_DELTA_DEBOUNCE_MS);
  }

  private maybeSendDelta(state: SharedViewState): void {
    if (this.viewSendInFlight) {
      this.pendingViewState = cloneViewState(state);
      return;
    }
    if (this.isRateLimited()) {
      this.rateLimitedState = cloneViewState(state);
      if (this.rateLimitRetryHandle === null) {
        const oldest = this.deltaTimestamps[0] ?? Date.now();
        this.rateLimitRetryHandle = setTimeout(() => {
          this.rateLimitRetryHandle = null;
          const pending = this.rateLimitedState;
          this.rateLimitedState = null;
          if (pending && this.active) this.maybeSendDelta(pending);
        }, Math.max(1, 1000 - (Date.now() - oldest)));
      }
      return;
    }
    const prev = this.lastSentState;
    if (!prev) {
      this.sendSnapshot(state);
      return;
    }
    const delta = this.delta.createDelta(prev, state);
    if (delta.ops.length === 0 && delta.payload == null) return;
    this.sendDeltaEnvelope(delta, state);
  }

  private isRateLimited(): boolean {
    const now = Date.now();
    this.deltaTimestamps = this.deltaTimestamps.filter((t) => now - t < 1000);
    if (this.deltaTimestamps.length >= MAX_DELTAS_PER_SECOND) return true;
    this.deltaTimestamps.push(now);
    return false;
  }

  private sendSnapshot(state: SharedViewState, force = false): void {
    const outgoing = this.outgoingProjection(state);
    if (this.viewSendInFlight) {
      this.pendingViewState = cloneViewState(outgoing);
      this.pendingViewForceSnapshot = this.pendingViewForceSnapshot || force || !this.lastSentState;
      return;
    }
    if (!force && (outgoing.viewHash === this.snapshotHashInFlight || outgoing.viewHash === this.lastSnapshotHashSent)) return;
    const delta = this.delta.createSnapshot(outgoing);
    this.snapshotHashInFlight = outgoing.viewHash;
    this.sendDeltaEnvelope(delta, outgoing);
  }

  private sendDeltaEnvelope(delta: ViewStateDelta, sentState: SharedViewState): void {
    const context = this.captureSecurityContext();
    if (!context || !this.localViewSharingEnabled || this.viewSendInFlight) return;
    const generation = this.viewSendGeneration;
    let accepted = false;
    this.viewSendInFlight = true;
    void this.toRelayEnvelope(delta, context).then((envelope) => {
      if (
        generation !== this.viewSendGeneration
        || !envelope
        || !this.matchesSecurityContext(context)
        || !this.localViewSharingEnabled
        || !hasPermission(this.share.currentPermissions(), 'view_tui')
      ) {
        this.clearSnapshotInFlight(delta);
        return;
      }
      accepted = this.transport.sendView(envelope);
      if (!accepted) {
        this.clearSnapshotInFlight(delta);
        this.pendingViewState = cloneViewState(sentState);
        this.pendingViewForceSnapshot = true;
        return;
      }
      if (delta.kind === 'snapshot') {
        this.snapshotHashInFlight = '';
        this.lastSnapshotHashSent = delta.newHash;
        this.statsRecorder.increment('snapshotsSent');
      }
      if (!this.lastSentState || sentState.seq >= this.lastSentState.seq) {
        this.lastSentState = cloneViewState(sentState);
      }
      if (delta.kind === 'snapshot') return;
      this.statsRecorder.increment(delta.kind === 'cursor' ? 'cursorsSent' : 'deltasSent');
    }).catch(() => {
      this.clearSnapshotInFlight(delta);
      this.rejectApply();
    }).finally(() => {
      if (generation !== this.viewSendGeneration) return;
      this.viewSendInFlight = false;
      if (accepted) this.flushPendingViewState();
    });
  }

  private async toRelayEnvelope(
    delta: ViewStateDelta,
    context: Readonly<{ sessionId: string; securityEpoch: number; ownerUserId: string }>,
  ): Promise<RelayEnvelope | null> {
    if (!hasPermission(this.share.currentPermissions(), 'view_tui')) {
      // T06/T11: backend rejects payloads without view_tui. Skip silently.
      return null;
    }
    if (
      !this.matchesSecurityContext(context)
      || !this.localViewSharingEnabled
      || !hasPermission(this.share.currentPermissions(), 'view_tui')
      || !this.cryptoPort.ready(context.sessionId, context.securityEpoch)
    ) return null;
    const sequence = await this.secureSequences.next(
      context.sessionId, context.securityEpoch, context.ownerUserId, 'semantic',
    );
    if (
      !this.matchesSecurityContext(context)
      || !this.localViewSharingEnabled
      || !hasPermission(this.share.currentPermissions(), 'view_tui')
    ) return null;
    const encrypted = await this.cryptoPort.seal(JSON.stringify(delta), {
      scopeId: context.sessionId,
      epoch: context.securityEpoch,
      sequence,
      payloadType: 'pair.view_delta',
      trafficClass: 'semantic',
    });
    if (
      !this.matchesSecurityContext(context)
      || !this.localViewSharingEnabled
      || !hasPermission(this.share.currentPermissions(), 'view_tui')
    ) return null;
    if (encrypted.length > Math.min(MAX_ENCRYPTED_PAYLOAD_BYTES, MAX_DATACHANNEL_BYTES)) return null;
    if (delta.kind === 'snapshot' && encrypted.length > SNAPSHOT_WARN_BYTES) {
      // Soft warning: snapshots over 32 KB are flagged but still sent.
    }
    return {
      message_id: newPairMessageId(),
      kind: delta.kind,
      base_hash: delta.baseHash,
      new_hash: delta.newHash,
      width: 0,
      height: 0,
      encrypted_payload: encrypted,
    };
  }

  // ── Incoming: transport -> state ──────────────────────────────────

  private subscribeToTransport(): void {
    this.msgSub = this.transport.message$.subscribe((msg) => {
      if (!this.active) return;
      if (msg.type === 'view_payload') {
        void this.handleIncomingEncrypted(msg.payload);
        return;
      }
    });
    this.viewTransportStateSub = this.transport.viewTransportState$.subscribe(state => {
      if (
        !this.active
        || !state.ready
        || state.sessionId !== this.sessionId
        || state.semanticEpoch !== this.securityEpoch
      ) return;
      const context = `${state.sessionId}:${state.semanticEpoch}:${state.generation}`;
      if (context === this.lastReadyTransportContext) return;
      this.lastReadyTransportContext = context;
      if (
        !this.localViewSharingEnabled
        || !hasPermission(this.share.currentPermissions(), 'view_tui')
        || !this.cryptoPort.ready(this.sessionId, this.securityEpoch)
      ) return;
      // A crypto-ready callback may precede the DataChannel open event. Drop
      // any state retained by that rejected dispatch and retry the latest
      // compact projection exactly once for this transport generation.
      this.pendingViewState = null;
      this.pendingViewForceSnapshot = false;
      this.sendSnapshot(this.view.current, true);
    });
  }

  private async handleIncomingEncrypted(raw: unknown): Promise<void> {
    if (!raw || typeof raw !== 'object') {
      this.rejectApply();
      return;
    }
    const envelope = raw as { encrypted_payload?: string };
    if (
      typeof envelope.encrypted_payload !== 'string'
      || new TextEncoder().encode(envelope.encrypted_payload).byteLength > MAX_DATACHANNEL_BYTES
    ) {
      this.rejectApply();
      return;
    }
    if (!this.cryptoPort.ready(this.sessionId, this.securityEpoch)) {
      this.rejectApply();
      return;
    }
    const expectedSessionId = this.sessionId;
    const expectedEpoch = this.securityEpoch;
    let opened;
    try {
      opened = await this.cryptoPort.open(envelope.encrypted_payload, {
        scopeId: this.sessionId, epoch: this.securityEpoch,
      });
    } catch {
      this.rejectApply();
      return;
    }
    if (!this.active || this.sessionId !== expectedSessionId || this.securityEpoch !== expectedEpoch) {
      this.rejectApply();
      return;
    }
    if (opened.payloadType === 'pair.view_delta') {
      this.applyIncomingView(opened.plaintext, opened.senderId);
      return;
    }
    if (opened.payloadType === 'pair.cursor') {
      this.applyIncomingCursor(opened.plaintext, opened.senderId);
      return;
    }
    if (opened.payloadType === 'pair.control') {
      this.applyIncomingControl(opened.plaintext, opened.senderId);
      return;
    }
    if (opened.payloadType === 'pair.snapshot_request') {
      if (!hasPermission(this.share.currentPermissions(), 'view_tui')) { this.rejectApply(); return; }
      if (!this.localViewSharingEnabled) { this.rejectApply(); return; }
      if (!isSnapshotRequestFor(opened.plaintext, this.sessionId)) { this.rejectApply(); return; }
      this.statsRecorder.increment('snapshotRequestsReceived');
      const now = Date.now();
      if (now - this.lastSnapshotResponseAt >= SNAPSHOT_RESPONSE_INTERVAL_MS) {
        this.lastSnapshotResponseAt = now;
        this.sendSnapshot(this.view.current, true);
      }
      return;
    }
    if (opened.payloadType === 'pair.artifact_ref') {
      if (!hasPermission(this.share.currentPermissions(), 'artifact_share')) this.rejectApply();
      return;
    }
    this.rejectApply();
  }

  private applyIncomingView(plain: string, authenticatedSenderId: string): void {
    let parsed: unknown;
    try { parsed = JSON.parse(plain); } catch {
      this.rejectApply();
      return;
    }
    if (!isViewStateDelta(parsed)) {
      this.rejectApply();
      return;
    }
    const delta = parsed;
    if (delta.sessionId !== this.sessionId || delta.senderUserId !== authenticatedSenderId) {
      this.rejectApply();
      return;
    }
    const perms = this.share.currentPermissions();
    if (!hasPermission(perms, 'view_tui')) {
      this.rejectApply();
      return;
    }
    if (
      !hasPermission(perms, 'artifact_share')
      && deltaSetsArtifactReference(delta)
    ) {
      this.rejectApply();
      return;
    }
    const currentProjection = this._remoteViews$.value.get(authenticatedSenderId)?.state ?? null;
    if (currentProjection && delta.seq <= currentProjection.seq) {
      // AEAD/replay validation authenticates each envelope but deliberately
      // permits a bounded out-of-order network window. The read model itself
      // is monotonic per authenticated sender, including full snapshots.
      this.rejectApply();
      return;
    }
    if (
      delta.kind !== 'snapshot'
      && (!currentProjection || this.delta.requiresSnapshotRequest(delta, currentProjection))
    ) {
      this.requestSnapshot(authenticatedSenderId);
      this.rejectApply();
      return;
    }
    const base = currentProjection ?? emptyRemoteViewBase(delta, this.sessionId, authenticatedSenderId);
    const next = this.delta.applyDelta(base, delta);
    if (this.hashOf(next) !== delta.newHash) {
      this.rejectApply();
      return;
    }
    const projections = new Map(this._remoteViews$.value);
    projections.set(authenticatedSenderId, Object.freeze({
      senderUserId: authenticatedSenderId,
      state: Object.freeze({ ...next }),
      receivedAt: Date.now(),
    }));
    this._remoteViews$.next(projections);
    if (delta.kind === 'snapshot') this.clearSnapshotRequests(authenticatedSenderId);
    this.statsRecorder.increment('appliesAccepted');
  }

  private applyIncomingCursor(plain: string, authenticatedSenderId: string): void {
    // T10: peer-cursor delivery is OUT-OF-BAND. The cursor is
    // rendered as a presence overlay, never written back to
    // local SharedViewState — that would cause a feedback loop
    // (own cursor → view.cursor → send delta → own cursor).
    const message = parseCursorMessage(plain);
    if (!message) {
      this.rejectApply();
      return;
    }
    if (message.sessionId !== this.sessionId || message.senderUserId !== authenticatedSenderId) {
      this.rejectApply();
      return;
    }
    if (!this.share.currentPermissions() || !hasPermission(this.share.currentPermissions(), 'remote_cursor')) {
      this.rejectApply();
      return;
    }
    this.peerCursorPresence.upsert(message);
    this.statsRecorder.increment('cursorsReceived');
  }

  /**
   * Public: send a remote-cursor presence event. Routed over the
   * transport as a dedicated `cursor` message; receivers update
   * their peer-cursor map but DO NOT apply to local state.
   * Cursors are dropped unless the session has the `remote_cursor`
   * permission granted.
   */
  sendCursor(userLabel: string, cursor: CursorPos): void {
    if (!this.active || !this.sessionId) return;
    if (!this.localCursorSharingEnabled) return;
    if (!this.share.currentPermissions() || !hasPermission(this.share.currentPermissions(), 'remote_cursor')) return;
    const safeLabel = userLabel.trim().slice(0, 32);
    if (!safeLabel || !isCursorPos(cursor)) return;
    this.pendingCursorMessage = {
      sessionId: this.sessionId,
      senderUserId: this.ownerUserId,
      userLabel: safeLabel,
      cursor,
      sentAt: Date.now(),
    };
    this.flushPendingCursor();
  }

  sendArtifactReference(reference: { artifactId: string; artifactHash: string }): void {
    if (!hasPermission(this.share.currentPermissions(), 'artifact_share')) return;
    void this.sendSecurePayload('selection', reference, 'pair.artifact_ref', 'semantic', 'artifact_share');
  }

  private applyIncomingControl(plain: string, authenticatedSenderId: string): void {
    let raw: unknown;
    try { raw = JSON.parse(plain); } catch { this.rejectApply(); return; }
    if (!isControlMessage(raw) || raw.senderUserId !== authenticatedSenderId) {
      this.rejectApply();
      return;
    }
    this.handleIncomingControl(raw);
  }

  private handleIncomingControl(raw: unknown): void {
    const outcome = this.controlGrant.accept(
      raw, this.sessionId, hasPermission(this.share.currentPermissions(), 'remote_control'),
    );
    if (outcome === 'rejected') this.rejectApply();
    else if (outcome === 'denied') this.statsRecorder.increment('controlDenied');
    else if (outcome === 'granted') this.statsRecorder.increment('controlGranted');
    else if (outcome === 'revoked') this.statsRecorder.increment('controlRevoked');
  }

  // ── Helpers ────────────────────────────────────────────────────────

  private requestSnapshot(authenticatedSenderId: string): void {
    if (!this.active) return;
    const now = Date.now();
    for (const [key, pending] of this.pendingSnapshotRequests) {
      if (pending.expiresAt <= now) this.pendingSnapshotRequests.delete(key);
    }
    const key = `${this.sessionId}:${this.securityEpoch}:${authenticatedSenderId}`;
    const pending = this.pendingSnapshotRequests.get(key);
    if (pending && now - pending.requestedAt < SNAPSHOT_REQUEST_COOLDOWN_MS) return;
    if (pending && pending.expiresAt > now) return;
    this.pendingSnapshotRequests.set(key, {
      requestedAt: now,
      expiresAt: now + SNAPSHOT_REQUEST_TIMEOUT_MS,
    });
    void this.sendSecurePayload(
      'control', { sessionId: this.sessionId }, 'pair.snapshot_request', 'control', 'view_tui',
    ).then(sent => {
      if (!sent) {
        this.pendingSnapshotRequests.delete(key);
        return;
      }
      this.statsRecorder.increment('snapshotRequestsSent');
    });
  }

  private async sendSecurePayload(
    kind: RelayEnvelope['kind'],
    payload: unknown,
    payloadType: string,
    trafficClass: 'control' | 'semantic',
    requiredPermission: PermissionKey,
    localGenerationIsCurrent: () => boolean = () => true,
  ): Promise<boolean> {
    const context = this.captureSecurityContext();
    if (
      !context
      || !localGenerationIsCurrent()
      || !hasPermission(this.share.currentPermissions(), requiredPermission)
      || !this.cryptoPort.ready(context.sessionId, context.securityEpoch)
    ) return false;
    try {
      const sequence = await this.secureSequences.next(
        context.sessionId, context.securityEpoch, context.ownerUserId, trafficClass,
      );
      if (
        !this.matchesSecurityContext(context)
        || !localGenerationIsCurrent()
        || !hasPermission(this.share.currentPermissions(), requiredPermission)
      ) return false;
      const encrypted = await this.cryptoPort.seal(JSON.stringify(payload), {
        scopeId: context.sessionId,
        epoch: context.securityEpoch,
        sequence,
        payloadType,
        trafficClass,
      });
      if (
        !this.matchesSecurityContext(context)
        || !localGenerationIsCurrent()
        || !hasPermission(this.share.currentPermissions(), requiredPermission)
        || encrypted.length > Math.min(MAX_ENCRYPTED_PAYLOAD_BYTES, MAX_DATACHANNEL_BYTES)
      ) return false;
      return this.transport.sendView({
        message_id: newPairMessageId(), kind, base_hash: '', new_hash: '', width: 0, height: 0,
        encrypted_payload: encrypted,
      });
    } catch {
      this.rejectApply();
      return false;
    }
  }

  private flushPendingCursor(): void {
    if (this.cursorSendInFlight || !this.pendingCursorMessage) return;
    const waitMs = CURSOR_INTERVAL_MS - (Date.now() - this.lastCursorDispatchAt);
    if (waitMs > 0) {
      if (this.cursorDispatchHandle === null) {
        this.cursorDispatchHandle = setTimeout(() => {
          this.cursorDispatchHandle = null;
          this.flushPendingCursor();
        }, waitMs);
      }
      return;
    }
    const message = this.pendingCursorMessage;
    this.pendingCursorMessage = null;
    this.cursorSendInFlight = true;
    this.lastCursorDispatchAt = Date.now();
    const generation = this.cursorSendGeneration;
    void this.sendSecurePayload(
      'cursor',
      message,
      'pair.cursor',
      'control',
      'remote_cursor',
      () => generation === this.cursorSendGeneration && this.localCursorSharingEnabled,
    ).then(sent => {
      if (sent) {
        this.statsRecorder.increment('cursorsSent');
      }
    }).finally(() => {
      if (generation !== this.cursorSendGeneration) return;
      this.cursorSendInFlight = false;
      if (this.pendingCursorMessage) this.flushPendingCursor();
    });
  }

  private captureSecurityContext(): Readonly<{ sessionId: string; securityEpoch: number; ownerUserId: string }> | null {
    if (!this.active || !this.sessionId || !this.ownerUserId || this.securityEpoch < 1) return null;
    return Object.freeze({
      sessionId: this.sessionId,
      securityEpoch: this.securityEpoch,
      ownerUserId: this.ownerUserId,
    });
  }

  private matchesSecurityContext(context: Readonly<{ sessionId: string; securityEpoch: number; ownerUserId: string }>): boolean {
    return this.active
      && this.sessionId === context.sessionId
      && this.securityEpoch === context.securityEpoch
      && this.ownerUserId === context.ownerUserId;
  }

  private clearSnapshotInFlight(delta: ViewStateDelta): void {
    if (
      delta.kind === 'snapshot'
      && delta.sessionId === this.sessionId
      && this.snapshotHashInFlight === delta.newHash
    ) this.snapshotHashInFlight = '';
  }

  private clearLocalViewSendState(): void {
    if (this.debounceHandle !== null) { clearTimeout(this.debounceHandle); this.debounceHandle = null; }
    if (this.rateLimitRetryHandle !== null) { clearTimeout(this.rateLimitRetryHandle); this.rateLimitRetryHandle = null; }
    this.rateLimitedState = null;
    this.deltaTimestamps = [];
    this.lastSeqSent = 0;
    this.lastSentState = null;
    this.snapshotHashInFlight = '';
    this.lastSnapshotHashSent = '';
    this.pendingViewState = null;
    this.pendingViewForceSnapshot = false;
    this.viewSendGeneration += 1;
    this.viewSendInFlight = false;
    this.pendingSnapshotRequests.clear();
  }

  private resetForSecurityEpochChange(): void {
    this.controlGrant.reset();
    this.followMode = 'paused';
    this._followMode$.next('paused');
    this.localViewSharingEnabled = false;
    this.localCursorSharingEnabled = false;
    this.publishLocalCompactSharing();
    this.clearLocalViewSendState();
    this.cancelCursorSend();
    this.lastSnapshotResponseAt = 0;
    this.peerCursorPresence.clearAndPublish();
    this._remoteViews$.next(new Map());
  }

  private consumePendingFirstReadyCompactSharing(): boolean {
    if (
      !this.active
      || this.pendingFirstReadyCompactSessionId !== this.sessionId
      || this.pendingFirstReadyCompactOwnerUserId !== this.ownerUserId
      || this.share.state$.value.session?.id !== this.sessionId
      || this.share.state$.value.role !== 'owner'
      || this.currentShareUserId() !== this.pendingFirstReadyCompactOwnerUserId
      || this.securityEpoch < 1
      || !this.cryptoPort.ready(this.sessionId, this.securityEpoch)
    ) return false;
    this.pendingFirstReadyCompactSessionId = '';
    this.pendingFirstReadyCompactOwnerUserId = '';
    return this.setLocalCompactSharing(this.sessionId, this.securityEpoch, {
      view: true,
      cursor: true,
    });
  }

  private cancelCursorSend(): void {
    this.pendingCursorMessage = null;
    this.cursorSendGeneration += 1;
    this.cursorSendInFlight = false;
    if (this.cursorDispatchHandle !== null) {
      clearTimeout(this.cursorDispatchHandle);
      this.cursorDispatchHandle = null;
    }
    this.lastCursorDispatchAt = 0;
  }

  private currentShareUserId(): string {
    try {
      return this.share.currentUserId;
    } catch {
      return '';
    }
  }

  private flushPendingViewState(): void {
    const state = this.pendingViewState;
    const forceSnapshot = this.pendingViewForceSnapshot;
    this.pendingViewState = null;
    this.pendingViewForceSnapshot = false;
    if (!state || !this.active || !this.localViewSharingEnabled) return;
    if (forceSnapshot || !this.lastSentState) {
      this.sendSnapshot(state, forceSnapshot);
      return;
    }
    this.maybeSendDelta(state);
  }

  private clearSnapshotRequests(authenticatedSenderId: string): void {
    this.pendingSnapshotRequests.delete(
      `${this.sessionId}:${this.securityEpoch}:${authenticatedSenderId}`,
    );
  }

  private rejectApply(): void {
    this.statsRecorder.increment('appliesRejected');
  }

  private outgoingProjection(state: SharedViewState): SharedViewState {
    const permissions = this.share.currentPermissions();
    const signature = JSON.stringify(permissions ?? null);
    if (signature !== this.permissionsSignature) {
      this.permissionsSignature = signature;
      this.lastSentState = null;
      this.lastSnapshotHashSent = '';
      this.clearForbiddenRemoteArtifacts(permissions);
    }
    const projected = projectOutgoingViewState(
      state, this.sessionId, this.ownerUserId, hasPermission(permissions, 'artifact_share'),
    );
    return { ...projected, viewHash: this.hashOf(projected) };
  }

  private clearForbiddenRemoteArtifacts(permissions: ReturnType<ShareSessionService['currentPermissions']>): void {
    if (hasPermission(permissions, 'artifact_share') || this._remoteViews$.value.size === 0) return;
    const redacted = redactRemoteArtifacts(this._remoteViews$.value);
    this._remoteViews$.next(redacted);
  }

  private publishLocalCompactSharing(): void {
    const current = this._localCompactSharing$.value;
    const pending = this.isLocalCompactSharingPending;
    if (
      current.view === this.localViewSharingEnabled
      && current.cursor === this.localCursorSharingEnabled
      && current.pending === pending
    ) return;
    this._localCompactSharing$.next(Object.freeze({
      view: this.localViewSharingEnabled,
      cursor: this.localCursorSharingEnabled,
      pending,
    }));
  }

  private hashOf(state: SharedViewState): string {
    const { viewHash: _viewHash, ...hashable } = state;
    return this.view.hashOf(hashable);
  }

  ngOnDestroy(): void {
    this.unbindSession();
  }
}
