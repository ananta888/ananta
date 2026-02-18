import { Injectable, inject } from '@angular/core';
import { Subject, Observable } from 'rxjs';

export interface ToastMessage {
  type: 'success' | 'error' | 'info' | 'warning';
  message: string;
  duration?: number;
}

@Injectable({ providedIn: 'root' })
export class ToastService {
  private toastSubject = new Subject<ToastMessage>();
  
  get toasts$(): Observable<ToastMessage> {
    return this.toastSubject.asObservable();
  }

  success(message: string, duration = 3000) {
    this.toastSubject.next({ type: 'success', message, duration });
  }

  error(message: string, duration = 5000) {
    this.toastSubject.next({ type: 'error', message, duration });
  }

  info(message: string, duration = 3000) {
    this.toastSubject.next({ type: 'info', message, duration });
  }

  warning(message: string, duration = 4000) {
    this.toastSubject.next({ type: 'warning', message, duration });
  }
}
