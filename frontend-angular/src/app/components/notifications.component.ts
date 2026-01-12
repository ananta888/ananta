import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { NotificationService, Notification } from '../services/notification.service';

@Component({
  selector: 'app-notifications',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="notification-container">
      <div *ngFor="let n of activeNotifications" 
           [class]="'notification ' + n.type"
           (click)="remove(n)">
        {{ n.message }}
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
      cursor: pointer;
      box-shadow: 0 2px 10px rgba(0,0,0,0.2);
      min-width: 200px;
      max-width: 400px;
      animation: slideIn 0.3s ease-out;
    }
    .info { background-color: #2196F3; }
    .error { background-color: #F44336; }
    .success { background-color: #4CAF50; }
    @keyframes slideIn {
      from { transform: translateX(100%); opacity: 0; }
      to { transform: translateX(0); opacity: 1; }
    }
  `]
})
export class NotificationsComponent implements OnInit {
  activeNotifications: Notification[] = [];

  constructor(private ns: NotificationService) {}

  ngOnInit() {
    this.ns.notifications$.subscribe(n => {
      this.activeNotifications.push(n);
      if (n.duration !== 0) {
        setTimeout(() => this.remove(n), n.duration || 5000);
      }
    });
  }

  remove(n: Notification) {
    this.activeNotifications = this.activeNotifications.filter(item => item !== n);
  }
}
