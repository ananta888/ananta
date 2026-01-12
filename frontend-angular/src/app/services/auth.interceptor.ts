import { Injectable } from '@angular/core';
import { HttpInterceptor, HttpRequest, HttpHandler, HttpEvent, HttpErrorResponse } from '@angular/common/http';
import { Observable, from, throwError, BehaviorSubject } from 'rxjs';
import { switchMap, catchError, filter, take } from 'rxjs/operators';
import { AgentDirectoryService } from './agent-directory.service';
import { UserAuthService } from './user-auth.service';
import { generateJWT } from '../utils/jwt';

@Injectable()
export class AuthInterceptor implements HttpInterceptor {
  private isRefreshing = false;
  private refreshTokenSubject: BehaviorSubject<any> = new BehaviorSubject<any>(null);

  constructor(
    private dir: AgentDirectoryService,
    private userAuth: UserAuthService
  ) {}

  intercept(req: HttpRequest<any>, next: HttpHandler): Observable<HttpEvent<any>> {
    // Falls bereits ein Authorization Header gesetzt ist, nichts tun
    if (req.headers.has('Authorization')) {
      return next.handle(req);
    }

    const agents = this.dir.list();
    // Finde den Agenten, dessen URL der Anfang der Request-URL ist
    const agent = agents.find(a => req.url.startsWith(a.url));

    let request = req;

    if (agent) {
      // Priorität 1: User-JWT für den Hub
      if (agent.role === 'hub' && this.userAuth.token) {
        request = req.clone({
          setHeaders: {
            Authorization: `Bearer ${this.userAuth.token}`
          }
        });
      }
      // Priorität 2: Agent-JWT (Shared Secret) für Worker oder Hub (falls kein User-JWT)
      else if (agent.token) {
        return from(generateJWT({ sub: 'frontend', iat: Math.floor(Date.now()/1000) }, agent.token)).pipe(
          switchMap(jwt => {
            const authReq = req.clone({
              setHeaders: {
                Authorization: `Bearer ${jwt}`
              }
            });
            return next.handle(authReq);
          })
        );
      }
    }

    return next.handle(request).pipe(
      catchError(error => {
        if (error instanceof HttpErrorResponse && error.status === 401 && !req.url.includes('/auth/refresh-token') && !req.url.includes('/login')) {
          return this.handle401Error(request, next);
        }
        return throwError(() => error);
      })
    );
  }

  private handle401Error(request: HttpRequest<any>, next: HttpHandler) {
    if (!this.isRefreshing) {
      this.isRefreshing = true;
      this.refreshTokenSubject.next(null);

      return this.userAuth.refreshToken().pipe(
        switchMap((res: any) => {
          this.isRefreshing = false;
          this.refreshTokenSubject.next(res.token);
          return next.handle(this.addTokenHeader(request, res.token));
        }),
        catchError((err) => {
          this.isRefreshing = false;
          this.userAuth.logout();
          return throwError(() => err);
        })
      );
    }

    return this.refreshTokenSubject.pipe(
      filter(token => token !== null),
      take(1),
      switchMap((token) => next.handle(this.addTokenHeader(request, token)))
    );
  }

  private addTokenHeader(request: HttpRequest<any>, token: string) {
    return request.clone({
      setHeaders: {
        Authorization: `Bearer ${token}`
      }
    });
  }
}
