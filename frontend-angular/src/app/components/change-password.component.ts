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
        <div id="password-success" class="success-msg" role="alert" aria-live="polite">Passwort erfolgreich geändert!</div>
      }
      @if (error) {
        <div id="password-error" class="error-msg" role="alert" aria-live="polite">{{error}}</div>
      }

      <form (submit)="onSubmit($event)" aria-label="Passwort ändern Formular">
        <div class="form-group">
          <label for="old-password">Altes Passwort</label>
          <input
            type="password"
            id="old-password"
            [(ngModel)]="oldPassword"
            name="oldPassword"
            required
            aria-required="true"
            [attr.aria-describedby]="error ? 'password-error' : (getOldPasswordError() ? 'old-password-error' : null)"
            [attr.aria-invalid]="getOldPasswordError() ? 'true' : null"
            (blur)="oldPasswordTouched = true">
          @if (oldPasswordTouched && getOldPasswordError()) {
            <small id="old-password-error" class="error-msg" style="display: block; margin-top: 4px;">{{getOldPasswordError()}}</small>
          }
        </div>
        <div class="form-group">
          <label for="new-password">Neues Passwort</label>
          <input
            type="password"
            id="new-password"
            [(ngModel)]="newPassword"
            name="newPassword"
            required
            aria-required="true"
            [attr.aria-describedby]="getNewPasswordError() ? 'new-password-error' : 'password-hint'"
            [attr.aria-invalid]="getNewPasswordError() ? 'true' : null"
            (blur)="newPasswordTouched = true">
          @if (newPasswordTouched && getNewPasswordError()) {
            <small id="new-password-error" class="error-msg" style="display: block; margin-top: 4px;">{{getNewPasswordError()}}</small>
          }
          @if (!getNewPasswordError()) {
            <small id="password-hint" class="muted" style="font-size: 11px; display: block; margin-top: 2px;">Mindestens 4 Zeichen</small>
          }
        </div>
        <div class="form-group">
          <label for="confirm-password">Neues Passwort bestätigen</label>
          <input
            type="password"
            id="confirm-password"
            [(ngModel)]="confirmPassword"
            name="confirmPassword"
            required
            aria-required="true"
            [attr.aria-describedby]="error ? 'password-error' : (getConfirmPasswordError() ? 'confirm-password-error' : null)"
            [attr.aria-invalid]="getConfirmPasswordError() ? 'true' : null"
            (blur)="confirmPasswordTouched = true">
          @if (confirmPasswordTouched && getConfirmPasswordError()) {
            <small id="confirm-password-error" class="error-msg" style="display: block; margin-top: 4px;">{{getConfirmPasswordError()}}</small>
          }
        </div>

        <button
          type="submit"
          [disabled]="loading || !isValid()"
          class="primary"
          [attr.aria-label]="loading ? 'Passwort wird geändert' : 'Passwort ändern'">
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
  oldPasswordTouched = false;
  newPasswordTouched = false;
  confirmPasswordTouched = false;

  getOldPasswordError(): string | null {
    if (!this.oldPassword || this.oldPassword.length === 0) {
      return 'Altes Passwort ist erforderlich';
    }
    return null;
  }

  getNewPasswordError(): string | null {
    if (!this.newPassword || this.newPassword.length === 0) {
      return 'Neues Passwort ist erforderlich';
    }
    if (this.newPassword.length < 4) {
      return 'Passwort muss mindestens 4 Zeichen lang sein';
    }
    return null;
  }

  getConfirmPasswordError(): string | null {
    if (!this.confirmPassword || this.confirmPassword.length === 0) {
      return 'Passwortbestätigung ist erforderlich';
    }
    if (this.newPassword && this.confirmPassword !== this.newPassword) {
      return 'Passwörter stimmen nicht überein';
    }
    return null;
  }

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
