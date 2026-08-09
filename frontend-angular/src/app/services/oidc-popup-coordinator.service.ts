import { Injectable, InjectionToken, inject } from '@angular/core';

const POPUP_STATE_PREFIX = 'p.';
const CALLBACK_MESSAGE_TYPE = 'ananta.oidc.popup.callback.v1';
const ACK_MESSAGE_TYPE = 'ananta.oidc.popup.ack.v1';
const CONTROL_MESSAGE_TYPE = 'ananta.oidc.popup.control.v1';
const CHANNEL_PREFIX = 'ananta.oidc.popup.v1.';
const CONTROL_STORAGE_PREFIX = 'ananta.oidc.popup.control.v1.';

export function isOidcPopupState(value: string | null | undefined): value is string {
  return typeof value === 'string'
    && value.startsWith(POPUP_STATE_PREFIX)
    && value.length >= POPUP_STATE_PREFIX.length + 20
    && /^[A-Za-z0-9._-]+$/.test(value);
}

export function isOidcPopupCallbackSearch(search = location.search): boolean {
  return isOidcPopupState(new URLSearchParams(search).get('state'));
}

export function isOidcPopupCallbackLocation(
  pathname = location.pathname,
  search = location.search,
): boolean {
  return pathname === '/oidc-callback' && isOidcPopupCallbackSearch(search);
}

export const OIDC_POPUP_CALLBACK_TIMEOUT_MS = new InjectionToken<number>(
  'OIDC_POPUP_CALLBACK_TIMEOUT_MS',
  { providedIn: 'root', factory: () => 5 * 60_000 },
);

export const OIDC_POPUP_ACK_TIMEOUT_MS = new InjectionToken<number>(
  'OIDC_POPUP_ACK_TIMEOUT_MS',
  { providedIn: 'root', factory: () => 60_000 },
);

export type OidcPopupAuthorizationResult =
  | { kind: 'code'; state: string; code: string }
  | { kind: 'error'; state: string; errorCode: string };

export interface OidcPopupAcknowledgement {
  ok: boolean;
  errorCode?: string;
  message?: string;
}

export interface OidcPopupParentSession {
  readonly result: Promise<OidcPopupAuthorizationResult>;
  acknowledge(result: OidcPopupAcknowledgement): void;
  dispose(): void;
}

type PopupCallbackMessage = {
  version: 1;
  type: typeof CALLBACK_MESSAGE_TYPE;
  state: string;
  status: 'code' | 'error';
  code?: string;
  errorCode?: string;
};

type PopupAckMessage = {
  version: 1;
  type: typeof ACK_MESSAGE_TYPE;
  state: string;
  ok: boolean;
  errorCode?: string;
  message?: string;
};

type PopupControlMessage = {
  version: 1;
  type: typeof CONTROL_MESSAGE_TYPE;
  state: string;
  status: 'communication_unavailable';
  createdAt: number;
};

export type OidcPopupCoordinatorFailure =
  | 'invalid_state'
  | 'invalid_callback'
  | 'popup_timeout'
  | 'communication_unavailable'
  | 'ack_timeout'
  | 'parent_rejected';

export class OidcPopupCoordinatorError extends Error {
  override readonly name = 'OidcPopupCoordinatorError';

  constructor(
    readonly code: OidcPopupCoordinatorFailure,
    message: string,
  ) {
    super(message);
  }
}

/**
 * Owns the browser-window transport for one OIDC popup transaction.
 *
 * The channel deliberately transports only the one-time authorization code,
 * never access/refresh tokens, PKCE verifiers, or nonces. The parent keeps the
 * PKCE material in memory and remains the sole owner of the token exchange.
 */
@Injectable({ providedIn: 'root' })
export class OidcPopupCoordinator {
  private readonly callbackTimeoutMs = inject(OIDC_POPUP_CALLBACK_TIMEOUT_MS);
  private readonly acknowledgementTimeoutMs = inject(OIDC_POPUP_ACK_TIMEOUT_MS);

  createState(randomValue: string): string {
    return `${POPUP_STATE_PREFIX}${randomValue}`;
  }

  isPopupState(value: string | null | undefined): value is string {
    return isOidcPopupState(value);
  }

  isPopupCallback(search = location.search): boolean {
    return isOidcPopupCallbackSearch(search);
  }

  beginParentSession(state: string, popup: Window): OidcPopupParentSession {
    if (!this.isPopupState(state)) {
      throw new OidcPopupCoordinatorError('invalid_state', 'Ungültiger OIDC-Popup-Zustand.');
    }
    return new BrowserOidcPopupParentSession(state, popup, this.callbackTimeoutMs);
  }

