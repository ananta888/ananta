import { HttpEvent, HttpHandler, HttpRequest } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, Observable, throwError } from 'rxjs';
import { catchError, filter, switchMap, take } from 'rxjs/operators';

import { UserAuthService } from './user-auth.service';

@Injectable({ providedIn: 'root' })
export class AuthRefreshCoordinator {
  private userAuth = inject(UserAuthService);

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
}
