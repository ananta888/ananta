/**
 * Welle 6: Watches AuthRefreshCoordinator.authRequired$ and redirects the
 * user to /login when a 401 cannot be recovered.
 *
 * Decoupled from the coordinator so the coordinator stays a pure side-
 * effect-free service (testable in isolation). The router side-effect
 * lives here.
 */
import { Injectable, inject } from '@angular/core';
import { Router } from '@angular/router';
import { AuthRefreshCoordinator } from './auth-refresh-coordinator.service';

@Injectable({ providedIn: 'root' })
export class AuthRequiredRouter {
  private coordinator = inject(AuthRefreshCoordinator);
  private router = inject(Router);

  start(): void {
    this.coordinator.authRequired$.subscribe((sphere) => {
      if (sphere === null) return;
      // We pass the failing sphere as a query param so LoginComponent can
      // pre-select the right mask (OIDC vs Hub-direct).
      void this.router.navigate(['/login'], { queryParams: { sphere } });
    });
  }
}