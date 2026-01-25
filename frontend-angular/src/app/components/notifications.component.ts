import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { NotificationService, Notification } from '../services/notification.service';

@Component({
  selector: 'app-notifications',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="notification-container">
      <div *ngFor="let n of activeNotifications; trackBy: trackById"
           [class]="'notification ' + n.type"
           (mouseenter)="pause(n)"
           (mouseleave)="resume(n)">
        <div class="notification-header">
          <span class="notification-title">{{ labels[n.type] }}</span>
          <button class="notification-close" (click)="remove(n)">x</button>
        </div>
        <div class="notification-message">{{ n.message }}</div>
        <div class="notification-progress" *ngIf="n.duration && n.duration > 0">
          <span [style.animationDuration.ms]="n.duration"></span>
        </div>
      </div>
    </div>
  `,
  styles: [`
    .notification-container {
      position: fixed;
      top: 20px;
      right: 20px;
      z-index: 9999;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .notification {
      padding: 12px 20px;
      border-radius: 4px;
      color: white;
      box-shadow: 0 2px 10px rgba(0,0,0,0.2);
      min-width: 200px;
      max-width: 400px;
      animation: slideIn 0.3s ease-out;
      position: relative;
      overflow: hidden;
    }
    .notification-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      margin-bottom: 4px;
    }
    .notification-title { opacity: 0.9; }
    .notification-message { font-size: 14px; }
    .notification-close {
      background: transparent;
      border: none;
      color: white;
      cursor: pointer;
      font-size: 12px;
      padding: 0;
    }
    .notification-progress {
      position: absolute;
      left: 0;
      bottom: 0;
      height: 3px;
      width: 100%;
      background: rgba(255,255,255,0.2);
    }
    .notification-progress span {
      display: block;
      height: 100%;
      background: rgba(255,255,255,0.6);
      animation-name: progress;
      animation-timing-function: linear;
      animation-fill-mode: forwards;
    }
    .info { background-color: #2196F3; }
    .error { background-color: #F44336; }
    .success { background-color: #4CAF50; }
    @keyframes slideIn {
      from { transform: translateX(100%); opacity: 0; }
      to { transform: translateX(0); opacity: 1; }
    }
    @keyframes progress {
      from { width: 100%; }
      to { width: 0%; }
    }
  `]
})
export class NotificationsComponent implements OnInit {
  activeNotifications: (Notification & { timeoutId?: any })[] = [];
  labels: Record<Notification['type'], string> = {
    info: 'Info',
    error: 'Error',
    success: 'Success'
  };

  constructor(private ns: NotificationService) {}

  ngOnInit() {
    this.ns.notifications$.subscribe(n => {
      this.activeNotifications = [...this.activeNotifications, n];
      if (n.duration !== 0) {
        const timeoutId = setTimeout(() => this.remove(n), n.duration || 5000);
        n.timeoutId = timeoutId;
      }
    });
  }

  remove(n: Notification) {
    if ((n as any).timeoutId) clearTimeout((n as any).timeoutId);
    this.activeNotifications = this.activeNotifications.filter(item => item.id !== n.id);
  }

  pause(n: Notification & { timeoutId?: any }) {
    if (n.timeoutId) {
      clearTimeout(n.timeoutId);
      n.timeoutId = undefined;
    }
  }

  resume(n: Notification & { timeoutId?: any }) {
    if (n.duration && n.duration > 0) {
      n.timeoutId = setTimeout(() => this.remove(n), n.duration);
    }
  }

  trackById(_: number, n: Notification) {
    return n.id;
  }
}
