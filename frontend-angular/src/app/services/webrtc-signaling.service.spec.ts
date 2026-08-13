import { TestBed } from '@angular/core/testing';
import { Observable, Subject, of, throwError } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import { SignalingStatus, WebrtcSignalingService } from './webrtc-signaling.service';

describe('WebrtcSignalingService authenticated cursor signaling', () => {
  let service: WebrtcSignalingService;
  let pollResponse: unknown;
  let handler: ReturnType<typeof vi.fn<(message: unknown) => Promise<void>>>;
  const signalPoll = vi.fn<(
    sessionId: string,
    cursor: string,
    securityEpoch?: number,
  ) => Observable<unknown>>();
  const signalSend = vi.fn();
  const controlPlane = {
    signalPoll,
    signalSend,
    assertSessionAvailable: vi.fn(),
    peerIdForSession: vi.fn(() => 'peer-a'),
    isPublicSession: vi.fn(() => false),
    requiresSignalEpoch: vi.fn(() => false),
  };

  function connect(securityEpoch = 2): void {
    service.connect(
      '',
      'session-1',
      'peer-b',
      controlPlane.isPublicSession('session-1') ? securityEpoch : undefined,
    );
  }

  beforeEach(() => {
    vi.useFakeTimers();
    sessionStorage.clear();
    pollResponse = { ok: true, data: { signals: [], cursor: '' } };
    signalPoll.mockReset();
    signalPoll.mockImplementation(() => of(pollResponse));
    signalSend.mockReset();
    signalSend.mockReturnValue(of({ ok: true }));
    controlPlane.assertSessionAvailable.mockReset();
    controlPlane.peerIdForSession.mockReset();
    controlPlane.peerIdForSession.mockReturnValue('peer-a');
    controlPlane.isPublicSession.mockReset();
    controlPlane.isPublicSession.mockReturnValue(false);
    controlPlane.requiresSignalEpoch.mockReset();
    controlPlane.requiresSignalEpoch.mockImplementation(sessionId => (
      controlPlane.isPublicSession(sessionId)
    ));
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [
      WebrtcSignalingService,
      { provide: PairSessionControlPlaneService, useValue: controlPlane },
    ] });
    service = TestBed.inject(WebrtcSignalingService);
    handler = vi.fn(async () => undefined);
    service.bindMessageHandler(message => handler(message));
  });

  afterEach(() => {
    service.hardDisconnect();
    sessionStorage.clear();
    vi.useRealTimers();
  });

  it('starts as disconnected', () => {
    expect(service.status$.value).toBe<SignalingStatus>('disconnected');
  });

  it('starts an immediate authenticated poll and advances the opaque cursor', async () => {
    pollResponse = { ok: true, data: { signals: [], cursor: '7' } };
    service.connect('wss://ignored.example/signaling', 'session-1', 'peer-b');
    await settleAsyncWork();
    (service as unknown as { pollSignals(): void }).pollSignals();

    expect(service.status$.value).toBe<SignalingStatus>('connected');
    expect(signalPoll.mock.calls).toEqual([
      ['session-1', ''],
      ['session-1', '7'],
    ]);
  });

  it('hardDisconnect is idempotent and leaves no reconnect path', () => {
    connect();
    service.hardDisconnect();
    expect(() => service.hardDisconnect()).not.toThrow();
    expect(service.status$.value).toBe('disconnected');
    expect((service as unknown as { pollHandle: unknown }).pollHandle).toBeNull();
  });

  it('binds the verified remote peer to every signal', async () => {
    connect();
    await service.send({
      type: 'offer', session_id: 'session-1', recipient_id: 'stale-peer', payload: { sdp: 'v=0' },
    });

    expect(signalSend).toHaveBeenCalledWith('session-1', {
      type: 'offer',
      session_id: 'session-1',
      recipient_id: 'peer-b',
      payload: { sdp: 'v=0' },
    });
  });

  it('binds the exact public epoch to every outbound signal', async () => {
    controlPlane.isPublicSession.mockReturnValue(true);
    connect(7);

    await service.send({
      type: 'offer',
      session_id: 'session-1',
      security_epoch: 99,
      payload: { sdp: 'v=0' },
    });

    expect(signalSend).toHaveBeenCalledWith('session-1', expect.objectContaining({
      type: 'offer',
      session_id: 'session-1',
      recipient_id: 'peer-b',
      security_epoch: 7,
    }));
    expect(signalPoll).toHaveBeenCalledWith('session-1', '', 7);
  });

  it('fails closed before a public poll when the exact epoch is missing', () => {
    controlPlane.isPublicSession.mockReturnValue(true);

    service.connect('', 'session-1', 'peer-b');

    expect(service.status$.value).toBe('failed');
    expect(service.failureReason$.value).toBe('signal_epoch_required');
    expect(signalPoll).not.toHaveBeenCalled();
    expect(signalSend).not.toHaveBeenCalled();
  });

  it('keeps the public v1 signaling wire compatible without an epoch field', async () => {
    controlPlane.isPublicSession.mockReturnValue(true);
    controlPlane.requiresSignalEpoch.mockReturnValue(false);
    pollResponse = {
      ok: true,
      data: {
        cursor: '1',
        signals: [{
          id: 'legacy-signal-1', type: 'offer', session_id: 'session-1',
          sender_id: 'peer-b', recipient_id: 'peer-a', payload: 'legacy-offer',
        }],
      },
    };

    connect();
    await settleAsyncWork();
    await service.send({
      type: 'answer', session_id: 'session-1', payload: { sdp: 'legacy-answer' },
    });

    expect(handler).toHaveBeenCalledWith(expect.objectContaining({
      payload: 'legacy-offer',
    }));
    expect(signalPoll).toHaveBeenCalledWith('session-1', '');
    expect(signalSend).toHaveBeenCalledWith('session-1', {
      type: 'answer', session_id: 'session-1', recipient_id: 'peer-b',
      payload: { sdp: 'legacy-answer' },
    });
  });

  it('fails closed before polling or sending without an exact recipient id', async () => {
    service.connect('', 'session-1');
    await expect(service.send({
      type: 'offer', session_id: 'session-1', payload: { sdp: 'v=0' },
    })).rejects.toThrow('webrtc_signal_context_invalid');

    expect(service.status$.value).toBe('failed');
    expect(signalSend).not.toHaveBeenCalled();
    expect(signalPoll).not.toHaveBeenCalled();
  });

  it('fails closed when the verified remote peer reflects the local device peer id', () => {
    service.connect('', 'session-1', 'peer-a');

    expect(service.status$.value).toBe('failed');
    expect(service.failureReason$.value).toBe('webrtc_peer_identity_must_be_distinct');
    expect(signalPoll).not.toHaveBeenCalled();
    expect(signalSend).not.toHaveBeenCalled();
  });

  it('deduplicates public signals and requires their id and addressed recipient', async () => {
    controlPlane.isPublicSession.mockReturnValue(true);
    const accepted = {
      id: 'signal-1', type: 'offer', session_id: 'session-1', sender_id: 'peer-b',
      recipient_id: 'peer-a', security_epoch: 2, payload: 'accepted',
    };
    pollResponse = {
      ok: true,
      data: {
        cursor: '2',
        signals: [
          accepted,
          accepted,
          { ...accepted, id: 'signal-2', recipient_id: 'someone-else' },
          { ...accepted, id: undefined },
        ],
      },
    };
    const observed: string[] = [];
    service.message$.subscribe(message => observed.push(String(message.payload)));

    connect();
    await settleAsyncWork();
    (service as unknown as { pollSignals(): void }).pollSignals();

    expect(observed).toEqual(['accepted']);
    expect(handler).toHaveBeenCalledTimes(1);
    expect(signalPoll.mock.calls).toEqual([
      ['session-1', '', 2],
      ['session-1', '2', 2],
    ]);
  });

  it.each([1, 3])(
    'fails closed on addressed epoch %s without poisoning a replacement epoch',
    async mismatchedEpoch => {
      controlPlane.isPublicSession.mockReturnValue(true);
      pollResponse = {
        ok: true,
        data: {
          cursor: '1',
          signals: [{
            id: `signal-${mismatchedEpoch}`,
            type: 'offer',
            session_id: 'session-1',
            sender_id: 'peer-b',
            recipient_id: 'peer-a',
            security_epoch: mismatchedEpoch,
            payload: { type: 'offer', sdp: 'wrong-epoch' },
          }],
        },
      };

      connect(2);
      await settleAsyncWork();

      expect(handler).not.toHaveBeenCalled();
      expect(service.status$.value).toBe('failed');
      expect(service.isSessionRecreationRequired('session-1', 2)).toBe(true);
      expect(service.isSessionRecreationRequired('session-1', 3)).toBe(false);

      pollResponse = { ok: true, data: { signals: [], cursor: '0' } };
      connect(3);
      await settleAsyncWork();

      expect(service.status$.value).toBe('connected');
      expect(signalPoll.mock.calls.at(-1)).toEqual(['session-1', '', 3]);
    },
  );

  it('starts a new epoch from cursor zero with an independent dedupe scope', async () => {
    controlPlane.isPublicSession.mockReturnValue(true);
    signalPoll
      .mockReturnValueOnce(of({
        ok: true,
        data: {
          cursor: '4',
          signals: [{
            id: 'signal-shared', type: 'offer', session_id: 'session-1',
            sender_id: 'peer-b', recipient_id: 'peer-a', security_epoch: 2,
            payload: 'epoch-2',
          }],
        },
      }))
      .mockReturnValueOnce(of({
        ok: true,
        data: {
          cursor: '1',
          signals: [{
            id: 'signal-shared', type: 'offer', session_id: 'session-1',
            sender_id: 'peer-b', recipient_id: 'peer-a', security_epoch: 3,
            payload: 'epoch-3',
          }],
        },
      }));

    connect(2);
    await settleAsyncWork();
    service.disconnect();
    connect(3);
    await settleAsyncWork();

    expect(signalPoll.mock.calls).toEqual([
      ['session-1', '', 2],
      ['session-1', '', 3],
    ]);
    expect(handler.mock.calls.map(([message]) => (message as SignalMessage).payload))
      .toEqual(['epoch-2', 'epoch-3']);
  });

  it('keeps only one signaling poll in flight', async () => {
    const pending = new Subject<unknown>();
    signalPoll.mockReturnValue(pending);
    connect();

    (service as unknown as { pollSignals(): void }).pollSignals();
    expect(signalPoll).toHaveBeenCalledTimes(1);

    pending.next({ ok: true, data: { signals: [], cursor: '1' } });
    pending.complete();
    await settleAsyncWork();
    (service as unknown as { pollSignals(): void }).pollSignals();
    expect(signalPoll).toHaveBeenCalledTimes(2);
  });

  it('fails closed without dispatching a truncated retained signal window', async () => {
    const observed: unknown[] = [];
    service.message$.subscribe(message => observed.push(message));
    pollResponse = {
      ok: true,
      data: {
        cursor: '9', cursor_floor: '5', truncated: true,
        signals: [{
          id: 'signal-9', type: 'offer', session_id: 'session-1', sender_id: 'peer-b',
          recipient_id: 'peer-a', payload: 'partial-offer',
        }],
      },
    };

    connect();
    await settleAsyncWork();

    expect(service.status$.value).toBe('failed');
    expect(observed).toEqual([]);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('retries a transient poll failure with the unchanged cursor', () => {
    signalPoll.mockReturnValueOnce(throwError(() => ({ status: 503 })));
    connect();
    signalPoll.mockReturnValueOnce(of({ ok: true, data: { signals: [], cursor: '1' } }));

    (service as unknown as { pollSignals(): void }).pollSignals();

    expect(service.status$.value).toBe('connected');
    expect(signalPoll.mock.calls).toEqual([['session-1', ''], ['session-1', '']]);
  });

  it('propagates an explicit terminal poll rejection without a recreation latch', () => {
    controlPlane.isPublicSession.mockReturnValue(true);
    signalPoll.mockReturnValueOnce(throwError(() => ({
      status: 404,
      error: { error: 'session_not_found' },
    })));

    connect();

    expect(service.status$.value).toBe('failed');
    expect(service.failureReason$.value).toBe('session_not_found');
    expect(service.isSessionRecreationRequired('session-1')).toBe(false);
    expect((service as unknown as { pollHandle: unknown }).pollHandle).toBeNull();
  });

  it('latches an unclassified definitive public poll rejection for recreation', () => {
    controlPlane.isPublicSession.mockReturnValue(true);
    signalPoll.mockReturnValueOnce(throwError(() => ({
      status: 404,
      error: { error: 'unexpected_public_rejection' },
    })));

    connect();

    expect(service.status$.value).toBe('failed');
    expect(service.failureReason$.value).toBe('public_signaling_session_recreation_required');
    expect(service.isSessionRecreationRequired('session-1')).toBe(true);
  });

  it('treats a parked peer poll rejection as reversible without a recreation latch', async () => {
    controlPlane.isPublicSession.mockReturnValue(true);
    signalPoll.mockReturnValueOnce(throwError(() => ({
      status: 409,
      error: { error: 'pair_runtime_not_ready' },
    })));

    connect(2);

    expect(service.status$.value).toBe('disconnected');
    expect(service.failureReason$.value).toBe('pair_runtime_not_ready');
    expect(service.isSessionRecreationRequired('session-1', 2)).toBe(false);
    expect((service as unknown as { pollHandle: unknown }).pollHandle).toBeNull();
    await expect(service.send({
      type: 'offer', session_id: 'session-1', payload: { sdp: 'not-yet' },
    })).rejects.toThrow('pair_runtime_not_ready');
    expect(service.status$.value).toBe('disconnected');

    signalPoll.mockReturnValueOnce(of({ ok: true, data: { signals: [], cursor: '0' } }));
    connect(2);
    expect(service.status$.value).toBe('connected');
  });

  it('honors Retry-After without hammering the signaling endpoint', () => {
    vi.setSystemTime(new Date('2026-08-11T08:00:00Z'));
    signalPoll.mockReturnValueOnce(throwError(() => ({
      status: 429,
      headers: { get: (name: string) => name === 'Retry-After' ? '5' : null },
    })));
    connect();

    (service as unknown as { pollSignals(): void }).pollSignals();
    expect(signalPoll).toHaveBeenCalledTimes(1);
    expect(service.status$.value).toBe('connected');

    vi.setSystemTime(new Date('2026-08-11T08:00:05Z'));
    signalPoll.mockReturnValueOnce(of({ ok: true, data: { signals: [], cursor: '1' } }));
    (service as unknown as { pollSignals(): void }).pollSignals();

    expect(signalPoll.mock.calls).toEqual([['session-1', ''], ['session-1', '']]);
  });

  it('fails closed when the signaling queue rejects an outbound signal', async () => {
    connect();
    signalSend.mockReturnValue(throwError(() => ({ status: 503, error: { error: 'queue_unavailable' } })));

    await expect(service.send({
      type: 'offer', session_id: 'session-1', payload: { sdp: 'v=0' },
    })).rejects.toBeTruthy();

    expect(service.status$.value).toBe('failed');
    expect((service as unknown as { pollHandle: unknown }).pollHandle).toBeNull();
  });

  it('propagates an explicit terminal send rejection without a recreation latch', async () => {
    controlPlane.isPublicSession.mockReturnValue(true);
    connect();
    signalSend.mockReturnValue(throwError(() => ({
      status: 403,
      error: { reason_code: 'membership_capability_retired' },
    })));

    await expect(service.send({
      type: 'offer', session_id: 'session-1', payload: { sdp: 'v=0' },
    })).rejects.toBeTruthy();
    await settleAsyncWork();

    expect(service.status$.value).toBe('failed');
    expect(service.failureReason$.value).toBe('membership_capability_retired');
    expect(service.isSessionRecreationRequired('session-1')).toBe(false);
  });

  it('treats a parked peer send rejection as reversible without poisoning the epoch', async () => {
    controlPlane.isPublicSession.mockReturnValue(true);
    connect(2);
    signalSend.mockReturnValueOnce(throwError(() => ({
      status: 409,
      error: { reason_code: 'pair_runtime_not_ready' },
    })));

    await expect(service.send({
      type: 'offer', session_id: 'session-1', payload: { sdp: 'v=0' },
    })).rejects.toBeTruthy();
    await settleAsyncWork();

    expect(service.status$.value).toBe('disconnected');
    expect(service.failureReason$.value).toBe('pair_runtime_not_ready');
    expect(service.isSessionRecreationRequired('session-1', 2)).toBe(false);

    connect(2);
    expect(service.status$.value).toBe('connected');
  });

  it('retries the exact public signal after Retry-After without letting queued ICE overtake it', async () => {
    controlPlane.isPublicSession.mockReturnValue(true);
    signalSend
      .mockReturnValueOnce(throwError(() => ({
        status: 429,
        headers: { get: (name: string) => name === 'Retry-After' ? '2' : null },
      })))
      .mockReturnValue(of({ ok: true }));
    connect();

    const offer = service.send({
      type: 'offer', session_id: 'session-1', payload: { type: 'offer', sdp: 'v=0' },
    });
    const earlyIce = service.send({
      type: 'ice_candidate', session_id: 'session-1', payload: { candidate: 'candidate:1' },
    });
    await settleAsyncWork();

    expect(signalSend.mock.calls.map(([, message]) => message.type)).toEqual(['offer']);
    await vi.advanceTimersByTimeAsync(1_999);
    expect(signalSend).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1);
    await offer;
    await earlyIce;

    expect(signalSend.mock.calls.map(([, message]) => message.type))
      .toEqual(['offer', 'offer', 'ice_candidate']);
    expect(signalSend.mock.calls[1]?.[1]).toEqual(signalSend.mock.calls[0]?.[1]);
    expect(service.status$.value).toBe('connected');
  });

  it('cancels a rate-limited public write without blocking a same-session reconnect', async () => {
    controlPlane.isPublicSession.mockReturnValue(true);
    signalSend.mockReturnValueOnce(throwError(() => ({
      status: 429,
      headers: { get: (name: string) => name === 'Retry-After' ? '5' : null },
    })));
    connect();
    const staleWrite = service.send({
      type: 'offer', session_id: 'session-1', payload: { type: 'offer', sdp: 'old' },
    }).catch(error => error as Error);
    await settleAsyncWork();

    service.disconnect();
    expect(() => service.assertSessionReusable('session-1')).not.toThrow();
    connect();
    expect(service.status$.value).toBe('connected');

    await vi.advanceTimersByTimeAsync(5_000);
    expect((await staleWrite).message).toBe('webrtc_signal_outbox_stale');
    expect(signalSend).toHaveBeenCalledTimes(1);
    expect(service.failureReason$.value).toBeNull();
  });

  it('serializes early ICE behind the server ACK of its delayed offer', async () => {
    const delayedOffer = new Subject<{ ok: true }>();
    signalSend.mockImplementation((_sessionId: string, message: { type: string }) => (
      message.type === 'offer' ? delayedOffer : of({ ok: true })
    ));
    connect();

    const offer = service.send({
      type: 'offer', session_id: 'session-1', payload: { type: 'offer', sdp: 'v=0' },
    });
    const earlyIce = service.send({
      type: 'ice_candidate', session_id: 'session-1', payload: { candidate: 'candidate:1' },
    });
    await Promise.resolve();

    expect(signalSend.mock.calls.map(([, message]) => message.type)).toEqual(['offer']);
    delayedOffer.next({ ok: true });
    delayedOffer.complete();
    await offer;
    await earlyIce;

    expect(signalSend.mock.calls.map(([, message]) => message.type))
      .toEqual(['offer', 'ice_candidate']);
  });

  it('does not ACK a cursor when the awaitable SDP handler rejects', async () => {
    controlPlane.isPublicSession.mockReturnValue(true);
    handler.mockRejectedValueOnce(new Error('set_remote_description_failed'));
    pollResponse = {
      ok: true,
      data: {
        cursor: '1',
        signals: [{
          id: 'signal-1', sequence: '1', type: 'offer', session_id: 'session-1',
          sender_id: 'peer-b', recipient_id: 'peer-a', security_epoch: 2,
          payload: { type: 'offer', sdp: 'bad' },
        }],
      },
    };

    connect();
    await settleAsyncWork();

    expect(handler).toHaveBeenCalledTimes(1);
    expect((service as unknown as { pollCursor: string }).pollCursor).toBe('');
    expect(signalPoll.mock.calls).toEqual([['session-1', '', 2]]);
    expect(service.status$.value).toBe('failed');
    expect(service.failureReason$.value).toBe('public_signaling_session_recreation_required');
    expect((service as unknown as { pollHandle: unknown }).pollHandle).toBeNull();

    connect();
    expect(service.status$.value).toBe('failed');
    expect(service.failureReason$.value).toBe('public_signaling_session_recreation_required');
    expect(signalPoll.mock.calls).toEqual([['session-1', '', 2]]);
  });

  it('resumes a same-peer session from its applied cursor without replaying old SDP', async () => {
    controlPlane.isPublicSession.mockReturnValue(true);
    signalPoll
      .mockReturnValueOnce(of({
        ok: true,
        data: {
          cursor: '1', cursor_floor: '0', truncated: false,
          signals: [{
            id: 'signal-1', type: 'offer', session_id: 'session-1', sender_id: 'peer-b',
            recipient_id: 'peer-a', security_epoch: 2, payload: 'first-offer',
          }],
        },
      }))
      .mockReturnValueOnce(of({
        ok: true,
        data: {
          cursor: '2', cursor_floor: '1', truncated: false,
          signals: [{
            id: 'signal-2', type: 'answer', session_id: 'session-1', sender_id: 'peer-b',
            recipient_id: 'peer-a', security_epoch: 2, payload: 'new-answer',
          }],
        },
      }));

    connect();
    await settleAsyncWork();
    service.disconnect();
    connect();
    await settleAsyncWork();

    expect(signalPoll.mock.calls).toEqual([
      ['session-1', '', 2],
      ['session-1', '1', 2],
    ]);
    expect(handler.mock.calls.map(([message]) => (message as { payload: unknown }).payload))
      .toEqual(['first-offer', 'new-answer']);
    expect((service as unknown as { pollCursor: string }).pollCursor).toBe('2');
    expect(service.status$.value).toBe('connected');
  });

  it('requires a new public session after disconnecting with an ambiguous POST', async () => {
    controlPlane.isPublicSession.mockReturnValue(true);
    const stalePost = new Subject<{ ok: true }>();
    signalSend.mockReturnValueOnce(stalePost).mockReturnValue(of({ ok: true }));
    connect();
    const oldWrite = service.send({
      type: 'offer', session_id: 'session-1', payload: { type: 'offer', sdp: 'old' },
    }).catch(error => error);
    await Promise.resolve();

    service.disconnect();
    expect(() => service.assertSessionReusable('session-1'))
      .toThrow('public_signaling_session_recreation_required');
    connect();
    expect(service.status$.value).toBe('failed');
    expect(service.failureReason$.value).toBe('public_signaling_session_recreation_required');
    stalePost.error(new Error('late_old_generation_failure'));
    await oldWrite;
    await settleAsyncWork();

    expect(service.status$.value).toBe('failed');
    await expect(service.send({
      type: 'offer', session_id: 'session-1', payload: { type: 'offer', sdp: 'new' },
    })).rejects.toThrow('webrtc_signal_context_invalid');
    expect(service.failureReason$.value).toBe('public_signaling_session_recreation_required');

    service.retireSession('session-1');
    expect(service.isSessionRecreationRequired('session-1')).toBe(false);
    service.bindMessageHandler(async () => undefined);
    connect();
    expect(service.status$.value).toBe('connected');
  });

  it('does not let a late old-epoch POST rejection poison the replacement epoch', async () => {
    controlPlane.isPublicSession.mockReturnValue(true);
    const stalePost = new Subject<{ ok: true }>();
    signalSend.mockReturnValueOnce(stalePost);
    connect(2);
    const oldWrite = service.send({
      type: 'offer', session_id: 'session-1', payload: { type: 'offer', sdp: 'old' },
    }).catch(error => error);
    await Promise.resolve();

    service.disconnect();
    connect(3);
    stalePost.error({
      status: 403,
      error: { reason_code: 'membership_capability_retired' },
    });
    await oldWrite;
    await settleAsyncWork();

    expect(service.status$.value).toBe('connected');
    expect(service.failureReason$.value).toBeNull();
    expect(service.isSessionRecreationRequired('session-1', 2)).toBe(true);
    expect(service.isSessionRecreationRequired('session-1', 3)).toBe(false);
  });

  it('stops public signaling when its immutable authority disappears', async () => {
    controlPlane.assertSessionAvailable.mockImplementationOnce(() => undefined);
    connect();
    await settleAsyncWork();
    controlPlane.assertSessionAvailable.mockImplementation(() => {
      throw new Error('public_session_authentication_lost');
    });
    signalPoll.mockImplementation(() => { throw new Error('public_session_authentication_lost'); });

    (service as unknown as { pollSignals(): void }).pollSignals();

    expect(service.status$.value).toBe('failed');
    expect((service as unknown as { pollHandle: unknown }).pollHandle).toBeNull();
  });
});

async function settleAsyncWork(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}
