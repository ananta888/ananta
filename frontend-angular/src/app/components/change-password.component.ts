import { Component, inject } from '@angular/core';

import { FormsModule } from '@angular/forms';
import { UserAuthService } from '../services/user-auth.service';

@Component({
  selector: 'app-change-password',
  standalone: true,
  imports: [FormsModule],
  template: `
    <div class="card">
      <h3>Passwort ändern</h3>
      @if (success) {
        <div class="success-msg">Passwort erfolgreich geändert!</div>
      }
      @if (error) {
        <div class="error-msg">{{error}}</div>
      }
    
      <form (submit)="onSubmit($event)">
        <div class="form-group">
          <label>Altes Passwort</label>
          <input type="password" [(ngModel)]="oldPassword" name="oldPassword" required>
        </div>
        <div class="form-group">
          <label>Neues Passwort</label>
          <input type="password" [(ngModel)]="newPassword" name="newPassword" required>
        </div>
        <div class="form-group">
          <label>Neues Passwort bestätigen</label>
          <input type="password" [(ngModel)]="confirmPassword" name="confirmPassword" required>
        </div>
    
        <button type="submit" [disabled]="loading || !isValid()" class="primary">
          {{ loading ? 'Wird geändert...' : 'Passwort ändern' }}
        </button>
      </form>
    </div>
    `,
  styles: [`
    .success-msg { color: #28a745; margin-bottom: 16px; padding: 8px; background: #e9f7ef; border-radius: 4px; }
    .error-msg { color: #dc3545; margin-bottom: 16px; padding: 8px; background: #fdeaea; border-radius: 4px; }
    .form-group { margin-bottom: 12px; }
    label { display: block; margin-bottom: 4px; }
    input { width: 100%; padding: 8px; border: 1px solid #ccc; border-radius: 4px; }
    button { margin-top: 8px; }
  `]
})
export class ChangePasswordComponent {
  private auth = inject(UserAuthService);

  oldPassword = '';
  newPassword = '';
  confirmPassword = '';
  loading = false;
  error = '';
  success = false;

  isValid() {
    return this.oldPassword && this.newPassword && this.newPassword === this.confirmPassword && this.newPassword.length >= 4;
  }

  onSubmit(e: Event) {
    e.preventDefault();
    if (!this.isValid()) return;

    this.loading = true;
    this.error = '';
    this.success = false;

    this.auth.changePassword(this.oldPassword, this.newPassword).subscribe({
      next: () => {
        this.loading = false;
        this.success = true;
        this.oldPassword = '';
        this.newPassword = '';
        this.confirmPassword = '';
      },
      error: err => {
        this.loading = false;
        this.error = err.error?.error || 'Passwortänderung fehlgeschlagen';
      }
    });
  }
}
