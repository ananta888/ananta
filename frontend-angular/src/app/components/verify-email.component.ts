import { Component, inject, OnInit } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';

@Component({
  selector: 'app-verify-email', standalone: true, imports: [RouterLink],
  template: `<main class="login-container"><section class="card login-card"><h2>E-Mail bestätigen</h2>
    <p role="status">{{message}}</p><a routerLink="/login">Zur Anmeldung</a></section></main>`,
})
export class VerifyEmailComponent implements OnInit {
  private readonly route = inject(ActivatedRoute); private readonly http = inject(HttpClient);
  message = 'Bestätigung läuft…';
  ngOnInit(): void {
    const token = this.route.snapshot.queryParamMap.get('token') || '';
    this.http.post<any>('/registrations/verify-email', {token}).subscribe({
      next: response => { this.message = response?.data?.status === 'pending_admin_approval'
        ? 'E-Mail bestätigt. Das Konto wartet noch auf die Adminfreigabe.' : 'E-Mail bestätigt. Das Konto ist jetzt aktiv.'; },
      error: () => { this.message = 'Der Bestätigungslink ist ungültig oder abgelaufen.'; },
    });
  }
}
