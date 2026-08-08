import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  OIDC_POPUP_ACK_TIMEOUT_MS,
  OIDC_POPUP_CALLBACK_TIMEOUT_MS,
  OidcPopupCoordinator,
  OidcPopupCoordinatorError,
  OidcPopupParentSession,
} from './oidc-popup-coordinator.service';

const CALLBACK_TYPE = 'ananta.oidc.popup.callback.v1';
const ACK_TYPE = 'ananta.oidc.popup.ack.v1';
const STATE = 'p.abcdefghijklmnopqrst';

type PostedBroadcast = {
  channel: string;
  data: unknown;
};

class BroadcastChannelDouble {
  static readonly instances: BroadcastChannelDouble[] = [];
  static readonly posted: PostedBroadcast[] = [];

  onmessage: ((event: MessageEvent<unknown>) => void) | null = null;
  onmessageerror: ((event: MessageEvent<unknown>) => void) | null = null;
  readonly name: string;
  closed = false;

  constructor(name: string) {
    this.name = name;
    BroadcastChannelDouble.instances.push(this);
  }

  postMessage(data: unknown): void {
    if (this.closed) throw new DOMException('BroadcastChannel is closed.');
    BroadcastChannelDouble.posted.push({ channel: this.name, data });
    for (const peer of BroadcastChannelDouble.instances) {
      if (peer === this || peer.closed || peer.name !== this.name) continue;
      peer.onmessage?.(new MessageEvent('message', { data }));
    }
  }

  close(): void {
    this.closed = true;
  }

  addEventListener(): void {}
  removeEventListener(): void {}
  dispatchEvent(): boolean { return true; }

  static reset(): void {
    this.instances.length = 0;
    this.posted.length = 0;
  }
}

class SilentBroadcastChannelDouble extends BroadcastChannelDouble {
  override postMessage(data: unknown): void {
    if (this.closed) throw new DOMException('BroadcastChannel is closed.');
    BroadcastChannelDouble.posted.push({ channel: this.name, data });
  }
}

function popupDouble(): Window & { postMessage: ReturnType<typeof vi.fn> } {
  return {
    closed: false,
    postMessage: vi.fn(),
  } as unknown as Window & { postMessage: ReturnType<typeof vi.fn> };
}

function callbackMessage(
  state: string,
  value: { status: 'code'; code: string } | { status: 'error'; errorCode: string },
): Record<string, unknown> {
  return {
    version: 1,
    type: CALLBACK_TYPE,
    state,
    ...value,
  };
}

function dispatchWindowMessage(data: unknown, origin: string, source: unknown): void {
  const event = new MessageEvent('message', { data, origin });
  Object.defineProperty(event, 'source', { configurable: true, value: source });
  window.dispatchEvent(event);
}

