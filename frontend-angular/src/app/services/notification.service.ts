import { Injectable } from '@angular/core';
import { Subject } from 'rxjs';

export interface Notification {
  message: string;
  type: 'info' | 'error' | 'success';
  duration?: number;
}

@Injectable({ providedIn: 'root' })
export class NotificationService {
  private notificationSubject = new Subject<Notification>();
  notifications$ = this.notificationSubject.asObservable();

  show(message: string, type: Notification['type'] = 'info', duration: number = 5000) {
    this.notificationSubject.next({ message, type, duration });
  }

  error(message: string) {
    this.show(message, 'error');
  }

  success(message: string) {
    this.show(message, 'success');
  }

  info(message: string) {
    this.show(message, 'info');
  }
}
