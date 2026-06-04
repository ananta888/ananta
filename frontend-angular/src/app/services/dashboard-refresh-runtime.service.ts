import { Injectable } from '@angular/core';
import { Subscription, interval } from 'rxjs';

@Injectable({ providedIn: 'root' })
export class DashboardRefreshRuntimeService {
  private refreshSub?: Subscription;
  private initialRefreshTimer?: ReturnType<typeof setTimeout>;

  start(refresh: () => void, pollMs = 10000): void {
    this.stop();
    this.initialRefreshTimer = setTimeout(() => {
      this.initialRefreshTimer = undefined;
      refresh();
    }, 0);
    this.refreshSub = interval(Math.max(3000, pollMs)).subscribe(() => refresh());
  }

  stop(): void {
    if (this.initialRefreshTimer) {
      clearTimeout(this.initialRefreshTimer);
      this.initialRefreshTimer = undefined;
    }
    this.refreshSub?.unsubscribe();
    this.refreshSub = undefined;
  }
}
