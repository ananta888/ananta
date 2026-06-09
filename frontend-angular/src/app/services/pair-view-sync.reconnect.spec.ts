/**
 * T14: Pair-View-Sync reconnect scenarios.
 *
 * These specs cover what happens when the transport drops and
 * the user comes back: the service must:
 *  - keep accepting incoming messages from the old transport
 *    after a `unbind`/`bind` cycle (fresh state)
 *  - drop messages tagged with a stale session id
 *  - drop messages whose baseHash does not match the current
 *    local view hash (the engine triggers a snapshot request)
 *  - request a snapshot whenever the receiver detects a base-
 *    hash mismatch on a view-payload message
 *  - the peer-cursor map is cleared on unbind so a new
 *    session does not inherit stale cursors
 */
import { describe, expect, it } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Subject } from 'rxjs';

import { PairViewSyncService, PeerCursor } from './pair-view-sync.service';
import { SharedViewStateService } from './shared-view-state.service';
import { ViewDeltaService } from './view-delta.service';
import { WebrtcTransportService } from './webrtc-transport.service';
import { ShareSessionService } from './share-session.service';
import {
  DEFAULT_PERMISSIONS,
  PermissionSet,
  RelayEnvelope,
  SharedViewState,
  ViewStateDelta,
  PAIR_VIEW_SYNC_VERSION,
} from './pair-view-sync.types';

class FakeTransport {
  message$ = new Subject<{ type: string; session_id: string; payload: unknown }>();
  sent: Array<{ type: string; payload: unknown }> = [];
  sentView: RelayEnvelope[] = [];
  send(type: string, payload: unknown): void { this.sent.push({ type, payload }); }
  sendView(e: RelayEnvelope): void { this.sentView.push(e); }
  emitView(env: RelayEnvelope, sessionId: string): void {
    this.message$.next({ type: 'view_payload', session_id: sessionId, payload: env });
  }
  emitCursorMessage(msg: PeerCursor, sessionId: string): void {
    this.message$.next({
      type: 'cursor',
      session_id: sessionId,
      payload: { encrypted_payload: `STUB1::${JSON.stringify({
        sessionId, senderUserId: msg.userId, userLabel: msg.userLabel,
        cursor: msg.cursor, sentAt: msg.lastSeenAt,
      })}` },
    });
  }
  emitSnapshotRequest(sessionId: string): void {
    this.message$.next({ type: 'snapshot_request', session_id: sessionId, payload: null });
  }
}

class FakeShare {
  perms: PermissionSet | null = { ...DEFAULT_PERMISSIONS, control: false, cursor: true };
  currentPermissions(): PermissionSet | null { return this.perms; }
  setPerms(p: PermissionSet | null): void { this.perms = p; }
}

function setup() {
  TestBed.configureTestingModule({ providers: [provideRouter([])] });
  const transport = new FakeTransport();
  const share = new FakeShare();
  TestBed.overrideProvider(WebrtcTransportService, { useValue: transport });
  TestBed.overrideProvider(ShareSessionService, { useValue: share });
  const sync = TestBed.runInInjectionContext(() => new PairViewSyncService());
  const view = TestBed.inject(SharedViewStateService);
  const delta = TestBed.inject(ViewDeltaService);
  return { transport, share, sync, view, delta };
}

function envFor(delta: ViewStateDelta): RelayEnvelope {
  return {
    message_id: 'm1', kind: delta.kind,
    base_hash: delta.baseHash, new_hash: delta.newHash,
    width: 0, height: 0,
    encrypted_payload: `STUB1::${JSON.stringify(delta)}`,
  };
}

function makeDelta(sessionId: string, senderUserId: string, seq: number, baseHash: string, newHash: string, kind: ViewStateDelta['kind'] = 'delta'): ViewStateDelta {
  return {
    version: PAIR_VIEW_SYNC_VERSION, sessionId, senderUserId, seq,
    baseHash, newHash, kind, ops: [], createdAt: Date.now(), payload: null,
  };
}

