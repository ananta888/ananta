import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { UserAuthService } from '../services/user-auth.service';
import { AgentDirectoryService } from '../services/agent-directory.service';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="login-container">
      <div class="card" style="max-width: 400px; margin: 100px auto;">
        <h2>Ananta Login</h2>
        <form (submit)="onLogin($event)">
          <div class="form-group">
            <label>Benutzername</label>
            <input type="text" [(ngModel)]="username" name="username" required>
          </div>
          <div class="form-group">
            <label>Passwort</label>
            <input type="password" [(ngModel)]="password" name="password" required>
          </div>
          <div *ngIf="error" class="error-msg">{{error}}</div>
          <button type="submit" [disabled]="loading" class="primary" style="width: 100%; margin-top: 16px;">
            {{ loading ? 'Lade...' : 'Anmelden' }}
          </button>
        </form>
      </div>
    </div>
  `,
  styles: [`
    .login-container { height: 100vh; background: #f5f5f5; display: flex; align-items: flex-start; }
    .error-msg { color: #dc3545; margin-top: 8px; font-size: 14px; }
    .form-group { margin-bottom: 12px; }
    label { display: block; margin-bottom: 4px; font-weight: 500; }
    input { width: 100%; padding: 8px; border: 1px solid #ccc; border-radius: 4px; }
  `]
})
export class LoginComponent {
  username = '';
  password = '';
  loading = false;
  error = '';

  constructor(
    private http: HttpClient,
    private router: Router,
    private auth: UserAuthService,
    private dir: AgentDirectoryService
  ) {}

  onLogin(e: Event) {
    e.preventDefault();
    this.loading = true;
    this.error = '';

    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) {
      this.error = 'Kein Hub in den Einstellungen gefunden.';
      this.loading = false;
      return;
    }

    this.http.post<any>(`${hub.url}/login`, {
      username: this.username,
      password: this.password
    }).subscribe({
      next: res => {
        this.auth.setTokens(res.token, res.refresh_token);
        this.router.navigate(['/dashboard']);
      },
      error: err => {
        this.error = err.error?.error || 'Login fehlgeschlagen';
        this.loading = false;
      }
    });
  }
}
