var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Injectable } from '@angular/core';
import { Subject } from 'rxjs';
let ToastService = class ToastService {
    constructor() {
        this.toastSubject = new Subject();
    }
    get toasts$() {
        return this.toastSubject.asObservable();
    }
    success(message, duration = 3000) {
        this.toastSubject.next({ type: 'success', message, duration });
    }
    error(message, duration = 5000) {
        this.toastSubject.next({ type: 'error', message, duration });
    }
    info(message, duration = 3000) {
        this.toastSubject.next({ type: 'info', message, duration });
    }
    warning(message, duration = 4000) {
        this.toastSubject.next({ type: 'warning', message, duration });
    }
};
ToastService = __decorate([
    Injectable({ providedIn: 'root' })
], ToastService);
export { ToastService };
//# sourceMappingURL=toast.service.js.map