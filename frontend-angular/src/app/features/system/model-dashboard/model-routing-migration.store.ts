import { DestroyRef, Injectable, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { catchError, forkJoin, of } from 'rxjs';

import { NotificationService } from '../../../services/notification.service';
import { UserAuthService } from '../../../services/user-auth.service';
import { SystemFacade } from '../../system/system.facade';
import {
  ModelCatalogClient,
  ModelRoutingDiagnostics,
  ModelRoutingLegacyMigrationPreview,
  ModelRoutingReleaseGate,
  ModelRoutingShadowReport,
  canUseModelMutation,
} from './model-catalog.client';

@Injectable()
export class ModelRoutingMigrationStore {
  private readonly client = inject(ModelCatalogClient);
  private readonly system = inject(SystemFacade);
  private readonly auth = inject(UserAuthService);
  private readonly notifications = inject(NotificationService);
  private readonly destroyRef = inject(DestroyRef);
  private baseUrl = '';

  readonly preview = signal<ModelRoutingLegacyMigrationPreview | null>(null);
  readonly shadow = signal<ModelRoutingShadowReport | null>(null);
  readonly releaseGate = signal<ModelRoutingReleaseGate | null>(null);
  readonly diagnostics = signal<ModelRoutingDiagnostics | null>(null);
  readonly confirmed = signal(false);
  readonly loading = signal(false);
  readonly applying = signal(false);
  readonly error = signal('');
  readonly user = signal<unknown>(this.auth.userPayload);
  readonly canMutate = computed(() => canUseModelMutation(this.user(), 'model_routing.mutate'));
  readonly canApply = computed(() => Boolean(
    this.canMutate() && this.confirmed() && this.preview()?.applicable && !this.applying(),
  ));

  constructor() {
    this.auth.user$.pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(user => this.user.set(user));
  }

  load(): void {
    const hub = this.system.resolveHubAgent();
    if (!hub?.url) return;
    this.baseUrl = hub.url;
    this.loading.set(true);
    forkJoin({
      preview: this.client.readLegacyMigrationPreview(this.baseUrl).pipe(catchError(() => of(null))),
      shadow: this.client.readRoutingShadow(this.baseUrl).pipe(catchError(() => of(null))),
      gate: this.client.readRoutingReleaseGate(this.baseUrl).pipe(catchError(() => of(null))),
      diagnostics: this.client.readRoutingDiagnostics(this.baseUrl).pipe(catchError(() => of(null))),
    }).subscribe(({ preview, shadow, gate, diagnostics }) => {
      this.preview.set(preview);
      this.shadow.set(shadow);
      this.releaseGate.set(gate);
      this.diagnostics.set(diagnostics);
      this.confirmed.set(false);
      this.error.set(!preview && !shadow && !gate && !diagnostics
        ? 'Migration und Betriebsdiagnose konnten nicht geladen werden.' : '');
      this.loading.set(false);
    });
  }

  apply(): void {
    const preview = this.preview();
    if (!this.baseUrl || !preview || !this.canApply()) return;
    this.applying.set(true);
    this.client.applyLegacyMigration(
      this.baseUrl, preview.current_revision, preview.confirmation_digest,
    ).subscribe({
      next: routing => {
        this.notifications.success(`Legacy-Modellwerte als Routing Revision ${routing.revision} migriert`);
        this.applying.set(false);
        this.load();
      },
      error: error => {
        this.error.set(error?.status === 409
          ? 'Migrationskonflikt: Vorschau neu laden und erneut explizit bestätigen.'
          : 'Legacy-Modellwerte konnten nicht migriert werden.');
        this.applying.set(false);
        this.confirmed.set(false);
      },
    });
  }
}