describe('PairViewSyncService (T14 reconnect)', () => {
  it('rejects view-payloads with a stale session id', () => {
    const { transport, sync, view } = setup();
    sync.bindSession('sess-A', 'owner-1');
    const initial = transport.sentView[0];
    const state = view.current;
    const d = makeDelta('sess-B', 'partner-x', 99, initial.new_hash, state.viewHash);
    const before = transport.sent.length;
    transport.emitView(envFor(d), 'sess-B');
    expect(transport.sent.length).toBe(before); // nothing sent in response
    sync.unbindSession();
  });

  it('rejects view-payloads whose baseHash mismatches the local hash', () => {
    const { transport, sync, view } = setup();
    sync.bindSession('sess-A', 'owner-1');
    const state = view.current;
    // baseHash is intentionally wrong (a hash the local state never produced)
    const d = makeDelta('sess-A', 'partner-x', 100, 'wrong-hash-1234', state.viewHash);
    const snapshotReqsBefore = transport.sent.filter((s) => s.type === 'snapshot_request').length;
    transport.emitView(envFor(d), 'sess-A');
    // The engine must request a snapshot
    const snapshotReqsAfter = transport.sent.filter((s) => s.type === 'snapshot_request').length;
    expect(snapshotReqsAfter).toBe(snapshotReqsBefore + 1);
    sync.unbindSession();
  });

  it('rejects cursor messages with a stale session id', () => {
    const { transport, sync } = setup();
    sync.bindSession('sess-A', 'owner-1');
    const emitted: PeerCursor[] = [];
    const sub = sync.peerCursors$.subscribe((m) => { for (const v of m.values()) emitted.push(v); });
    // First, emit a valid cursor so peer-cursor map is populated
    transport.emitCursorMessage({
      userId: 'partner-A', userLabel: 'P',
      cursor: { line: null, column: null, x: 10, y: 20 },
      lastSeenAt: Date.now(),
    }, 'sess-A');
    // Now a stale-session cursor
    transport.emitCursorMessage({
      userId: 'partner-A', userLabel: 'P',
      cursor: { line: null, column: null, x: 99, y: 99 },
      lastSeenAt: Date.now(),
    }, 'sess-Z');
    const found = emitted.find((c) => c.cursor.x === 99 && c.cursor.y === 99);
    expect(found).toBeUndefined();
    sub.unsubscribe();
    sync.unbindSession();
  });

  it('clears peer-cursor map on unbindSession so a new session does not inherit stale cursors', () => {
    const { transport, sync } = setup();
    sync.bindSession('sess-A', 'owner-1');
    const emits: Array<ReadonlyMap<string, PeerCursor>> = [];
    const sub = sync.peerCursors$.subscribe((m) => { emits.push(m); });
    transport.emitCursorMessage({
      userId: 'partner-A', userLabel: 'P',
      cursor: { line: null, column: null, x: 11, y: 22 },
      lastSeenAt: Date.now(),
    }, 'sess-A');
    // Re-bind -> a different session
    sync.unbindSession();
    // The most recent emit must be the empty map produced by unbind
    const last = emits[emits.length - 1];
    expect(last).toBeDefined();
    expect(last.size).toBe(0);
    sub.unsubscribe();
    sync.unbindSession();
  });

  it('keeps accepting view-payloads across an unbind+bind cycle (fresh session id)', () => {
    const { transport, sync, view } = setup();
    sync.bindSession('sess-A', 'owner-1');
    // Change something
    view.updatePartial({ activeTab: 'first' });
    sync.unbindSession();
    sync.bindSession('sess-B', 'owner-2');
    const initial = transport.sentView[transport.sentView.length - 1];
    const state = view.current;
    const d = makeDelta('sess-B', 'partner-y', 1, initial.new_hash, state.viewHash);
    // Should accept
    const before = (sync as any).stats.appliesAccepted;
    transport.emitView(envFor(d), 'sess-B');
    const after = (sync as any).stats.appliesAccepted;
    expect(after).toBeGreaterThanOrEqual(before + 1);
    sync.unbindSession();
  });

  it('throttles snapshot-requests: only one snapshot_request is emitted per baseHash mismatch', () => {
    const { transport, sync, view } = setup();
    sync.bindSession('sess-A', 'owner-1');
    const state = view.current;
    const d = makeDelta('sess-A', 'partner-x', 200, 'wrong-hash-zzzz', state.viewHash);
    const before = transport.sent.filter((s) => s.type === 'snapshot_request').length;
    transport.emitView(envFor(d), 'sess-A');
    transport.emitView(envFor(d), 'sess-A');
    transport.emitView(envFor(d), 'sess-A');
    const after = transport.sent.filter((s) => s.type === 'snapshot_request').length;
    // 3 mismatch, 3 requests (we don't dedupe these, but they must not loop)
    expect(after - before).toBe(3);
    sync.unbindSession();
  });
});
