import { describe, expect, it, beforeEach, vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Subject } from 'rxjs';

import { PairViewSyncService } from './pair-view-sync.service';
import { SharedViewStateService } from './shared-view-state.service';
import { ViewDeltaService } from './view-delta.service';
import { WebrtcTransportService } from './webrtc-transport.service';
import { ShareSessionService } from './share-session.service';
import { PAIR_VIEW_CRYPTO, PairViewCryptoPort } from './pair-view-crypto.service';
import { PairSecureSequenceService } from './pair-secure-sequence.service';
import {
  ControlMessage,
  DEFAULT_PERMISSIONS,
  PermissionSet,
  RelayEnvelope,
  SharedViewState,
  ViewStateDelta,
  PAIR_VIEW_SYNC_VERSION,
} from './pair-view-sync.types';

// ── Test doubles ─────────────────────────────────────────────────────

class FakeTransport {
  mode$ = { value: 'webrtc' as 'webrtc' | 'hub_relay' | 'idle' };
  message$ = new Subject<{ type: string; session_id: string; payload: unknown }>();
  sent: Array<{ type: string; payload: unknown }> = [];
  sentView: RelayEnvelope[] = [];
  send(type: string, payload: unknown): void {
    this.sent.push({ type, payload });
  }
  sendView(envelope: RelayEnvelope): void {
    this.sentView.push(envelope);
  }
  // T06
  emitView(viewEnvelope: RelayEnvelope, sessionId: string): void {
    this.message$.next({ type: 'view_payload', session_id: sessionId, payload: viewEnvelope });
  }
  emitControl(msg: ControlMessage, sessionId: string): void {
    this.message$.next({
      type: 'view_payload', session_id: sessionId,
      payload: { encrypted_payload: `TEST1::${JSON.stringify(msg)}` },
    });
  }
  emitSnapshotRequest(sessionId: string): void {
    this.message$.next({ type: 'snapshot_request', session_id: sessionId, payload: null });
  }
}

class FakeCrypto implements PairViewCryptoPort {
  ready(_scopeId: string, epoch: number): boolean { return epoch === 1; }
  async seal(plaintext: string): Promise<string> { return `TEST1::${plaintext}`; }
  async open(serialized: string): Promise<any> {
    const prefix = serialized.startsWith('TEST1::') ? 'TEST1::' : 'STUB1::';
    if (!serialized.startsWith(prefix)) throw new Error('test_ciphertext_invalid');
    const plaintext = serialized.slice(prefix.length);
    const value = JSON.parse(plaintext);
    let payloadType = 'pair.view_delta';
    if (value?.kind && ['request', 'grant', 'revoke', 'request_follow', 'request_unfollow'].includes(value.kind)) payloadType = 'pair.control';
    else if (value?.userLabel) payloadType = 'pair.cursor';
    return { plaintext, payloadType, senderId: value?.senderUserId ?? 'peer', sequence: value?.seq ?? 1 };
  }
  clear(): void {}
}

class FakeShare {
  private perms: PermissionSet | null = { ...DEFAULT_PERMISSIONS, remote_control: false, remote_cursor: false };
  currentPermissions(): PermissionSet | null { return this.perms; }
  setPerms(p: PermissionSet | null): void { this.perms = p; }
}

function setup(extra?: { perms?: PermissionSet | null }) {
  TestBed.configureTestingModule({
    providers: [provideRouter([])],
  });
  const transport = new FakeTransport();
  const share = new FakeShare();
  let secureSequence = 0;
  if (extra?.perms !== undefined) share.setPerms(extra.perms);

  // Provide fakes for the services that PairViewSync injects.
  TestBed.overrideProvider(WebrtcTransportService, { useValue: transport });
  TestBed.overrideProvider(ShareSessionService, { useValue: share });
  TestBed.overrideProvider(PAIR_VIEW_CRYPTO, { useValue: new FakeCrypto() });
  TestBed.overrideProvider(PairSecureSequenceService, { useValue: {
    next: async () => { secureSequence += 1; return secureSequence; },
    clearScope: () => undefined,
  } });

  const sync = TestBed.runInInjectionContext(() => new PairViewSyncService());
  const view = TestBed.inject(SharedViewStateService);
  const delta = TestBed.inject(ViewDeltaService);

  // Manually bind, since we don't have a real session response.
  sync.bindSession('sess-test', 'owner-test', 1);
  return { transport, share, sync, view, delta };
}

function decodeEncrypted(enc: string): unknown {
  if (enc.startsWith('TEST1::')) return JSON.parse(enc.slice('TEST1::'.length));
  if (enc.startsWith('STUB1::')) return JSON.parse(enc.slice('STUB1::'.length));
  return null;
}

describe('PairViewSyncService (T05 sendepfad)', () => {
  it('sends a snapshot on bind', async () => {
    const { transport, sync } = setup();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(transport.sentView.length).toBeGreaterThanOrEqual(1);
    const first = transport.sentView[0];
    expect(first.kind).toBe('snapshot');
    sync.unbindSession();
  });

  it('debounces non-scroll deltas', async () => {
    const { transport, view, sync } = setup();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const before = transport.sentView.length;
    view.updatePartial({ activeTab: 'details' });
    view.updatePartial({ activeTab: 'logs' });
    view.updatePartial({ activeTab: 'review' });
    await new Promise((r) => setTimeout(r, 150));
    const after = transport.sentView.length;
    // At most 1 new delta from the three updates, plus the snapshot
    expect(after - before).toBeLessThanOrEqual(1);
    sync.unbindSession();
  });

  it('rate-limits burst to MAX_DELTAS_PER_SECOND', async () => {
    const { transport, view, sync } = setup();
    const before = transport.sentView.length;
    for (let i = 0; i < 50; i++) {
      view.updatePartial({ activeTab: `tab-${i}` });
    }
    await new Promise((r) => setTimeout(r, 300));
    // We must not have flooded the transport
    const sent = transport.sentView.length - before;
    expect(sent).toBeLessThanOrEqual(20);
    sync.unbindSession();
  });

  it('scroll is throttled (no more than 1 message per SCROLL_THROTTLE_MS)', async () => {
    const { transport, view, sync } = setup();
    const before = transport.sentView.length;
    for (let i = 0; i < 10; i++) {
      view.updateScroll({ x: i, y: i * 10 });
    }
    await new Promise((r) => setTimeout(r, 250));
    const scrollMsgs = transport.sentView.slice(before).filter((e) => e.kind === 'scroll');
    expect(scrollMsgs.length).toBeLessThanOrEqual(3);
    sync.unbindSession();
  });
});

