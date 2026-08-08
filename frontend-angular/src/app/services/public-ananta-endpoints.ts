// Bootstrap fallback used before the protected Hub profile endpoint is
// available. Explicit E2E environments override this through their profile.
export const PUBLIC_OIDC_REALM = 'ananta';
export const PUBLIC_OIDC_ISSUER = `https://keycloak.ananta.de/realms/${PUBLIC_OIDC_REALM}`;
export const PUBLIC_KEYCLOAK_BASE_URL = 'https://keycloak.ananta.de';
export const PUBLIC_OIDC_CLIENT_ID = 'ananta-tui';
export const PUBLIC_OIDC_AUTHORIZATION_ENDPOINT = `${PUBLIC_OIDC_ISSUER}/protocol/openid-connect/auth`;
export const PUBLIC_OIDC_TOKEN_ENDPOINT = `${PUBLIC_OIDC_ISSUER}/protocol/openid-connect/token`;
export const PUBLIC_OIDC_DEVICE_AUTHORIZATION_ENDPOINT = `${PUBLIC_OIDC_ISSUER}/protocol/openid-connect/auth/device`;
export const PUBLIC_OIDC_END_SESSION_ENDPOINT = `${PUBLIC_OIDC_ISSUER}/protocol/openid-connect/logout`;
export const PUBLIC_WEBRTC_BASE_URL = 'https://webrtc.ananta.de';
export const PUBLIC_WEBRTC_SIGNALING_URL = 'wss://webrtc.ananta.de/signaling';
export const PUBLIC_WEBRTC_STUN_URL = 'stun:webrtc.ananta.de:3478';
export const PUBLIC_WEBRTC_TURN_URL = 'turn:webrtc.ananta.de:3478';
export const PUBLIC_RENDEZVOUS_SIGNING_KEY_ID = 'rv:796c1b35f1815ef88b439c40';
export const PUBLIC_RENDEZVOUS_SIGNING_PUBLIC_KEY_B64 = '09/I+N/xxwdvU3OShyMJJtam1LhCq86BXOvwBkjfJvc=';