  async relayCurrentCallback(search = location.search): Promise<void> {
    const params = new URLSearchParams(search);
    const state = params.get('state');
    if (!this.isPopupState(state)) {
      throw new OidcPopupCoordinatorError('invalid_state', 'Ungültiger OIDC-Popup-Zustand.');
    }

    const oauthError = normalizeProtocolValue(params.get('error'));
    const code = params.get('code');
    let callback: PopupCallbackMessage;
    if (oauthError) {
      callback = {
        version: 1,
        type: CALLBACK_MESSAGE_TYPE,
        state,
        status: 'error',
        errorCode: oauthError,
      };
    } else if (code && code.length <= 8_192) {
      callback = {
        version: 1,
        type: CALLBACK_MESSAGE_TYPE,
        state,
        status: 'code',
        code,
      };
    } else {
      callback = {
        version: 1,
        type: CALLBACK_MESSAGE_TYPE,
        state,
        status: 'error',
        errorCode: 'invalid_callback',
      };
    }

    await this.sendCallbackAndAwaitAcknowledgement(callback);
  }

  private sendCallbackAndAwaitAcknowledgement(callback: PopupCallbackMessage): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      const expectedOrigin = location.origin;
      const opener = window.opener;
      let channel: BroadcastChannel | null = null;
      let settled = false;
      let timeoutHandle: number | undefined;

      const cleanup = () => {
        if (timeoutHandle !== undefined) window.clearTimeout(timeoutHandle);
        window.removeEventListener('message', onWindowMessage);
        channel?.close();
      };

      const settle = (ack: PopupAckMessage) => {
        if (settled) return;
        settled = true;
        cleanup();
        if (ack.ok) {
          resolve();
          return;
        }
        reject(new OidcPopupCoordinatorError(
          'parent_rejected',
          ack.message || 'Das Hauptfenster konnte die Keycloak-Anmeldung nicht abschließen.',
        ));
      };

      const onWindowMessage = (event: MessageEvent<unknown>) => {
        if (event.origin !== expectedOrigin || event.source !== opener) return;
        const ack = parseAckMessage(event.data, callback.state);
        if (ack) settle(ack);
      };

      window.addEventListener('message', onWindowMessage);

      let sent = false;
      if (typeof BroadcastChannel !== 'undefined') {
        try {
          channel = new BroadcastChannel(channelName(callback.state));
          channel.onmessage = (event: MessageEvent<unknown>) => {
            const ack = parseAckMessage(event.data, callback.state);
            if (ack) settle(ack);
          };
          channel.postMessage(callback);
          sent = true;
        } catch {
          channel?.close();
          channel = null;
        }
      }

      if (opener) {
        try {
          opener.postMessage(callback, expectedOrigin);
          sent = true;
        } catch {
          // BroadcastChannel remains the COOP-safe transport when available.
        }
      }

      if (!sent) {
        publishCommunicationFailure(callback.state);
        settled = true;
        cleanup();
        reject(new OidcPopupCoordinatorError(
          'communication_unavailable',
          'Das Callback-Fenster kann das Hauptfenster nicht erreichen. Bitte die Anmeldung erneut starten.',
        ));
        return;
      }

      if (settled) return;
      timeoutHandle = window.setTimeout(() => {
        if (settled) return;
        publishCommunicationFailure(callback.state);
        settled = true;
        cleanup();
        reject(new OidcPopupCoordinatorError(
          'ack_timeout',
          'Das Hauptfenster hat den OIDC-Callback nicht rechtzeitig bestätigt.',
        ));
      }, this.acknowledgementTimeoutMs);
    });
  }
}

class BrowserOidcPopupParentSession implements OidcPopupParentSession {
  readonly result: Promise<OidcPopupAuthorizationResult>;

  private readonly expectedOrigin = location.origin;
  private readonly channel: BroadcastChannel | null;
  private readonly timeoutHandle: number;
  private settled = false;
  private disposed = false;
  private resolveResult!: (value: OidcPopupAuthorizationResult) => void;
  private rejectResult!: (reason: OidcPopupCoordinatorError) => void;

  constructor(
    private readonly state: string,
    private readonly popup: Window,
    timeoutMs: number,
  ) {
    this.result = new Promise<OidcPopupAuthorizationResult>((resolve, reject) => {
      this.resolveResult = resolve;
      this.rejectResult = reject;
    });

    window.addEventListener('message', this.onWindowMessage);
    window.addEventListener('storage', this.onStorage);

    let channel: BroadcastChannel | null = null;
    if (typeof BroadcastChannel !== 'undefined') {
      try {
        channel = new BroadcastChannel(channelName(state));
        channel.onmessage = (event: MessageEvent<unknown>) => {
          this.acceptCallback(event.data);
        };
      } catch {
        channel?.close();
        channel = null;
      }
    }
    this.channel = channel;

    this.timeoutHandle = window.setTimeout(() => {
      if (this.settled || this.disposed) return;
      this.settled = true;
      this.rejectResult(new OidcPopupCoordinatorError(
        'popup_timeout',
        'Die Keycloak-Anmeldung wurde nicht innerhalb von fünf Minuten abgeschlossen.',
      ));
      this.dispose();
    }, timeoutMs);
  }