describe('PairViewSyncService (T07 apply path)', () => {
  it('applies a valid snapshot to local state', async () => {
    const { transport, view, sync } = setup();
    const initialViewHash = view.current.viewHash;
    const snapshot: ViewStateDelta = {
      version: PAIR_VIEW_SYNC_VERSION,
      sessionId: 'sess-test',
      senderUserId: 'peer',
      seq: 999,
      baseHash: 'xxx',
      newHash: 'yyy',
      kind: 'snapshot',
      ops: [],
      createdAt: Date.now(),
    };
    transport.emitView(transport.sentView[0], 'sess-test'); // warmup
    // Construct a valid snapshot envelope to apply
    const enc = `STUB1::${JSON.stringify(snapshot)}`;
    transport.emitView({
      message_id: 'm1', kind: 'snapshot', base_hash: 'xxx', new_hash: 'yyy',
      width: 0, height: 0, encrypted_payload: enc,
    }, 'sess-test');
    await new Promise((resolve) => setTimeout(resolve, 0));
    // viewHash should change because we apply the snapshot
    expect(view.current.viewHash).not.toBe(initialViewHash);
    sync.unbindSession();
  });

  it('drops envelopes with mismatched sessionId', () => {
    const { transport, view, sync } = setup();
    const seq0 = view.current.seq;
    const enc = `STUB1::${JSON.stringify({
      version: PAIR_VIEW_SYNC_VERSION,
      sessionId: 'sess-other',
      senderUserId: 'peer', seq: 1, baseHash: '', newHash: 'h',
      kind: 'snapshot', ops: [], createdAt: Date.now(),
    })}`;
    transport.emitView({
      message_id: 'm2', kind: 'snapshot', base_hash: '', new_hash: 'h',
      width: 0, height: 0, encrypted_payload: enc,
    }, 'sess-other');
    expect(view.current.seq).toBe(seq0); // unchanged
    sync.unbindSession();
  });

  it('drops envelopes that fail validator', () => {
    const { transport, sync } = setup();
    // Garbage payload
    transport.emitView({
      message_id: 'm3', kind: 'snapshot', base_hash: '', new_hash: 'h',
      width: 0, height: 0, encrypted_payload: 'STUB1::not-json',
    }, 'sess-test');
    // No exception = good
    sync.unbindSession();
  });
});

describe('PairViewSyncService (T12 control default-deny)', () => {
  it('denies a control request when permission is not granted', async () => {
    const { transport, sync } = setup({ perms: { ...DEFAULT_PERMISSIONS, remote_control: false } });
    const before = transport.sent.filter((s) => s.type === 'control').length;
    transport.emitControl({
      sessionId: 'sess-test', senderUserId: 'peer', kind: 'request', grantToken: null, createdAt: Date.now(),
    }, 'sess-test');
    await new Promise((resolve) => setTimeout(resolve, 0));
    const after = transport.sent.filter((s) => s.type === 'control').length;
    expect(after).toBe(before); // no grant issued
    sync.unbindSession();
  });

  it('issues a grant on request when permission is granted', async () => {
    const { transport, sync } = setup({ perms: { ...DEFAULT_PERMISSIONS, remote_control: true } });
    await new Promise((resolve) => setTimeout(resolve, 0));
    const before = transport.sentView.length;
    transport.emitControl({
      sessionId: 'sess-test', senderUserId: 'peer', kind: 'request', grantToken: null, createdAt: Date.now(),
    }, 'sess-test');
    await new Promise((resolve) => setTimeout(resolve, 0));
    const after = transport.sentView.length;
    expect(after).toBe(before + 1);
    const grant = decodeEncrypted(transport.sentView[transport.sentView.length - 1].encrypted_payload) as ControlMessage;
    expect(grant.kind).toBe('grant');
    expect(grant.grantToken).toBeTruthy();
    sync.unbindSession();
  });

  it('revoke clears an existing grant', async () => {
    const { transport, sync } = setup({ perms: { ...DEFAULT_PERMISSIONS, remote_control: true } });
    transport.emitControl({
      sessionId: 'sess-test', senderUserId: 'peer', kind: 'request', grantToken: null, createdAt: Date.now(),
    }, 'sess-test');
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(sync.hasControlGrant()).toBe(true);
    transport.emitControl({
      sessionId: 'sess-test', senderUserId: 'peer', kind: 'revoke', grantToken: null, createdAt: Date.now(),
    }, 'sess-test');
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(sync.hasControlGrant()).toBe(false);
    sync.unbindSession();
  });

  it('denies control from a different session', async () => {
    const { transport, sync } = setup({ perms: { ...DEFAULT_PERMISSIONS, remote_control: true } });
    transport.emitControl({
      sessionId: 'sess-other', senderUserId: 'peer', kind: 'request', grantToken: null, createdAt: Date.now(),
    }, 'sess-other');
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(sync.hasControlGrant()).toBe(false);
    sync.unbindSession();
  });
});
