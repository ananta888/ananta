import { Injectable, inject } from '@angular/core';
import { HttpInterceptor, HttpRequest, HttpHandler, HttpEvent, HttpErrorResponse } from '@angular/common/http';
import { Observable, throwError } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { NotificationService } from './notification.service';

@Injectable()
export class ErrorInterceptor implements HttpInterceptor {
  private ns = inject(NotificationService);


  intercept(req: HttpRequest<any>, next: HttpHandler): Observable<HttpEvent<any>> {
    return next.handle(req).pipe(
      catchError((error: HttpErrorResponse) => {
        // 401 errors are handled centrally by AuthInterceptor (refresh/logout flow)
        // and/or by local login UI; avoid noisy transient overlays for users.
        if (error.status === 401) {
          (error as any).__anantaHandledByInterceptor = true;
          return throwError(() => error);
        }

        // Polling/navigation-related GET aborts frequently surface as "status 0 / Unknown Error".
        // Showing a global error toast for those transient reads makes the UI noisy and flaky.
        if (error.status === 0 && req.method === 'GET') {
          (error as any).__anantaHandledByInterceptor = true;
          return throwError(() => error);
        }

        // 403 on GET requests = background read / config check (e.g. /v1/models, chat/messages poll).
        // These are not user-action failures; individual services handle them locally.
        if (error.status === 403 && req.method === 'GET') {
          (error as any).__anantaHandledByInterceptor = true;
          return throwError(() => error);
        }

        // Snake-session endpoints (/snakes/...) handle 403/404 locally via isSessionGoneError
        // and show contextual messages. A redundant global toast here is pure noise.
        if (error.status === 403 && req.url.includes('/snakes/')) {
          (error as any).__anantaHandledByInterceptor = true;
          return throwError(() => error);
        }

        let errorMessage = 'Ein Netzwerkfehler ist aufgetreten.';
        
        if (error.error instanceof ErrorEvent) {
          // Client-seitiger Fehler
          errorMessage = `Fehler: ${error.error.message}`;
        } else {
          // Server-seitiger Fehler
          errorMessage = `API-Fehler (${error.status}): `;
          if (error.error?.error) {
            errorMessage += error.error.error;
          } else if (error.error?.detail) {
            errorMessage += error.error.detail;
          } else if (error.error?.message) {
            errorMessage += error.error.message;
          } else if (error.message) {
            errorMessage += error.message;
          } else {
            errorMessage += error.statusText;
          }
        }
        
        // Zentrale Benachrichtigung
        this.ns.error(errorMessage);
        (error as any).__anantaHandledByInterceptor = true;
        
        // Fehler weiterreichen, damit Komponenten bei Bedarf noch spezifisch reagieren können
        return throwError(() => error);
      })
    );
  }
}
