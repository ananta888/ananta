import { TestBed } from '@angular/core/testing';
import { Observable, Subject, of, throwError } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import { SignalingStatus, WebrtcSignalingService } from './webrtc-signaling.service';

describe('WebrtcSignalingService authenticated cursor signaling', () => {
  let service: WebrtcSignalingService;
  let pollResponse: unknown;
  let handler: ReturnType<typeof vi.fn<(message: unknown) => Promise<void>>>;
  const signalPoll = vi.fn<(sessionId: string, cursor: string) => Observable<unknown>>();
  const signalSend = vi.fn();
  const controlPlane = {
    signalPoll,
    signalSend,
    assertSessionAvailable: vi.fn(),
    peerIdForSession: vi.fn(() => 'peer-a'),
    isPublicSession: vi.fn(() => false),
  };

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
    service.connect('', 'session-1', 'peer-b');
    service.hardDisconnect();
    expect(() => service.hardDisconnect()).not.toThrow();
    expect(service.status$.value).toBe('disconnected');
    expect((service as unknown as { pollHandle: unknown }).pollHandle).toBeNull();
  });

  it('binds the verified remote peer to every signal', async () => {
    service.connect('', 'session-1', 'peer-b');
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

  it('fails closed before polling or sending without an exact recipient id', async () => {
    service.connect('', 'session-1');
    await expect(service.send({
      type: 'offer', session_id: 'session-1', payload: { sdp: 'v=0' },
    })).rejects.toThrow('webrtc_signal_context_invalid');

    expect(service.status$.value).toBe('failed');
    expect(signalSend).not.toHaveBeenCalled();
    expect(signalPoll).not.toHaveBeenCalled();
  });

  it('deduplicates public signals and requires their id and addressed recipient', async () => {
    controlPlane.isPublicSession.mockReturnValue(true);
    const accepted = {
      id: 'signal-1', type: 'offer', session_id: 'session-1', sender_id: 'peer-b',
      recipient_id: 'peer-a', payload: 'accepted',
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

    service.connect('', 'session-1', 'peer-b');
    await settleAsyncWork();
    (service as unknown as { pollSignals(): void }).pollSignals();

    expect(observed).toEqual(['accepted']);
    expect(handler).toHaveBeenCalledTimes(1);
    expect(signalPoll.mock.calls).toEqual([['session-1', ''], ['session-1', '2']]);
  });

  it('keeps only one signaling poll in flight', async () => {
    const pending = new Subject<unknown>();
    signalPoll.mockReturnValue(pending);
    service.connect('', 'session-1', 'peer-b');

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

    service.connect('', 'session-1', 'peer-b');
    await settleAsyncWork();

    expect(service.status$.value).toBe('failed');
    expect(observed).toEqual([]);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('retries a transient poll failure with the unchanged cursor', () => {
    signalPoll.mockReturnValueOnce(throwError(() => ({ status: 503 })));
    service.connect('', 'session-1', 'peer-b');
    signalPoll.mockReturnValueOnce(of({ ok: true, data: { signals: [], cursor: '1' } }));

    (service as unknown as { pollSignals(): void }).pollSignals();

    expect(service.status$.value).toBe('connected');
    expect(signalPoll.mock.calls).toEqual([['session-1', ''], ['session-1', '']]);
  });

  it('fails closed when the signaling queue rejects an outbound signal', async () => {
    service.connect('', 'session-1', 'peer-b');
    signalSend.mockReturnValue(throwError(() => ({ status: 429, error: { error: 'queue_full' } })));

    await expect(service.send({
      type: 'offer', session_id: 'session-1', payload: { sdp: 'v=0' },
    })).rejects.toBeTruthy();

    expect(service.status$.value).toBe('failed');
    expect((service as unknown as { pollHandle: unknown }).pollHandle).toBeNull();
  });

  it('serializes early ICE behind the server ACK of its delayed offer', async () => {
    const delayedOffer = new Subject<{ ok: true }>();
    signalSend.mockImplementation((_sessionId: string, message: { type: string }) => (
      message.type === 'offer' ? delayedOffer : of({ ok: true })
    ));
    service.connect('', 'session-1', 'peer-b');

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
          sender_id: 'peer-b', recipient_id: 'peer-a', payload: { type: 'offer', sdp: 'bad' },
        }],
      },
    };

    service.connect('', 'session-1', 'peer-b');
    await settleAsyncWork();

    expect(handler).toHaveBeenCalledTimes(1);
    expect((service as unknown as { pollCursor: string }).pollCursor).toBe('');
    expect(signalPoll.mock.calls).toEqual([['session-1', '']]);
    expect(service.status$.value).toBe('failed');
    expect(vi.getTimerCount()).toBe(0);
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
            recipient_id: 'peer-a', payload: 'first-offer',
          }],
        },
      }))
      .mockReturnValueOnce(of({
        ok: true,
        data: {
          cursor: '2', cursor_floor: '1', truncated: false,
          signals: [{
            id: 'signal-2', type: 'answer', session_id: 'session-1', sender_id: 'peer-b',
            recipient_id: 'peer-a', payload: 'new-answer',
          }],
        },
      }));

    service.connect('', 'session-1', 'peer-b');
    await settleAsyncWork();
    service.disconnect();
    service.connect('', 'session-1', 'peer-b');
    await settleAsyncWork();

    expect(signalPoll.mock.calls).toEqual([
      ['session-1', ''],
      ['session-1', '1'],
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
    service.connect('', 'session-1', 'peer-b');
    const oldWrite = service.send({
      type: 'offer', session_id: 'session-1', payload: { type: 'offer', sdp: 'old' },
    }).catch(error => error);
    await Promise.resolve();

    service.disconnect();
    service.connect('', 'session-1', 'peer-b');
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
  });

  it('stops public signaling when its immutable authority disappears', async () => {
    controlPlane.assertSessionAvailable.mockImplementationOnce(() => undefined);
    service.connect('', 'session-1', 'peer-b');
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
