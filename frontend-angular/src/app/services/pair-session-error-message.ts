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
});

export function pairSessionErrorMessage(error: unknown, fallback: string): string {
  const code = error instanceof Error
    ? error.message
    : typeof error === 'string'
      ? error
      : '';
  return PUBLIC_PAIR_ERROR_MESSAGES[code] ?? (code || fallback);
}
