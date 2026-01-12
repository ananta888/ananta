import { Injectable } from '@angular/core';
import { HttpInterceptor, HttpRequest, HttpHandler, HttpEvent } from '@angular/common/http';
import { Observable, from } from 'rxjs';
import { switchMap } from 'rxjs/operators';
import { AgentDirectoryService } from './agent-directory.service';
import { UserAuthService } from './user-auth.service';
import { generateJWT } from '../utils/jwt';

@Injectable()
export class AuthInterceptor implements HttpInterceptor {
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

    if (agent) {
      // Priorität 1: User-JWT für den Hub
      if (agent.role === 'hub' && this.userAuth.token) {
        const authReq = req.clone({
          setHeaders: {
            Authorization: `Bearer ${this.userAuth.token}`
          }
        });
        return next.handle(authReq);
      }

      // Priorität 2: Agent-JWT (Shared Secret) für Worker oder Hub (falls kein User-JWT)
      if (agent.token) {
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

    return next.handle(req);
  }
}
