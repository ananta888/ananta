import { InjectionToken } from '@angular/core';

export const SFU_ALLOW_INSECURE_LOCALHOST_TRANSPORT = new InjectionToken<boolean>(
  'SFU_ALLOW_INSECURE_LOCALHOST_TRANSPORT',
  {
    providedIn: 'root',
    factory: () => (globalThis as typeof globalThis & {
      __ANANTA_SFU_ALLOW_INSECURE_LOCALHOST__?: unknown;
    }).__ANANTA_SFU_ALLOW_INSECURE_LOCALHOST__ === true,
  },
);

export function isAllowedSfuEndpoint(
  value: string,
  kind: 'http' | 'websocket',
  allowInsecureLocalhost: boolean,
): boolean {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return false;
  }
  if (url.username || url.password) return false;
  const secureProtocol = kind === 'http' ? 'https:' : 'wss:';
  if (url.protocol === secureProtocol) return true;
  const insecureProtocol = kind === 'http' ? 'http:' : 'ws:';
  return allowInsecureLocalhost
    && url.protocol === insecureProtocol
    && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
}
