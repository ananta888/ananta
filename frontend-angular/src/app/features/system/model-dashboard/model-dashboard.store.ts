import { DestroyRef, Injectable, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';

import { NotificationService } from '../../../services/notification.service';
import { UserAuthService } from '../../../services/user-auth.service';
import { SystemFacade } from '../../system/system.facade';
import {
  ModelCatalog,
  ModelCatalogClient,
  ModelSummary,
  canUseModelMutation,
} from './model-catalog.client';

@Injectable()
export class ModelDashboardStore {
  private readonly client = inject(ModelCatalogClient);
  private readonly system = inject(SystemFacade);
  private readonly auth = inject(UserAuthService);
  private readonly notifications = inject(NotificationService);
  private readonly destroyRef = inject(DestroyRef);
  private baseUrl = '';

  readonly catalog = signal<ModelCatalog | null>(null);
  readonly loading = signal(false);
  readonly error = signal('');
  readonly user = signal<unknown>(this.auth.userPayload);
  readonly canRefresh = computed(() => canUseModelMutation(this.user(), 'model_catalog.refresh'));
  readonly canSetDefault = computed(
    () => canUseModelMutation(this.user(), 'model_catalog.set_default'),
  );
  readonly providers = computed(() => {
    const groups = new Map<string, ModelSummary[]>();
    for (const model of this.catalog()?.models ?? []) {
      groups.set(model.provider_id, [...(groups.get(model.provider_id) ?? []), model]);
    }
    return [...groups.entries()].map(([providerId, models]) => ({ providerId, models }));
  });

  constructor() {
    this.auth.user$.pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(user => this.user.set(user));
  }

  load(): void {
    const hub = this.system.resolveHubAgent();
    if (!hub?.url) {
      this.error.set('Kein Hub-Agent konfiguriert.');
      return;
    }
    this.baseUrl = hub.url;
    this.loading.set(true);
    this.client.read(this.baseUrl).subscribe({
      next: catalog => {
        this.catalog.set(catalog);
        this.error.set('');
        this.loading.set(false);
      },
      error: () => {
        this.error.set('Modellkatalog konnte nicht geladen werden.');
        this.loading.set(false);
      },
    });
  }

  refresh(): void {
    if (!this.baseUrl || !this.canRefresh()) return;
    this.loading.set(true);
    this.client.refresh(this.baseUrl).subscribe({
      next: catalog => {
        this.catalog.set(catalog);
        this.loading.set(false);
        this.notifications.success('Modellkatalog aktualisiert');
      },
      error: () => {
        this.error.set('Modellkatalog konnte nicht aktualisiert werden.');
        this.loading.set(false);
      },
    });
  }

  setDefault(model: ModelSummary): void {
    if (!this.baseUrl || !this.canSetDefault() || model.availability !== 'available') return;
    this.client.selectDefault(this.baseUrl, model.provider_id, model.model_id).subscribe({
      next: () => {
        this.notifications.success(`${model.display_name} ist jetzt Standard`);
        this.load();
      },
      error: () => this.error.set('Standardmodell konnte nicht geändert werden.'),
    });
  }
}

