var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Injectable } from '@angular/core';
import { Subject } from 'rxjs';
let NotificationService = class NotificationService {
    constructor() {
        this.notificationSubject = new Subject();
        this.notifications$ = this.notificationSubject.asObservable();
    }
    show(message, type = 'info', duration = 5000) {
        const id = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
        this.notificationSubject.next({ id, message, type, duration });
    }
    error(message) {
        this.show(message, 'error');
    }
    success(message) {
        this.show(message, 'success');
    }
    info(message) {
        this.show(message, 'info');
    }
    fromApiError(error, fallback) {
        const message = error?.error?.message || error?.error?.error || error?.message;
        if (typeof message === 'string' && message.trim())
            return message.trim();
        return fallback;
    }
};
NotificationService = __decorate([
    Injectable({ providedIn: 'root' })
], NotificationService);
export { NotificationService };
//# sourceMappingURL=notification.service.js.map