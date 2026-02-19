import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable, map, retry, timeout, timer } from 'rxjs';
import { AgentDirectoryService } from './agent-directory.service';
import { UserAuthService } from './user-auth.service';
import { generateJWT } from '../utils/jwt';

@Injectable({ providedIn: 'root' })
export class HubApiCoreService {
  private http = inject(HttpClient);
  private dir = inject(AgentDirectoryService);
  private userAuth = inject(UserAuthService);

  readonly timeoutMs = 15000;
  readonly retryCount = 2;
  private cache = new Map<string, { ts: number; data: any }>();

  getExponentialBackoff(initialDelay: number = 2000, maxDelay: number = 60000) {
    return {
      delay: (_error: any, retryCount: number) => {
        const delay = Math.min(initialDelay * Math.pow(2, retryCount - 1), maxDelay);
        return timer(delay);
      }
    };
  }

  getHeaders(baseUrl: string, token?: string) {
    let headers = new HttpHeaders();
    if (!token) {
      const hub = this.dir.list().find(a => a.role === 'hub');
      if (hub && baseUrl.startsWith(hub.url) && this.userAuth.token) {
        token = this.userAuth.token;
      } else {
        const agent = this.dir.list().find(a => baseUrl.startsWith(a.url));
        token = agent?.token;
      }
    }
    if (token) headers = headers.set('Authorization', `Bearer ${token}`);
    return { headers };
  }

  unwrapResponse<T>(obs: Observable<T>): Observable<T> {
    return obs.pipe(
      map((response: any) => {
        let current = response;
        for (let i = 0; i < 4; i += 1) {
          if (current && typeof current === 'object' && 'status' in current && 'data' in current) {
            current = current.data;
            continue;
          }
          break;
        }
        return current;
      })
    );
  }

  get<T>(url: string, baseUrl: string, token?: string, useRetry = true, timeoutMs?: number): Observable<T> {
    const call = this.http.get<T>(url, this.getHeaders(baseUrl, token)).pipe(timeout(timeoutMs ?? this.timeoutMs));
    return this.unwrapResponse(useRetry ? call.pipe(retry(this.retryCount)) : call);
  }

  post<T>(url: string, body: any, baseUrl: string, token?: string, useRetry = false, timeoutMs?: number): Observable<T> {
    const call = this.http.post<T>(url, body, this.getHeaders(baseUrl, token)).pipe(timeout(timeoutMs ?? this.timeoutMs));
    return this.unwrapResponse(useRetry ? call.pipe(retry(this.retryCount)) : call);
  }

  patch<T>(url: string, body: any, baseUrl: string, token?: string, timeoutMs?: number): Observable<T> {
    return this.unwrapResponse(this.http.patch<T>(url, body, this.getHeaders(baseUrl, token)).pipe(timeout(timeoutMs ?? this.timeoutMs)));
  }

  delete<T>(url: string, baseUrl: string, token?: string, timeoutMs?: number): Observable<T> {
    return this.unwrapResponse(this.http.delete<T>(url, this.getHeaders(baseUrl, token)).pipe(timeout(timeoutMs ?? this.timeoutMs)));
  }

  cacheGet(baseUrl: string, key: string, ttlMs: number) {
    const entry = this.cache.get(`${baseUrl}|${key}`);
    if (!entry) return null;
    if (Date.now() - entry.ts > ttlMs) return null;
    return entry.data;
  }

  cacheSet(baseUrl: string, key: string, data: any) {
    this.cache.set(`${baseUrl}|${key}`, { ts: Date.now(), data });
  }

  streamTaskLogs(baseUrl: string, id: string, token?: string): Observable<any> {
    return new Observable(observer => {
      let urlStr = `${baseUrl}/tasks/${id}/stream-logs`;
      if (!token) token = this.dir.list().find(a => urlStr.startsWith(a.url))?.token;
      if (token) urlStr += (urlStr.includes('?') ? '&' : '?') + `token=${encodeURIComponent(token)}`;
      const eventSource = new EventSource(urlStr);
      eventSource.onmessage = (event) => {
        try { observer.next(JSON.parse(event.data)); } catch {}
      };
      eventSource.onerror = (error) => {
        if (eventSource.readyState === EventSource.CLOSED) observer.complete();
        else observer.error(error);
      };
      return () => eventSource.close();
    }).pipe(retry(this.getExponentialBackoff()));
  }

  streamSystemEvents(baseUrl: string, token?: string): Observable<any> {
    return new Observable(observer => {
      let urlStr = `${baseUrl}/api/system/events`;
      let eventSource: EventSource | null = null;
      let closed = false;

      (async () => {
        let resolvedToken = token;
        const hub = this.dir.list().find(a => a.role === 'hub');
        const isHubEvents = !!hub && urlStr.startsWith(hub.url);
        if (!resolvedToken) {
          if (isHubEvents) resolvedToken = this.userAuth.token || undefined;
          else {
            const agent = this.dir.list().find(a => urlStr.startsWith(a.url));
            if (agent?.token) resolvedToken = await generateJWT({ sub: 'frontend', iat: Math.floor(Date.now() / 1000) }, agent.token);
          }
        }
        if (!resolvedToken) {
          observer.error(new Error('System events require an authenticated token'));
          return;
        }
        urlStr += (urlStr.includes('?') ? '&' : '?') + `token=${encodeURIComponent(resolvedToken)}`;
        if (closed) return;
        eventSource = new EventSource(urlStr);
        eventSource.onmessage = (event) => {
          try { observer.next(JSON.parse(event.data)); } catch {}
        };
        eventSource.onerror = (error) => observer.error(error);
      })().catch((error) => observer.error(error));

      return () => {
        closed = true;
        if (eventSource) eventSource.close();
      };
    }).pipe(retry(this.getExponentialBackoff()));
  }
}
