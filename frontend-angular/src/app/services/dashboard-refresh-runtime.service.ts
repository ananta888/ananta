import { Injectable } from '@angular/core';
import { Subscription, interval } from 'rxjs';

@Injectable({ providedIn: 'root' })
export class DashboardRefreshRuntimeService {
  private refreshSub?: Subscription;

  start(refresh: () => void, pollMs = 10000): void {
    this.stop();
    refresh();
    this.refreshSub = interval(Math.max(3000, pollMs)).subscribe(() => refresh());
  }

  stop(): void {
    this.refreshSub?.unsubscribe();
    this.refreshSub = undefined;
  }
}
