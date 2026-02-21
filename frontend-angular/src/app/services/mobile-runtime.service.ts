import { Injectable } from '@angular/core';
import { Capacitor } from '@capacitor/core';
import { BehaviorSubject } from 'rxjs';

@Injectable({ providedIn: 'root' })
export class MobileRuntimeService {
  readonly isNative = Capacitor.isNativePlatform();
  readonly online$ = new BehaviorSubject<boolean>(typeof navigator !== 'undefined' ? navigator.onLine : true);

  init(): void {
    if (typeof window === 'undefined') return;
    window.addEventListener('online', () => this.online$.next(true));
    window.addEventListener('offline', () => this.online$.next(false));
    this.online$.next(navigator.onLine);
  }

  async requestPushPermission(): Promise<'granted' | 'denied' | 'default'> {
    if (typeof Notification === 'undefined') return 'denied';
    if (Notification.permission === 'granted') return 'granted';
    return Notification.requestPermission();
  }
}
