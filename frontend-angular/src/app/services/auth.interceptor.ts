import { Injectable, inject } from '@angular/core';
import { HttpInterceptor, HttpRequest, HttpHandler, HttpEvent, HttpErrorResponse } from '@angular/common/http';
import { Observable, from, throwError, BehaviorSubject } from 'rxjs';
import { switchMap, catchError, filter, take } from 'rxjs/operators';
import { AgentDirectoryService } from './agent-directory.service';
import { UserAuthService } from './user-auth.service';
import { AuthTarget, resolveAuthTarget } from './auth-target.resolver';
import { generateJWT } from '../utils/jwt';

@Injectable()
export class AuthInterceptor implements HttpInterceptor {
  private dir = inject(AgentDirectoryService);
  private userAuth = inject(UserAuthService);

  private isRefreshing = false;
  private refreshTokenSubject: BehaviorSubject<string | null> = new BehaviorSubject<string | null>(null);

  intercept(req: HttpRequest<unknown>, next: HttpHandler): Observable<HttpEvent<unknown>> {
    if (req.headers.has('Authorization')) {
      return next.handle(req);
    }

    const target = resolveAuthTarget({
      agents: this.dir.list(),
      userToken: this.userAuth.token || null,
      requestUrl: req.url,
    });

    return this.dispatchByTarget(req, next, target);
  }

  private dispatchByTarget(
    req: HttpRequest<unknown>,
    next: HttpHandler,
    target: AuthTarget,
  ): Observable<HttpEvent<unknown>> {
    switch (target.kind) {
      case 'hub_user_bearer':
      case 'user_bearer_fallback_on_worker': {
        const authReq = this.addTokenHeader(req, target.userToken!);
        return this.withRefreshOn401(authReq, next);
      }
      case 'agent_jwt_shared_secret': {
        return from(
          generateJWT(
            { sub: 'frontend', iat: Math.floor(Date.now() / 1000) },
            target.agentSharedSecret!,
          ),
        ).pipe(
          switchMap(jwt => next.handle(this.addTokenHeader(req, jwt))),
        );
      }
      case 'passthrough_unknown_target':
      case 'passthrough_no_credentials':
      default:
        return this.withRefreshOn401(req, next);
    }
  }

  private withRefreshOn401(
    request: HttpRequest<unknown>,
    next: HttpHandler,
  ): Observable<HttpEvent<unknown>> {
    return next.handle(request).pipe(
      catchError(error => {
        if (
          error instanceof HttpErrorResponse &&
          error.status === 401 &&
          !request.url.includes('/auth/refresh-token') &&
          !request.url.includes('/login')
        ) {
          return this.handle401Error(request, next);
        }
        return throwError(() => error);
      }),
    );
  }

  private handle401Error(request: HttpRequest<unknown>, next: HttpHandler) {
    if (!this.isRefreshing) {
      this.isRefreshing = true;
      this.refreshTokenSubject.next(null);

      return this.userAuth.refreshToken().pipe(
        switchMap((res: any) => {
          this.isRefreshing = false;
          this.refreshTokenSubject.next(res.access_token);
          return next.handle(this.addTokenHeader(request, res.access_token));
        }),
        catchError((err) => {
          this.isRefreshing = false;
          this.userAuth.logout();
          return throwError(() => err);
        }),
      );
    }

    return this.refreshTokenSubject.pipe(
      filter((token): token is string => token !== null),
      take(1),
      switchMap(token => next.handle(this.addTokenHeader(request, token))),
    );
  }

  private addTokenHeader(request: HttpRequest<unknown>, token: string): HttpRequest<unknown> {
    return request.clone({
      setHeaders: {
        Authorization: `Bearer ${token}`,
      },
    });
  }
}
