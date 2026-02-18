import { Component, OnInit, OnDestroy, inject } from '@angular/core';
import { AsyncPipe, NgClass } from '@angular/common';
import { ToastService, ToastMessage } from '../services/toast.service';
import { Subscription } from 'rxjs';

@Component({
  selector: 'app-toast',
  standalone: true,
  imports: [AsyncPipe, NgClass],
  template: `
    <div class="toast-container">
      @for (toast of activeToasts; track toast.id) {
        <div class="toast" [ngClass]="'toast-' + toast.type">
          <span class="toast-icon">{{ iconFor(toast.type) }}</span>
          <span class="toast-message">{{ toast.message }}</span>
          <button class="toast-close" (click)="dismiss(toast.id)" aria-label="Dismiss">&times;</button>
        </div>
      }
    </div>
  `,
  styles: [`
    .toast-container {
      position: fixed;
      top: 60px;
      right: 16px;
      z-index: 9999;
      display: flex;
      flex-direction: column;
      gap: 8px;
      max-width: 400px;
    }
    .toast {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 12px 16px;
      border-radius: 8px;
      background: var(--card-bg);
      border: 1px solid var(--border);
      box-shadow: 0 4px 12px rgba(0,0,0,0.15);
      animation: slideIn 0.2s ease-out;
    }
    @keyframes slideIn {
      from { transform: translateX(100%); opacity: 0; }
      to { transform: translateX(0); opacity: 1; }
    }
    .toast-success { border-left: 4px solid #22c55e; }
    .toast-error { border-left: 4px solid #ef4444; }
    .toast-info { border-left: 4px solid #3b82f6; }
    .toast-warning { border-left: 4px solid #f59e0b; }
    .toast-icon {
      font-size: 16px;
      flex-shrink: 0;
    }
    .toast-message {
      flex: 1;
      font-size: 14px;
    }
    .toast-close {
      background: none;
      border: none;
      font-size: 18px;
      cursor: pointer;
      opacity: 0.6;
      padding: 0 4px;
    }
    .toast-close:hover {
      opacity: 1;
    }
    @media (max-width: 500px) {
      .toast-container {
        left: 8px;
        right: 8px;
        max-width: none;
      }
    }
  `]
})
export class ToastComponent implements OnInit, OnDestroy {
  private toastService = inject(ToastService);
  
  activeToasts: Array<ToastMessage & { id: number }> = [];
  private counter = 0;
  private sub?: Subscription;

  ngOnInit() {
    this.sub = this.toastService.toasts$.subscribe(toast => {
      const id = ++this.counter;
      this.activeToasts.push({ ...toast, id });
      
      const duration = toast.duration ?? 3000;
      if (duration > 0) {
        setTimeout(() => this.dismiss(id), duration);
      }
    });
  }

  ngOnDestroy() {
    this.sub?.unsubscribe();
  }

  dismiss(id: number) {
    this.activeToasts = this.activeToasts.filter(t => t.id !== id);
  }

  iconFor(type: ToastMessage['type']): string {
    switch (type) {
      case 'success': return '✓';
      case 'error': return '✕';
      case 'warning': return '⚠';
      case 'info': 
      default: return 'ℹ';
    }
  }
}
