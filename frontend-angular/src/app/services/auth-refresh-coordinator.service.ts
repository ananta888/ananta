import { HttpEvent, HttpHandler, HttpRequest } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, Observable, firstValueFrom, throwError } from 'rxjs';
import { catchError, filter, switchMap, take } from 'rxjs/operators';

import { UserAuthService } from './user-auth.service';

@Injectable({ providedIn: 'root' })
export class AuthRefreshCoordinator {
  private userAuth = inject(UserAuthService);

  /**
   * Welle 6: emits when a 401 could not be recovered by any refresh
   * strategy and the user must re-authenticate. The LoginComponent
   * listens to this to show the appropriate login mask.
   * `sphere` tells which sphere failed: 'hub' (Hub-direct login) or
   * 'oidc' (Keycloak/OIDC login).
   */
  readonly authRequired$ = new BehaviorSubject<'hub' | 'oidc' | null>(null);

  /** T13: Pro-active token refresh — call from AppComponent or interceptor. */
  startSilentRefreshTimer(): void {
    setInterval(async () => {
      const token = this.userAuth.token;
      if (!token) return;
      try {
        const parts = token.split('.');
        if (parts.length !== 3) return;
        const payload = JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')));
        const expiry = Number(payload.exp) * 1000;
        if (expiry - Date.now() < 60_000) {
          await firstValueFrom(this.userAuth.refreshToken());
        }
      } catch { /* ignore */ }
    }, 30_000);
  }

  private isRefreshing = false;
  private refreshTokenSubject = new BehaviorSubject<string | null>(null);

  handleUnauthorized(
    request: HttpRequest<unknown>,
    next: HttpHandler,
    applyToken: (request: HttpRequest<unknown>, token: string) => HttpRequest<unknown>,
  ): Observable<HttpEvent<unknown>> {
    if (!this.isRefreshing) {
      this.isRefreshing = true;
      this.refreshTokenSubject.next(null);

      return this.userAuth.refreshToken().pipe(
        switchMap((res: { access_token: string }) => {
          this.isRefreshing = false;
          this.refreshTokenSubject.next(res.access_token);
          return next.handle(applyToken(request, res.access_token));
        }),
        catchError((err) => {
          this.isRefreshing = false;
          // Welle 6: refresh failed → tell the UI to show the login mask.
          // Which mask depends on which sphere is active.
          this.requireAuthentication('hub');
          this.userAuth.logout();
          return throwError(() => err);
        }),
      );
    }

    return this.refreshTokenSubject.pipe(
      filter((token): token is string => token !== null),
      take(1),
      switchMap(token => next.handle(applyToken(request, token))),
    );
  }

  requireAuthentication(sphere: 'hub' | 'oidc'): void {
    this.authRequired$.next(sphere);
  }
}
