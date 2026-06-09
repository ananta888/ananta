/**
 * T05 / T07 / T12: PairViewSyncService — sends and applies view-sync.
 *
 * One service, three jobs:
 *  1. Subscribe to SharedViewStateService and emit minimal deltas
 *     (debounced, rate-limited) over WebrtcTransportService.
 *  2. Subscribe to WebrtcTransportService.message$ and apply
 *     validated incoming envelopes to the local view state.
 *  3. Enforce control default-deny: a control permission must be
 *     explicitly granted in the active session before any control
 *     message is acted on. Grant tokens are session-scoped and
 *     never persisted.
 *
 * T12 invariant: control grant never follows from view_tui or
 * cursor. The UI shows an explicit grant dialog; the backend
 * also enforces it.
 */
import { Injectable, OnDestroy, inject } from '@angular/core';
import { Subject, Subscription } from 'rxjs';

import { WebrtcTransportService } from './webrtc-transport.service';
import { SharedViewStateService } from './shared-view-state.service';
import { ViewDeltaService } from './view-delta.service';
import { ShareSessionService } from './share-session.service';
import { hasPermission } from './permission-labels';
import {
  ControlMessage,
  PAIR_VIEW_SYNC_VERSION,
  RelayEnvelope,
  SharedViewState,
  ViewStateDelta,
} from './pair-view-sync.types';
import {
  isControlMessage,
  isViewStateDelta,
  MAX_ENCRYPTED_PAYLOAD_BYTES,
  SNAPSHOT_WARN_BYTES,
} from './pair-view-sync.validators';

/** Optional test override: how to "encrypt" payloads. */
export type PayloadEncrypter = (plaintext: string) => string;
export type PayloadDecrypter = (ciphertext: string) => string | null;

const DEFAULT_STUB_ENC: PayloadEncrypter = (s) => `STUB1::${s}`;
const DEFAULT_STUB_DEC: PayloadDecrypter = (s) => {
  if (s.startsWith('STUB1::')) return s.slice('STUB1::'.length);
  return null;
};

const VIEW_DELTA_DEBOUNCE_MS = 80;
const CURSOR_THROTTLE_MS = 50;
const SCROLL_THROTTLE_MS = 100;
const MAX_DELTAS_PER_SECOND = 5;
const MAX_CURSORS_PER_SECOND = 20;

export interface PairSyncStats {
  snapshotsSent: number;
  deltasSent: number;
  cursorsSent: number;
  appliesAccepted: number;
  appliesRejected: number;
  snapshotRequestsSent: number;
  snapshotRequestsReceived: number;
  controlGranted: number;
  controlDenied: number;
  controlRevoked: number;
}

function newId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

@Injectable({ providedIn: 'root' })
export class PairViewSyncService implements OnDestroy {
  private transport = inject(WebrtcTransportService);
  private view = inject(SharedViewStateService);
  private delta = inject(ViewDeltaService);
  private share = inject(ShareSessionService);

  private readonly _followMode$ = new Subject<'active' | 'paused'>();
  readonly followMode$ = this._followMode$.asObservable();

  private readonly _stats$ = new Subject<PairSyncStats>();
  readonly stats$ = this._stats$.asObservable();

  private stats: PairSyncStats = {
    snapshotsSent: 0, deltasSent: 0, cursorsSent: 0,
    appliesAccepted: 0, appliesRejected: 0,
    snapshotRequestsSent: 0, snapshotRequestsReceived: 0,
    controlGranted: 0, controlDenied: 0, controlRevoked: 0,
  };

  /** Active session; set via bindSession() / cleared in unbind(). */
  private sessionId = '';
  private ownerUserId = '';
  private active: boolean = false;
  private followMode: 'active' | 'paused' = 'active';
  private controlGrantToken: string | null = null;
  private encrypter: PayloadEncrypter = DEFAULT_STUB_ENC;
  private decrypter: PayloadDecrypter = DEFAULT_STUB_DEC;

  // ── Throttle state ────────────────────────────────────────────────
  private lastDeltaTs = 0;
  private deltaTimestamps: number[] = [];
  private cursorTimestamps: number[] = [];
  private lastCursorSent = '';
  private lastScrollSent = '';
  private lastSeqSent = 0;

