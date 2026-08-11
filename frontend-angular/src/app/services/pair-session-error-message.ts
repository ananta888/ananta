import { rateLimitMessage } from './http-rate-limit';

const PUBLIC_PAIR_ERROR_MESSAGES: Readonly<Record<string, string>> = Object.freeze({
  public_session_authentication_required:
    'Bitte zuerst im Login-Tab bei Keycloak anmelden.',
  public_session_authentication_lost:
    'Die Keycloak-Anmeldung wurde beendet. Bitte erneut anmelden.',
  public_session_authentication_expired:
    'Die Keycloak-Anmeldung ist abgelaufen. Bitte im Login-Tab erneut anmelden.',
  public_session_authentication_not_yet_valid:
    'Die Keycloak-Anmeldung ist noch nicht gültig. Bitte die Systemzeit prüfen und erneut anmelden.',
  public_session_authentication_invalid:
    'Die gespeicherte Keycloak-Anmeldung ist ungültig. Bitte im Login-Tab erneut anmelden.',
  public_rendezvous_profile_untrusted:
    'Das öffentliche WebRTC-Profil stimmt nicht mit der sicheren Konfiguration überein.',
  public_session_profile_changed:
    'Das Netzwerkprofil wurde während der Session geändert. Bitte eine neue Session erstellen.',
  public_pair_pending_attempt_conflict:
    'Ein früherer Beitrittsversuch ist noch ungeklärt. Verwirf ihn nur, wenn er sicher nicht erfolgreich war.',
  peer_identity_must_be_distinct:
    'Ein Gerät kann nicht mit sich selbst verbunden werden. Verwende auf dem zweiten Rechner dessen eigene Geräteidentität.',
  device_key_must_be_distinct:
    'Beide Pair-Teilnehmer verwenden denselben Geräteschlüssel. Nutze auf dem zweiten Rechner ein eigenes Browserprofil.',
});

export function pairSessionErrorCode(error: unknown): string {
  const serverCode = (error as { error?: { error?: unknown } } | null)?.error?.error;
  if (typeof serverCode === 'string') return serverCode;
  if (error instanceof Error) return error.message;
  return typeof error === 'string' ? error : '';
}

export function pairSessionErrorMessage(error: unknown, fallback: string): string {
  const limited = rateLimitMessage(error);
  if (limited) return limited;
  const code = pairSessionErrorCode(error);
  return PUBLIC_PAIR_ERROR_MESSAGES[code] ?? (code || fallback);
}
