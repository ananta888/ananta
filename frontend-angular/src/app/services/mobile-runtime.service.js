var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Injectable } from '@angular/core';
import { Capacitor } from '@capacitor/core';
import { BehaviorSubject } from 'rxjs';
let MobileRuntimeService = class MobileRuntimeService {
    constructor() {
        this.isNative = Capacitor.isNativePlatform();
        this.online$ = new BehaviorSubject(typeof navigator !== 'undefined' ? navigator.onLine : true);
    }
    init() {
        if (typeof window === 'undefined')
            return;
        window.addEventListener('online', () => this.online$.next(true));
        window.addEventListener('offline', () => this.online$.next(false));
        this.online$.next(navigator.onLine);
    }
    async requestPushPermission() {
        if (typeof Notification === 'undefined')
            return 'denied';
        if (Notification.permission === 'granted')
            return 'granted';
        return Notification.requestPermission();
    }
};
MobileRuntimeService = __decorate([
    Injectable({ providedIn: 'root' })
], MobileRuntimeService);
export { MobileRuntimeService };
//# sourceMappingURL=mobile-runtime.service.js.map