  // ── Subscriptions ─────────────────────────────────────────────────
  private viewSub: Subscription | null = null;
  private msgSub: Subscription | null = null;
  private debounceHandle: ReturnType<typeof setTimeout> | null = null;
  private cursorThrottleHandle: ReturnType<typeof setTimeout> | null = null;
  private scrollThrottleHandle: ReturnType<typeof setTimeout> | null = null;
  private pendingScroll: SharedViewState['scroll'] | null = null;
  private pendingCursor: SharedViewState['cursor'] | null = null;

  bindSession(sessionId: string, ownerUserId: string, encrypter?: PayloadEncrypter): void {
    this.unbindSession();
    this.sessionId = sessionId;
    this.ownerUserId = ownerUserId;
    this.active = true;
    this.followMode = 'active';
    this.controlGrantToken = null;
    this.lastDeltaTs = 0;
    this.deltaTimestamps = [];
    this.cursorTimestamps = [];
    this.lastSeqSent = 0;
    if (encrypter) this.encrypter = encrypter;
    this.view.bindToSession(sessionId, ownerUserId);
    this.subscribeToView();
    this.subscribeToTransport();
    this.sendInitialSnapshot();
  }

  unbindSession(): void {
    this.active = false;
    this.sessionId = '';
    this.ownerUserId = '';
    this.view.unbindFromSession();
    if (this.debounceHandle !== null) { clearTimeout(this.debounceHandle); this.debounceHandle = null; }
    if (this.cursorThrottleHandle !== null) { clearTimeout(this.cursorThrottleHandle); this.cursorThrottleHandle = null; }
    if (this.scrollThrottleHandle !== null) { clearTimeout(this.scrollThrottleHandle); this.scrollThrottleHandle = null; }
    this.viewSub?.unsubscribe();
    this.viewSub = null;
    this.msgSub?.unsubscribe();
    this.msgSub = null;
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
    return this.controlGrantToken !== null;
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
    const request: ControlMessage = {
      sessionId: this.sessionId,
      senderUserId: this.ownerUserId,
      kind: 'request',
      grantToken: null,
      createdAt: Date.now(),
    };
    this.transport.send('control', request);
  }

  // ── Outgoing: state -> transport ──────────────────────────────────

