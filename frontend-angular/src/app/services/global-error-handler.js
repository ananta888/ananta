var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Injectable, Injector, inject } from '@angular/core';
import { NotificationService } from './notification.service';
import { HttpErrorResponse } from '@angular/common/http';
let GlobalErrorHandler = class GlobalErrorHandler {
    constructor() {
        this.injector = inject(Injector);
    }
    handleError(error) {
        const ns = this.injector.get(NotificationService);
        let message = 'Ein unerwarteter Fehler ist aufgetreten.';
        if (error instanceof HttpErrorResponse) {
            message = `API-Fehler: ${error.status} ${error.statusText}`;
            if (error.error?.error) {
                message += ` - ${error.error.error}`;
            }
            else if (error.error?.detail) {
                message += ` - ${error.error.detail}`;
            }
            else if (error.error?.message) {
                message += ` - ${error.error.message}`;
            }
            else if (typeof error.error === 'string' && error.error.length < 100) {
                message += ` - ${error.error}`;
            }
        }
        else if (error instanceof Error) {
            message = error.message;
        }
        else if (typeof error === 'string') {
            message = error;
        }
        console.error('Global Error Handler:', error);
        ns.error(message);
    }
};
GlobalErrorHandler = __decorate([
    Injectable()
], GlobalErrorHandler);
export { GlobalErrorHandler };
//# sourceMappingURL=global-error-handler.js.map