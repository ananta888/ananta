import { HttpEvent, HttpHandler, HttpRequest } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, Observable, defer, finalize, shareReplay, throwError } from 'rxjs';
import { catchError, switchMap, tap } from 'rxjs/operators';

import { UserAuthService } from './user-auth.service';
import { isDefinitiveHubRefreshFailure } from './hub-refresh-error';

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

  private refreshInFlight$: Observable<{ access_token: string; refresh_token?: string }> | null = null;
  private failedRefresh: Observable<{ access_token: string; refresh_token?: string }> | null = null;

  handleUnauthorized(
    request: HttpRequest<unknown>,
    next: HttpHandler,
    applyToken: (request: HttpRequest<unknown>, token: string) => HttpRequest<unknown>,
  ): Observable<HttpEvent<unknown>> {
    const refresh = this.refreshAccessToken();
    const guardedRefresh = refresh.pipe(
      tap(() => {
        if (this.failedRefresh === refresh) this.failedRefresh = null;
      }),
      catchError((err) => {
        if (isDefinitiveHubRefreshFailure(err) && this.failedRefresh !== refresh) {
          this.failedRefresh = refresh;
          // A shared failed refresh may reject many concurrent 401 requests;
          // authentication state and logout are changed exactly once.
          this.requireAuthentication('hub');
          this.userAuth.logoutHub();
        }
        return throwError(() => err);
      }),
    );
    // Retry failures propagate as ordinary request failures. Only the refresh
    // source above is allowed to change authentication state.
    return guardedRefresh.pipe(
      switchMap((res: { access_token: string }) => (
        next.handle(applyToken(request, res.access_token))
      )),
    );
  }

  requireAuthentication(sphere: 'hub' | 'oidc'): void {
    this.authRequired$.next(sphere);
  }

  private refreshAccessToken(): Observable<{ access_token: string; refresh_token?: string }> {
    if (this.refreshInFlight$) return this.refreshInFlight$;
    let shared!: Observable<{ access_token: string; refresh_token?: string }>;
    const operation = defer(() => this.userAuth.refreshToken()).pipe(
      finalize(() => {
        if (this.refreshInFlight$ === shared) this.refreshInFlight$ = null;
      }),
    );
    shared = operation.pipe(shareReplay({ bufferSize: 1, refCount: false }));
    this.refreshInFlight$ = shared;
    return shared;
  }
}
