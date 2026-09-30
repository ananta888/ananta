/**
 * Stable, user-facing failure contract of the OIDC popup login and the pure
 * mapping of authorization/coordinator/transport failures onto it.
 */
import { OidcPopupCoordinatorError } from './oidc-popup-coordinator.service';

export type OidcPopupLoginFailure =
  | 'popup_blocked'
  | 'configuration_missing'
  | 'popup_closed'
  | 'popup_timeout'
  | 'popup_communication_failed'
  | 'authorization_denied'
  | 'callback_invalid'
  | 'token_exchange_failed'
  | 'token_exchange_timeout'
  | 'token_endpoint_unreachable'
  | 'nonce_mismatch'
  | 'issuer_unreachable'
  | 'popup_start_failed';

/** Stable, user-facing failure contract for callers that render login feedback. */
export class OidcPopupLoginError extends Error {
  override readonly name = 'OidcPopupLoginError';

  constructor(
    readonly code: OidcPopupLoginFailure,
    message: string,
    options?: ErrorOptions,
  ) {
    super(message, options);
  }
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException
    ? error.name === 'AbortError'
    : error instanceof Error && error.name === 'AbortError';
}

export function oidcAuthorizationError(errorCode: string): OidcPopupLoginError {
  if (errorCode === 'access_denied') {
    return new OidcPopupLoginError(
      'authorization_denied',
      'Die Keycloak-Anmeldung wurde abgebrochen oder abgelehnt.',
    );
  }
  if (errorCode === 'communication_unavailable') {
    return new OidcPopupLoginError(
      'popup_communication_failed',
      'Das Callback-Fenster konnte das Hauptfenster nicht sicher erreichen.',
    );
  }
  return new OidcPopupLoginError(
    'callback_invalid',
    'Keycloak konnte die Popup-Anmeldung nicht abschließen. Bitte erneut anmelden.',
  );
}

export function normalizeOidcPopupError(error: unknown, callbackReceived: boolean): OidcPopupLoginError {
  if (error instanceof OidcPopupLoginError) return error;
  if (error instanceof OidcPopupCoordinatorError) {
    if (error.code === 'popup_timeout') {
      return new OidcPopupLoginError('popup_timeout', error.message, { cause: error });
    }
    if (error.code === 'communication_unavailable') {
      return new OidcPopupLoginError('popup_communication_failed', error.message, { cause: error });
    }
    return new OidcPopupLoginError('callback_invalid', error.message, { cause: error });
  }
  return new OidcPopupLoginError(
    callbackReceived ? 'token_exchange_failed' : 'popup_start_failed',
    callbackReceived
      ? 'Die Keycloak-Anmeldung konnte nicht abgeschlossen werden. Bitte erneut anmelden.'
      : 'Das Keycloak-Anmeldefenster konnte nicht initialisiert werden. Bitte die Anmeldung erneut starten.',
    error instanceof Error ? { cause: error } : undefined,
  );
}
