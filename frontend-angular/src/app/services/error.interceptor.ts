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
        
        // Fehler weiterreichen, damit Komponenten bei Bedarf noch spezifisch reagieren können
        return throwError(() => error);
      })
    );
  }
}