describe('OidcPopupCoordinator', () => {
  const originalOpener = Object.getOwnPropertyDescriptor(window, 'opener');
  const sessions: OidcPopupParentSession[] = [];
  let coordinator: OidcPopupCoordinator;

  function configure(callbackTimeoutMs = 1_000, acknowledgementTimeoutMs = 1_000): void {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        OidcPopupCoordinator,
        { provide: OIDC_POPUP_CALLBACK_TIMEOUT_MS, useValue: callbackTimeoutMs },
        { provide: OIDC_POPUP_ACK_TIMEOUT_MS, useValue: acknowledgementTimeoutMs },
      ],
    });
    coordinator = TestBed.inject(OidcPopupCoordinator);
  }

  function beginSession(popup = popupDouble(), state = STATE): OidcPopupParentSession {
    const session = coordinator.beginParentSession(state, popup);
    sessions.push(session);
    return session;
  }

  beforeEach(() => {
    BroadcastChannelDouble.reset();
    vi.stubGlobal('BroadcastChannel', BroadcastChannelDouble as unknown as typeof BroadcastChannel);
    Object.defineProperty(window, 'opener', { configurable: true, value: null });
    configure();
  });

  afterEach(() => {
    for (const session of sessions.splice(0)) session.dispose();
    localStorage.clear();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    if (originalOpener) {
      Object.defineProperty(window, 'opener', originalOpener);
    } else {
      delete (window as unknown as Record<string, unknown>)['opener'];
    }
    TestBed.resetTestingModule();
  });

  it('uses a reserved state prefix as the popup-flow discriminator', () => {
    expect(coordinator.createState('abcdefghijklmnopqrst')).toBe(STATE);
    expect(coordinator.isPopupState(STATE)).toBe(true);
    expect(coordinator.isPopupCallback(`?code=one-time-code&state=${STATE}`)).toBe(true);

    expect(coordinator.isPopupState('r.abcdefghijklmnopqrst')).toBe(false);
    expect(coordinator.isPopupState('p.too-short')).toBe(false);
    expect(coordinator.isPopupState('p.abcdefghijklmnopqrst$')).toBe(false);
    expect(coordinator.isPopupState(null)).toBe(false);
    expect(coordinator.isPopupCallback('?code=one-time-code&state=r.abcdefghijklmnopqrst')).toBe(false);
  });

  it('accepts a parent callback only for the exact origin, popup source and state, then deduplicates it', async () => {
    const popup = popupDouble();
    const session = beginSession(popup);

    dispatchWindowMessage(
      callbackMessage(STATE, { status: 'code', code: 'wrong-origin' }),
      'https://attacker.invalid',
      popup,
    );
    dispatchWindowMessage(
      callbackMessage(STATE, { status: 'code', code: 'wrong-source' }),
      location.origin,
      {},
    );
    dispatchWindowMessage(
      callbackMessage('p.zyxwvutsrqponmlkjihg', { status: 'code', code: 'wrong-state' }),
      location.origin,
      popup,
    );
    dispatchWindowMessage(
      callbackMessage(STATE, { status: 'code', code: 'accepted-first' }),
      location.origin,
      popup,
    );
    dispatchWindowMessage(
      callbackMessage(STATE, { status: 'code', code: 'duplicate-second' }),
      location.origin,
      popup,
    );

    await expect(session.result).resolves.toEqual({
      kind: 'code',
      state: STATE,
      code: 'accepted-first',
    });
    session.acknowledge({ ok: true });
  });

  it('cleans up listeners, channel and control state after a successful parent acknowledgement', async () => {
    const removeListener = vi.spyOn(window, 'removeEventListener');
    const popup = popupDouble();
    const session = beginSession(popup);
    const parentChannel = BroadcastChannelDouble.instances[0];
    const controlKey = `ananta.oidc.popup.control.v1.${STATE}`;
    localStorage.setItem(controlKey, 'stale');

    dispatchWindowMessage(
      callbackMessage(STATE, { status: 'code', code: 'authorization-code' }),
      location.origin,
      popup,
    );
    await expect(session.result).resolves.toMatchObject({ kind: 'code', code: 'authorization-code' });
    expect(parentChannel.closed).toBe(false);

    session.acknowledge({ ok: true });

    expect(parentChannel.closed).toBe(true);
    expect(localStorage.getItem(controlKey)).toBeNull();
    expect(removeListener).toHaveBeenCalledWith('message', expect.any(Function));
    expect(removeListener).toHaveBeenCalledWith('storage', expect.any(Function));
    expect(popup.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ type: ACK_TYPE, state: STATE, ok: true }),
      location.origin,
    );
  });

  it('returns a sanitized authorization error and cleans up after a rejected acknowledgement', async () => {
    const popup = popupDouble();
    const session = beginSession(popup);
    const parentChannel = BroadcastChannelDouble.instances[0];

    dispatchWindowMessage(
      callbackMessage(STATE, { status: 'error', errorCode: 'access_denied' }),
      location.origin,
      popup,
    );

    await expect(session.result).resolves.toEqual({
      kind: 'error',
      state: STATE,
      errorCode: 'access_denied',
    });
    session.acknowledge({
      ok: false,
      errorCode: 'authorization_denied',
      message: 'Anmeldung\nwurde abgelehnt.',
    });

    expect(parentChannel.closed).toBe(true);
    expect(popup.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: ACK_TYPE,
        ok: false,
        errorCode: 'authorization_denied',
        message: 'Anmeldung wurde abgelehnt.',
      }),
      location.origin,
    );
  });

  it('rejects a timed-out parent session and releases every transport resource', async () => {
    vi.useFakeTimers();
    configure(50, 1_000);
    const removeListener = vi.spyOn(window, 'removeEventListener');
    const clearTimeout = vi.spyOn(window, 'clearTimeout');
    const session = beginSession();
    const parentChannel = BroadcastChannelDouble.instances[0];
    const controlKey = `ananta.oidc.popup.control.v1.${STATE}`;
    localStorage.setItem(controlKey, 'stale');
    const outcome = session.result.catch(error => error);

    await vi.advanceTimersByTimeAsync(50);

    const error = await outcome;
    expect(error).toBeInstanceOf(OidcPopupCoordinatorError);
    expect(error).toMatchObject({ code: 'popup_timeout' });
    expect(parentChannel.closed).toBe(true);
    expect(localStorage.getItem(controlKey)).toBeNull();
    expect(removeListener).toHaveBeenCalledWith('message', expect.any(Function));
    expect(removeListener).toHaveBeenCalledWith('storage', expect.any(Function));
    expect(clearTimeout).toHaveBeenCalled();
  });

  it('completes an opener-less callback through BroadcastChannel and its acknowledgement', async () => {
    const popup = popupDouble();
    const session = beginSession(popup);
    Object.defineProperty(window, 'opener', { configurable: true, value: null });

    const relay = coordinator.relayCurrentCallback(`?code=authorization-code&state=${STATE}`);
    await expect(session.result).resolves.toEqual({
      kind: 'code',
      state: STATE,
      code: 'authorization-code',
    });
    session.acknowledge({ ok: true });

    await expect(relay).resolves.toBeUndefined();
    expect(BroadcastChannelDouble.instances).toHaveLength(2);
    expect(BroadcastChannelDouble.instances.every(instance => instance.closed)).toBe(true);
  });

  it('rejects the callback when the parent sends a negative acknowledgement and closes both channels', async () => {
    const session = beginSession();
    const relay = coordinator.relayCurrentCallback(`?error=access_denied&state=${STATE}`);
    await expect(session.result).resolves.toMatchObject({
      kind: 'error',
      errorCode: 'access_denied',
    });
    session.acknowledge({ ok: false, errorCode: 'authorization_denied', message: 'Abgelehnt.' });

    const error = await relay.catch(candidate => candidate);
    expect(error).toBeInstanceOf(OidcPopupCoordinatorError);
    expect(error).toMatchObject({ code: 'parent_rejected' });
    expect(BroadcastChannelDouble.instances.every(instance => instance.closed)).toBe(true);
  });

  it('reports a malformed callback to the parent instead of leaving it to time out', async () => {
    const session = beginSession();
    const relay = coordinator.relayCurrentCallback(`?state=${STATE}`);

    await expect(session.result).resolves.toEqual({
      kind: 'error',
      state: STATE,
      errorCode: 'invalid_callback',
    });
    session.acknowledge({ ok: false, errorCode: 'callback_invalid', message: 'Ungültiger Callback.' });

    await expect(relay).rejects.toMatchObject({ code: 'parent_rejected' });
  });

  it('times out an unacknowledged callback and removes its listener and channel', async () => {
    vi.useFakeTimers();
    configure(1_000, 50);
    const removeListener = vi.spyOn(window, 'removeEventListener');
    const clearTimeout = vi.spyOn(window, 'clearTimeout');
    const relay = coordinator.relayCurrentCallback(`?code=authorization-code&state=${STATE}`);
    const outcome = relay.catch(error => error);
    const callbackChannel = BroadcastChannelDouble.instances[0];

    await vi.advanceTimersByTimeAsync(50);

    const error = await outcome;
    expect(error).toBeInstanceOf(OidcPopupCoordinatorError);
    expect(error).toMatchObject({ code: 'ack_timeout' });
    expect(callbackChannel.closed).toBe(true);
    expect(removeListener).toHaveBeenCalledWith('message', expect.any(Function));
    expect(clearTimeout).toHaveBeenCalled();
  });

  it('signals the parent through a state-bound control marker when BroadcastChannel is not delivered', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('BroadcastChannel', SilentBroadcastChannelDouble as unknown as typeof BroadcastChannel);
    configure(1_000, 50);
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    const session = beginSession();
    const parentOutcome = session.result;
    const relayOutcome = coordinator
      .relayCurrentCallback(`?code=authorization-code&state=${STATE}`)
      .catch(error => error);

    await vi.advanceTimersByTimeAsync(50);
    const relayError = await relayOutcome;
    expect(relayError).toMatchObject({ code: 'ack_timeout' });

    const controlWrite = setItem.mock.calls.find(([key]) =>
      String(key).startsWith('ananta.oidc.popup.control.v1.'),
    );
    expect(controlWrite).toBeDefined();
    const [key, newValue] = controlWrite!;
    window.dispatchEvent(new StorageEvent('storage', {
      key: String(key),
      newValue: String(newValue),
      storageArea: localStorage,
      url: location.href,
    }));

    await expect(parentOutcome).resolves.toEqual({
      kind: 'error',
      state: STATE,
      errorCode: 'communication_unavailable',
    });
    session.acknowledge({ ok: false, errorCode: 'popup_communication_failed' });
    expect(String(newValue)).not.toContain('authorization-code');
  });

  it('relays only the one-time code and acknowledgement, never URL tokens or a PKCE verifier', async () => {
    const popup = popupDouble();
    const session = beginSession(popup);
    const query = new URLSearchParams({
      state: STATE,
      code: 'one-time-code',
      access_token: 'access-secret',
      refresh_token: 'refresh-secret',
      code_verifier: 'verifier-secret',
      nonce: 'nonce-secret',
    });

    const relay = coordinator.relayCurrentCallback(`?${query}`);
    await session.result;
    session.acknowledge({ ok: true });
    await relay;

    const broadcastPayloads = BroadcastChannelDouble.posted.map(entry => entry.data);
    const popupPayloads = popup.postMessage.mock.calls.map(call => call[0]);
    expect(broadcastPayloads).toContainEqual({
      version: 1,
      type: CALLBACK_TYPE,
      state: STATE,
      status: 'code',
      code: 'one-time-code',
    });
    expect(broadcastPayloads).toContainEqual({
      version: 1,
      type: ACK_TYPE,
      state: STATE,
      ok: true,
    });

    const serializedMessages = JSON.stringify([...broadcastPayloads, ...popupPayloads]);
    expect(serializedMessages).not.toContain('access-secret');
    expect(serializedMessages).not.toContain('refresh-secret');
    expect(serializedMessages).not.toContain('verifier-secret');
    expect(serializedMessages).not.toContain('nonce-secret');
    expect(serializedMessages).not.toContain('access_token');
    expect(serializedMessages).not.toContain('refresh_token');
    expect(serializedMessages).not.toContain('code_verifier');
  });
});
