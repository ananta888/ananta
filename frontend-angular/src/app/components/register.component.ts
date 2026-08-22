import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { RouterLink } from '@angular/router';

@Component({
  selector: 'app-register', standalone: true, imports: [FormsModule, RouterLink],
  template: `
    <main class="login-container"><section class="card login-card">
      <h2>Hub-Konto anlegen</h2>
      <form (submit)="submit($event)">
        <label>Benutzername<input name="username" [(ngModel)]="username" required autocomplete="username"></label>
        <label>E-Mail<input name="email" [(ngModel)]="email" required type="email" autocomplete="email"></label>
        <label>Passwort<input name="password" [(ngModel)]="password" required type="password" autocomplete="new-password"></label>
        <p class="muted">Mindestens 12 Zeichen sowie Groß-/Kleinbuchstabe, Zahl und Sonderzeichen.</p>
        @if (message) { <p [class.error-msg]="failed" role="status">{{message}}</p> }
        <button class="primary btn-full" [disabled]="busy">{{busy ? 'Wird angelegt…' : 'Registrieren'}}</button>
      </form>
      <a routerLink="/login">Zurück zur Anmeldung</a>
    </section></main>`,
})
export class RegisterComponent {
  private readonly http = inject(HttpClient);
  username = ''; email = ''; password = ''; busy = false; failed = false; message = '';
  submit(event: Event): void {
    event.preventDefault(); this.busy = true; this.failed = false; this.message = '';
    this.http.post('/registrations', {username: this.username, email: this.email, password: this.password})
      .subscribe({
        next: () => { this.message = 'Bestätigungs-E-Mail wurde versendet.'; this.busy = false; },
        error: error => { this.failed = true; this.message = error?.error?.message || 'Registrierung fehlgeschlagen.'; this.busy = false; },
      });
  }
}
