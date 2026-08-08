import { Injectable, inject } from '@angular/core';
import { Observable, map, of } from 'rxjs';

import { AgentDirectoryService } from './agent-directory.service';
import { HubApiCoreService } from './hub-api-core.service';
import { NetworkProfileService } from './network-profile.service';
import { UserAuthService } from './user-auth.service';

interface ApiEnvelope<T> { ok?: boolean; data?: T; session?: T }

/**
 * Selects the control plane for a Pair session.
 *
 * Public OIDC sessions use the low-data rendezvous boundary. Local/legacy
 * sessions retain the Hub API for backwards compatibility. Payload traffic
 * never passes through this adapter.
 */
@Injectable({ providedIn: 'root' })
export class PairSessionControlPlaneService {
  private readonly core = inject(HubApiCoreService);
  private readonly directory = inject(AgentDirectoryService);
  private readonly profiles = inject(NetworkProfileService);
  private readonly auth = inject(UserAuthService);

  get isPublic(): boolean {
    return !!this.publicBaseUrl && !!this.auth.oidcAccessTokenValue;
  }

  get currentPeerId(): string {
    if (this.isPublic) {
      const claims = decodeJwtPayload(this.auth.oidcAccessTokenValue);
      return String(claims?.['preferred_username'] || claims?.['email'] || claims?.['sub'] || '');
    }
    const claims = this.auth.userPayload;
    return String(claims?.sub || claims?.username || '');
  }

  get signalingUrl(): string {
    return this.profiles.current.rendezvous?.signaling_url || this.profiles.current.signaling_url || '';
  }

  create<T>(body: Record<string, unknown>): Observable<T> {
    if (!this.isPublic) {
      return this.core.post<ApiEnvelope<T>>(`${this.hubBaseUrl}/share-sessions`, body, this.hubBaseUrl).pipe(
        map(response => response.session ?? response.data as T),
      );
    }
    const publicBody = {
      ...body,
      owner_device_id: this.deviceId,
      owner_device_fingerprint: body['public_key_fingerprint'],
      allowed_permissions: body['permissions'],
    };
    return this.publicPost<ApiEnvelope<T>>('/rendezvous/sessions', publicBody).pipe(
      map(response => response.session ?? response.data as T),
    );
  }

  join<T>(body: Record<string, unknown>): Observable<T> {
    if (!this.isPublic) {
      return this.core.post<ApiEnvelope<T>>(`${this.hubBaseUrl}/share-sessions/join-by-code`, body, this.hubBaseUrl).pipe(
        map(response => response.session ?? response.data as T),
      );
    }
    return this.publicPost<ApiEnvelope<T>>('/rendezvous/sessions/join', {
      ...body,
      device_id: this.deviceId,
      device_fingerprint: body['public_key_fingerprint'],
    }).pipe(map(response => response.session ?? response.data as T));
  }

  participants<T>(sessionId: string): Observable<T> {
    const path = this.isPublic
      ? `/rendezvous/sessions/${encodeURIComponent(sessionId)}/participants`
      : `/share-sessions/${encodeURIComponent(sessionId)}/participants`;
    return this.get<T>(path);
  }

  heartbeat(sessionId: string): Observable<unknown> {
    // Public participant listing already refreshes presence. Avoid a second
    // request solely for heartbeat traffic.
    if (this.isPublic) return of({ ok: true });
    return this.post(`/share-sessions/${encodeURIComponent(sessionId)}/heartbeat`, {});
  }

  end(sessionId: string): Observable<unknown> {
    const path = this.isPublic
      ? `/rendezvous/sessions/${encodeURIComponent(sessionId)}`
      : `/share-sessions/${encodeURIComponent(sessionId)}`;
    const base = this.baseUrl;
    return this.core.delete(`${base}${path}`, base, this.token);
  }

  securityGet<T>(sessionId: string, suffix: string): Observable<T> {
    const root = this.isPublic ? '/rendezvous/sessions' : '/share-sessions';
    return this.get<T>(`${root}/${encodeURIComponent(sessionId)}/security/${suffix}`);
  }

  securityPost<T>(sessionId: string, suffix: string, body: unknown): Observable<T> {
    const root = this.isPublic ? '/rendezvous/sessions' : '/share-sessions';
    return this.post<T>(`${root}/${encodeURIComponent(sessionId)}/security/${suffix}`, body);
  }

  signalPoll<T>(sessionId: string, cursor: string): Observable<T> {
    const path = this.isPublic
      ? `/webrtc/sessions/${encodeURIComponent(sessionId)}/signal`
      : `/api/webrtc/sessions/${encodeURIComponent(sessionId)}/signal?since=${encodeURIComponent(cursor)}`;
    return this.get<T>(path);
  }

  signalSend<T>(sessionId: string, body: unknown): Observable<T> {
    return this.post<T>(`/${this.isPublic ? 'webrtc' : 'api/webrtc'}/sessions/${encodeURIComponent(sessionId)}/signal`, body);
  }

  turnCredentials(): Observable<{
    username: string; password: string; ttl: number; uris: string[];
  } | null> {
    if (!this.isPublic) return of(null);
    return this.get<ApiEnvelope<{
      username: string; password: string; ttl: number; uris: string[];
    }>>('/rendezvous/turn-credentials').pipe(map(response => response.data ?? null));
  }

  private get<T>(path: string): Observable<T> {
    const base = this.baseUrl;
    return this.core.get<T>(`${base}${path}`, base, this.token);
  }

  private post<T>(path: string, body: unknown): Observable<T> {
    const base = this.baseUrl;
    return this.core.post<T>(`${base}${path}`, body, base, this.token);
  }

  private publicPost<T>(path: string, body: unknown): Observable<T> {
    return this.core.post<T>(`${this.publicBaseUrl}${path}`, body, this.publicBaseUrl, this.token);
  }

  private get baseUrl(): string { return this.isPublic ? this.publicBaseUrl : this.hubBaseUrl; }
  private get token(): string | undefined { return this.isPublic ? this.auth.oidcAccessTokenValue || undefined : undefined; }
  private get publicBaseUrl(): string { return String(this.profiles.current.rendezvous?.base_url || '').replace(/\/$/, ''); }
  private get hubBaseUrl(): string {
    return String(this.directory.list().find(agent => agent.role === 'hub')?.url || '').replace(/\/$/, '');
  }

  private get deviceId(): string {
    const key = 'ananta.pair-device-id.v1';
    try {
      const existing = localStorage.getItem(key);
      if (existing) return existing;
      const created = crypto.randomUUID ? crypto.randomUUID() : `device-${Date.now()}`;
      localStorage.setItem(key, created);
      return created;
    } catch {
      return `device-${this.currentPeerId || 'browser'}`;
    }
  }
}

function decodeJwtPayload(token: string | null): Record<string, unknown> | null {
  if (!token) return null;
  try {
    const encoded = token.split('.')[1];
    if (!encoded) return null;
    const normalized = encoded.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(encoded.length / 4) * 4, '=');
    return JSON.parse(atob(normalized)) as Record<string, unknown>;
  } catch {
    return null;
  }
}
