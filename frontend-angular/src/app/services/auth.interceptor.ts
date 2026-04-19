import { Injectable, inject } from '@angular/core';
import { HttpInterceptor, HttpRequest, HttpHandler, HttpEvent, HttpErrorResponse } from '@angular/common/http';
import { Observable, from, throwError } from 'rxjs';
import { switchMap, catchError } from 'rxjs/operators';
import { AgentDirectoryService } from './agent-directory.service';
import { UserAuthService } from './user-auth.service';
import { AuthTarget, resolveAuthTarget } from './auth-target.resolver';
import { AgentJwtService } from './agent-jwt.service';
import { AuthRefreshCoordinator } from './auth-refresh-coordinator.service';

@Injectable()
export class AuthInterceptor implements HttpInterceptor {
  private dir = inject(AgentDirectoryService);
  private userAuth = inject(UserAuthService);
  private agentJwt = inject(AgentJwtService);
  private refreshCoordinator = inject(AuthRefreshCoordinator);

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
        return this.withRefreshPolicy(authReq, next, target);
      }
      case 'agent_jwt_shared_secret': {
        return from(
          this.agentJwt.createFrontendToken(target.agentSharedSecret!),
        ).pipe(
          switchMap(jwt => next.handle(this.addTokenHeader(req, jwt))),
        );
      }
      case 'passthrough_unknown_target':
      case 'passthrough_no_credentials':
      default:
        return this.withRefreshPolicy(req, next, target);
    }
  }

  private withRefreshPolicy(
    request: HttpRequest<unknown>,
    next: HttpHandler,
    target: AuthTarget,
  ): Observable<HttpEvent<unknown>> {
    return next.handle(request).pipe(
      catchError(error => {
        if (
          target.refreshOnUnauthorized &&
          error instanceof HttpErrorResponse &&
          error.status === 401 &&
          !request.url.includes('/auth/refresh-token') &&
          !request.url.includes('/login')
        ) {
          return this.refreshCoordinator.handleUnauthorized(
            request,
            next,
            (retryRequest, token) => this.addTokenHeader(retryRequest, token),
          );
        }
        return throwError(() => error);
      }),
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