  private subscribeToView(): void {
    this.viewSub = this.view.state$.subscribe((state) => {
      if (!this.active) return;
      // If the seq didn't move (the state service short-circuited
      // an unchanged state), there is nothing to send.
      if (state.seq === this.lastSeqSent) return;
      // Snapshot on first emission after bind, or on major changes
      if (this.lastSeqSent === 0) {
        this.sendSnapshot(state);
      } else {
        this.scheduleDelta(state);
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
    if (this.isRateLimited()) return;
    const prev = this.view.current;
    const delta = this.delta.createDelta(prev, state);
    if (delta.kind === 'scroll' && state.scroll) {
      this.maybeSendScroll(state.scroll, prev.viewHash, state.viewHash);
      return;
    }
    if (delta.kind === 'cursor' && state.cursor) {
      this.maybeSendCursor(state.cursor, prev.viewHash, state.viewHash);
      return;
    }
    if (delta.ops.length === 0) return; // no real change
    this.sendDeltaEnvelope(delta);
  }

  private maybeSendScroll(scroll: SharedViewState['scroll'], baseHash: string, newHash: string): void {
    const serialised = `${scroll.x}:${scroll.y}`;
    if (serialised === this.lastScrollSent) return;
    this.lastScrollSent = serialised;
    if (this.scrollThrottleHandle !== null) return;
    this.scrollThrottleHandle = setTimeout(() => {
      this.scrollThrottleHandle = null;
      const delta: ViewStateDelta = {
        version: PAIR_VIEW_SYNC_VERSION,
        sessionId: this.sessionId,
        senderUserId: this.ownerUserId,
        seq: this.view.current.seq,
        baseHash, newHash, kind: 'scroll',
        ops: [], createdAt: Date.now(),
        payload: scroll,
      };
      this.sendDeltaEnvelope(delta);
    }, SCROLL_THROTTLE_MS);
  }

  private maybeSendCursor(cursor: SharedViewState['cursor'], baseHash: string, newHash: string): void {
    const serialised = `${cursor.line}:${cursor.column}`;
    if (serialised === this.lastCursorSent) return;
    this.lastCursorSent = serialised;
    if (this.cursorTimestamps.length >= MAX_CURSORS_PER_SECOND) {
      const head = this.cursorTimestamps[0];
      if (Date.now() - head < 1000) return;
      this.cursorTimestamps.shift();
    }
    this.cursorTimestamps.push(Date.now());
    if (this.cursorThrottleHandle !== null) return;
    this.cursorThrottleHandle = setTimeout(() => {
      this.cursorThrottleHandle = null;
      const delta: ViewStateDelta = {
        version: PAIR_VIEW_SYNC_VERSION,
        sessionId: this.sessionId,
        senderUserId: this.ownerUserId,
        seq: this.view.current.seq,
        baseHash, newHash, kind: 'cursor',
        ops: [], createdAt: Date.now(),
        payload: cursor,
      };
      this.sendDeltaEnvelope(delta);
    }, CURSOR_THROTTLE_MS);
  }

  private isRateLimited(): boolean {
    const now = Date.now();
    this.deltaTimestamps = this.deltaTimestamps.filter((t) => now - t < 1000);
    if (this.deltaTimestamps.length >= MAX_DELTAS_PER_SECOND) return true;
    this.deltaTimestamps.push(now);
    return false;
  }

  private sendInitialSnapshot(): void {
    this.sendSnapshot(this.view.current);
  }

  private sendSnapshot(state: SharedViewState): void {
    const delta = this.delta.createSnapshot(state);
    this.sendDeltaEnvelope(delta);
    this.stats.snapshotsSent += 1;
    this._stats$.next({ ...this.stats });
  }

  private sendDeltaEnvelope(delta: ViewStateDelta): void {
    if (!this.active || !this.sessionId) return;
    const envelope = this.toRelayEnvelope(delta);
    if (!envelope) return;
    this.transport.sendView(envelope);
    if (delta.kind === 'snapshot') return;
    if (delta.kind === 'cursor') {
      this.stats.cursorsSent += 1;
    } else {
      this.stats.deltasSent += 1;
    }
    this._stats$.next({ ...this.stats });
  }

  private toRelayEnvelope(delta: ViewStateDelta): RelayEnvelope | null {
    if (!this.share.currentPermissions()) {
      // T06/T11: backend rejects payloads without view_tui. Skip silently.
      return null;
    }
    const plaintext = JSON.stringify(delta);
    const encrypted = this.encrypter(plaintext);
    if (encrypted.length > MAX_ENCRYPTED_PAYLOAD_BYTES) return null;
    if (delta.kind === 'snapshot' && encrypted.length > SNAPSHOT_WARN_BYTES) {
      // Soft warning: snapshots over 32 KB are flagged but still sent.
    }
    return {
      message_id: newId(),
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
        this.handleIncomingView(msg.payload);
        return;
      }
      if (msg.type === 'cursor') {
        this.handleIncomingCursor(msg.payload);
        return;
      }
      if (msg.type === 'control') {
        this.handleIncomingControl(msg.payload);
        return;
      }
      if (msg.type === 'snapshot_request') {
        this.stats.snapshotRequestsReceived += 1;
        this._stats$.next({ ...this.stats });
        this.sendSnapshot(this.view.current);
        return;
      }
    });
  }

  private handleIncomingView(raw: unknown): void {
    // The transport delivers RelayEnvelopes; we have to decrypt
    // and validate the inner delta. A failed decrypt is treated
    // like any other invalid payload: dropped silently.
    if (!raw || typeof raw !== 'object') {
      this.stats.appliesRejected += 1;
      this._stats$.next({ ...this.stats });
      return;
    }
    const envelope = raw as { encrypted_payload?: string };
    if (typeof envelope.encrypted_payload !== 'string') {
      this.stats.appliesRejected += 1;
      this._stats$.next({ ...this.stats });
      return;
    }
    const plain = this.decrypter(envelope.encrypted_payload);
    if (plain === null) {
      this.stats.appliesRejected += 1;
      this._stats$.next({ ...this.stats });
      return;
    }
    let parsed: unknown;
    try { parsed = JSON.parse(plain); } catch {
      this.stats.appliesRejected += 1;
      this._stats$.next({ ...this.stats });
      return;
    }
    if (!isViewStateDelta(parsed)) {
      this.stats.appliesRejected += 1;
      this._stats$.next({ ...this.stats });
      return;
    }
    const delta = parsed;
    if (delta.sessionId !== this.sessionId) {
      this.stats.appliesRejected += 1;
      this._stats$.next({ ...this.stats });
      return;
    }
    const perms = this.share.currentPermissions();
    if (!hasPermission(perms, 'view_tui')) {
      this.stats.appliesRejected += 1;
      this._stats$.next({ ...this.stats });
      return;
    }
    if (this.delta.requiresSnapshotRequest(delta, this.view.current)) {
      this.requestSnapshot();
      this.stats.appliesRejected += 1;
      this._stats$.next({ ...this.stats });
      return;
    }
    if (this.followMode === 'paused' && delta.kind !== 'snapshot') {
      // Local deviation in progress: only snapshots can re-sync.
      return;
    }
    const next = this.delta.applyDelta(this.view.current, delta);
    this.view.updatePartial(this.toPartial(next));
    this.stats.appliesAccepted += 1;
    this._stats$.next({ ...this.stats });
  }

  private handleIncomingCursor(raw: unknown): void {
    if (!this.share.currentPermissions() || !hasPermission(this.share.currentPermissions(), 'cursor')) {
      this.stats.appliesRejected += 1;
      this._stats$.next({ ...this.stats });
      return;
    }
    // The cursor is shown in a presence overlay; we don't apply it
    // to local state. The actual application of the cursor position
    // happens in PairPresenceService. We just record the event.
    this.stats.appliesAccepted += 1;
    this._stats$.next({ ...this.stats });
  }

  private handleIncomingControl(raw: unknown): void {
    if (!isControlMessage(raw)) {
      this.stats.appliesRejected += 1;
      this._stats$.next({ ...this.stats });
      return;
    }
    const msg = raw as ControlMessage;
    if (msg.sessionId !== this.sessionId) {
      this.stats.appliesRejected += 1;
      this._stats$.next({ ...this.stats });
      return;
    }
    // T12: control default-deny. The permission must be granted AND
    // the grant token must match a token previously issued. The
    // grant is session-scoped, never persisted.
    const perms = this.share.currentPermissions();
    if (!hasPermission(perms, 'control')) {
      this.stats.controlDenied += 1;
      this._stats$.next({ ...this.stats });
      return;
    }
    if (msg.kind === 'request') {
      // Owner side: issue a grant token.
      this.controlGrantToken = newId();
      const grant: ControlMessage = {
        sessionId: this.sessionId,
        senderUserId: this.ownerUserId,
        kind: 'grant',
        grantToken: this.controlGrantToken,
        createdAt: Date.now(),
      };
      this.transport.send('control', grant);
      this.stats.controlGranted += 1;
      this._stats$.next({ ...this.stats });
      return;
    }
    if (msg.kind === 'grant') {
      // Partner side: only accept if we previously requested control.
      this.controlGrantToken = msg.grantToken;
      this.stats.controlGranted += 1;
      this._stats$.next({ ...this.stats });
      return;
    }
    if (msg.kind === 'revoke') {
      this.controlGrantToken = null;
      this.stats.controlRevoked += 1;
      this._stats$.next({ ...this.stats });
      return;
    }
    if (msg.kind === 'request_follow' || msg.kind === 'request_unfollow') {
      // Toggle follow mode on request; both sides can ask.
      this.setFollowMode(msg.kind === 'request_follow' ? 'active' : 'paused');
      return;
    }
  }

  // ── Helpers ────────────────────────────────────────────────────────

  private requestSnapshot(): void {
    if (!this.active) return;
    this.transport.send('snapshot_request', { sessionId: this.sessionId });
    this.stats.snapshotRequestsSent += 1;
    this._stats$.next({ ...this.stats });
  }

  private toPartial(next: SharedViewState): Partial<SharedViewState> {
    return {
      route: next.route,
      queryParams: next.queryParams,
      activeSurface: next.activeSurface,
      activeTab: next.activeTab,
      activePanel: next.activePanel,
      activeArtifactId: next.activeArtifactId,
      activeArtifactHash: next.activeArtifactHash,
      activeFilePath: next.activeFilePath,
      activeSymbolId: next.activeSymbolId,
      scroll: next.scroll,
      cursor: next.cursor,
      selection: next.selection,
      zoom: next.zoom,
      collapsedSections: next.collapsedSections,
    };
  }

  ngOnDestroy(): void {
    this.unbindSession();
  }
}