  acknowledge(result: OidcPopupAcknowledgement): void {
    if (this.disposed) return;
    const errorCode = normalizeProtocolValue(result.errorCode);
    const ack: PopupAckMessage = {
      version: 1,
      type: ACK_MESSAGE_TYPE,
      state: this.state,
      ok: result.ok,
      ...(errorCode ? { errorCode } : {}),
      ...(result.message ? { message: sanitizeMessage(result.message) } : {}),
    };
    try { this.channel?.postMessage(ack); } catch { /* postMessage fallback below */ }
    try { this.popup.postMessage(ack, this.expectedOrigin); } catch { /* COOP can sever the proxy. */ }
    this.dispose();
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    window.clearTimeout(this.timeoutHandle);
    window.removeEventListener('message', this.onWindowMessage);
    window.removeEventListener('storage', this.onStorage);
    this.channel?.close();
    removeControlState(this.state);
  }

  private readonly onWindowMessage = (event: MessageEvent<unknown>) => {
    if (event.origin !== this.expectedOrigin || event.source !== this.popup) return;
    this.acceptCallback(event.data);
  };

  private readonly onStorage = (event: StorageEvent) => {
    if (event.key !== controlStorageKey(this.state) || !event.newValue) return;
    if (event.storageArea && event.storageArea !== localStorage) return;
    if (event.url) {
      try {
        if (new URL(event.url).origin !== this.expectedOrigin) return;
      } catch {
        return;
      }
    }
    const control = parseControlMessage(event.newValue, this.state);
    if (!control) return;
    this.acceptCallback({
      version: 1,
      type: CALLBACK_MESSAGE_TYPE,
      state: this.state,
      status: 'error',
      errorCode: control.status,
    });
  };

  private acceptCallback(value: unknown): void {
    if (this.settled || this.disposed) return;
    const callback = parseCallbackMessage(value, this.state);
    if (!callback) return;
    this.settled = true;
    window.clearTimeout(this.timeoutHandle);
    if (callback.status === 'code') {
      this.resolveResult({ kind: 'code', state: this.state, code: callback.code! });
    } else {
      this.resolveResult({
        kind: 'error',
        state: this.state,
        errorCode: callback.errorCode || 'callback_error',
      });
    }
  }
}

function parseCallbackMessage(value: unknown, expectedState: string): PopupCallbackMessage | null {
  if (!isRecord(value)
      || value['version'] !== 1
      || value['type'] !== CALLBACK_MESSAGE_TYPE
      || value['state'] !== expectedState
      || (value['status'] !== 'code' && value['status'] !== 'error')) {
    return null;
  }
  if (value['status'] === 'code') {
    const code = value['code'];
    if (typeof code !== 'string' || !code) return null;
    return { version: 1, type: CALLBACK_MESSAGE_TYPE, state: expectedState, status: 'code', code };
  }
  const errorCode = normalizeProtocolValue(value['errorCode']);
  if (!errorCode) return null;
  return { version: 1, type: CALLBACK_MESSAGE_TYPE, state: expectedState, status: 'error', errorCode };
}

function parseAckMessage(value: unknown, expectedState: string): PopupAckMessage | null {
  if (!isRecord(value)
      || value['version'] !== 1
      || value['type'] !== ACK_MESSAGE_TYPE
      || value['state'] !== expectedState
      || typeof value['ok'] !== 'boolean') {
    return null;
  }
  const errorCode = normalizeProtocolValue(value['errorCode']);
  return {
    version: 1,
    type: ACK_MESSAGE_TYPE,
    state: expectedState,
    ok: value['ok'],
    ...(errorCode ? { errorCode } : {}),
    ...(typeof value['message'] === 'string' ? { message: sanitizeMessage(value['message']) } : {}),
  };
}

function parseControlMessage(value: string, expectedState: string): PopupControlMessage | null {
  try {
    const parsed: unknown = JSON.parse(value);
    if (!isRecord(parsed)
        || parsed['version'] !== 1
        || parsed['type'] !== CONTROL_MESSAGE_TYPE
        || parsed['state'] !== expectedState
        || parsed['status'] !== 'communication_unavailable'
        || typeof parsed['createdAt'] !== 'number') {
      return null;
    }
    return parsed as PopupControlMessage;
  } catch {
    return null;
  }
}

function publishCommunicationFailure(state: string): void {
  const control: PopupControlMessage = {
    version: 1,
    type: CONTROL_MESSAGE_TYPE,
    state,
    status: 'communication_unavailable',
    createdAt: Date.now(),
  };
  try {
    const key = controlStorageKey(state);
    localStorage.setItem(key, JSON.stringify(control));
    // The storage event keeps the set operation's newValue even though the
    // short-lived control marker is removed immediately afterwards.
    removeControlState(state);
  } catch {
    // The callback still reports the failure locally when storage is unavailable.
  }
}

function removeControlState(state: string): void {
  try { localStorage.removeItem(controlStorageKey(state)); } catch { /* Cleanup is best-effort. */ }
}

function channelName(state: string): string {
  return `${CHANNEL_PREFIX}${state}`;
}

function controlStorageKey(state: string): string {
  return `${CONTROL_STORAGE_PREFIX}${state}`;
}

function normalizeProtocolValue(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const normalized = value.trim();
  return /^[A-Za-z0-9._-]{1,80}$/.test(normalized) ? normalized : null;
}

function sanitizeMessage(value: string): string {
  return value.replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, 240);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
