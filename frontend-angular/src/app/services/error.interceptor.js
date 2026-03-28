var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Injectable, inject } from '@angular/core';
import { throwError } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { NotificationService } from './notification.service';
let ErrorInterceptor = class ErrorInterceptor {
    constructor() {
        this.ns = inject(NotificationService);
    }
    intercept(req, next) {
        return next.handle(req).pipe(catchError((error) => {
            let errorMessage = 'Ein Netzwerkfehler ist aufgetreten.';
            if (error.error instanceof ErrorEvent) {
                // Client-seitiger Fehler
                errorMessage = `Fehler: ${error.error.message}`;
            }
            else {
                // Server-seitiger Fehler
                errorMessage = `API-Fehler (${error.status}): `;
                if (error.error?.error) {
                    errorMessage += error.error.error;
                }
                else if (error.error?.detail) {
                    errorMessage += error.error.detail;
                }
                else if (error.error?.message) {
                    errorMessage += error.error.message;
                }
                else if (error.message) {
                    errorMessage += error.message;
                }
                else {
                    errorMessage += error.statusText;
                }
            }
            // Zentrale Benachrichtigung
            this.ns.error(errorMessage);
            // Fehler weiterreichen, damit Komponenten bei Bedarf noch spezifisch reagieren können
            return throwError(() => error);
        }));
    }
};
ErrorInterceptor = __decorate([
    Injectable()
], ErrorInterceptor);
export { ErrorInterceptor };
//# sourceMappingURL=error.interceptor.js.map