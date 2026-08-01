import { CommonModule } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  OnInit,
  Output,
  computed,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize, forkJoin, switchMap, throwError } from 'rxjs';

import type {
  SourceControlGitAuthorizationHealth,
  SourceControlGitAuthorizationKind,
  SourceControlGitAuthorizationView,
} from '../../models/source-control-v1-governance.model';
import {
  SourceControlV1GovernanceApiClient,
} from '../../services/source-control-v1-governance-api.client';

@Component({
  selector: 'app-git-authorization-onboarding',
  standalone: true,
  imports: [CommonModule, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="authorization" aria-labelledby="git-authorization-title">
      <header>
        <div>
          <p class="eyebrow">Hub-verwaltete Berechtigung</p>
          <h3 id="git-authorization-title">Git-Autorisierung</h3>
        </div>
        <button type="button" (click)="reload()" [disabled]="loading() || mutating()">
          Neu laden
        </button>
      </header>

      @if (health(); as currentHealth) {
        <p class="health" [attr.data-status]="currentHealth.status" role="status">
          Provider: {{ currentHealth.status }}
          @if (currentHealth.reason_code) {
            <span>({{ currentHealth.reason_code }})</span>
          }
        </p>
      } @else {
        <p class="health" data-status="unavailable" role="status">Provider: unavailable</p>
      }

      <div class="form-grid">
        <label for="authorization-kind">Art</label>
        <select
          id="authorization-kind"
          [ngModel]="authorizationKind()"
          (ngModelChange)="authorizationKind.set($event)"
        >
          <option value="github_app">GitHub App</option>
          <option value="github_oauth">GitHub OAuth</option>
          <option value="generic_git">Generisches Git</option>
        </select>

        <label for="authorization-handle">Serverseitiges Authorization-Handle</label>
        <input
          id="authorization-handle"
          data-testid="git-authorization-handle"
          autocomplete="off"
          [ngModel]="authorizationHandle()"
          (ngModelChange)="authorizationHandle.set($event)"
        />

        @if (authorizationKind() !== 'generic_git') {
          <label for="authorization-repository">Repository (owner/repository)</label>
          <input
            id="authorization-repository"
            data-testid="git-authorization-repository"
            autocomplete="off"
            [ngModel]="repository()"
            (ngModelChange)="repository.set($event)"
          />
        }

        <button
          type="button"
          data-testid="provision-git-authorization"
          [disabled]="!canProvision()"
          (click)="provision()"
        >
          {{ mutating() ? 'Hub validiert...' : 'Validieren und provisionieren' }}
        </button>
      </div>

      <p class="hint">
        Der Browser uebermittelt ausschließlich Handle, Autorisierungsart und
        Repository-Koordinate. Zugangsdaten bleiben im Hub.
      </p>

      @if (errorMessage()) {
        <p class="notice error" role="alert">{{ errorMessage() }}</p>
      }
      @if (successMessage()) {
        <p class="notice success" role="status">{{ successMessage() }}</p>
      }

      @if (authorizations().length > 0) {
        <ul aria-label="Registrierte Git-Autorisierungen">
          @for (authorization of authorizations(); track authorization.authorization_ref) {
            <li>
              <div>
                <strong>{{ authorization.repository || authorization.authorization_ref }}</strong>
                <span>{{ authorization.authorization_kind }} · {{ authorization.authorization_state }}</span>
              </div>
              <div class="row-actions">
                <button type="button" (click)="loadDetail(authorization)" [disabled]="mutating()">
                  Status
                </button>
                <button
                  type="button"
                  (click)="revoke(authorization)"
                  [disabled]="mutating() || !authorization.next_actions.includes('revoke')"
                >
                  Widerrufen
                </button>
                <button
                  type="button"
                  (click)="recordScopeLoss(authorization)"
                  [disabled]="mutating() || !authorization.next_actions.includes('record_scope_loss')"
                >
                  Scope-Verlust melden
                </button>
              </div>
            </li>
          }
        </ul>
      }
    </section>
  `,
  styles: [`
    :host { display: block; }
    .authorization { display: grid; gap: .8rem; padding: 1rem; border: 1px solid var(--border-color, #8794a0); border-radius: .65rem; background: color-mix(in srgb, var(--surface-color, #fff) 92%, #0d5c63); }
    header, li, .row-actions { display: flex; align-items: center; justify-content: space-between; gap: .65rem; flex-wrap: wrap; }
    h3, .eyebrow, .health, .hint, .notice { margin: 0; }
    .eyebrow { font-size: .72rem; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; }
    .form-grid { display: grid; grid-template-columns: minmax(10rem, .7fr) minmax(13rem, 1.3fr); gap: .55rem .8rem; align-items: center; }
    .form-grid button { grid-column: 2; justify-self: start; }
    input, select, button { min-height: 2.35rem; padding: .45rem .65rem; font: inherit; }
    .health[data-status="healthy"] { color: #087c56; }
    .health[data-status="degraded"] { color: #8a5500; }
    .health[data-status="unavailable"], .error { color: #8f1711; }
    .hint { color: var(--muted, #516170); }
    .success { color: #087c56; }
    ul { display: grid; gap: .55rem; margin: 0; padding: 0; list-style: none; }
    li { padding-top: .6rem; border-top: 1px solid var(--border-color, #8794a0); }
    li span { display: block; color: var(--muted, #516170); font-size: .85rem; }
    @media (max-width: 42rem) { .form-grid { grid-template-columns: 1fr; } .form-grid button { grid-column: 1; } }
  `],
})
export class GitAuthorizationOnboardingComponent implements OnInit {
  private readonly api = inject(SourceControlV1GovernanceApiClient);

  @Output() readonly provisioned = new EventEmitter<SourceControlGitAuthorizationView>();

  readonly authorizationKind = signal<SourceControlGitAuthorizationKind>('github_app');
  readonly authorizationHandle = signal('');
  readonly repository = signal('');
  readonly health = signal<SourceControlGitAuthorizationHealth | null>(null);
  readonly authorizations = signal<readonly SourceControlGitAuthorizationView[]>([]);
  readonly loading = signal(false);
  readonly mutating = signal(false);
  readonly errorMessage = signal('');
  readonly successMessage = signal('');

  readonly canProvision = computed(() => {
    const health = this.health();
    const handle = this.authorizationHandle().trim();
    if (!health || health.status === 'unavailable' || this.loading() || this.mutating()) {
      return false;
    }
    if (!/^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,255}$/.test(handle)) {
      return false;
    }
    if (this.authorizationKind() === 'generic_git') {
      return health.connector_ready.generic_git;
    }
    return health.connector_ready.github_repository && isRepositoryCoordinate(this.repository());
  });

  ngOnInit(): void {
    this.reload();
  }

  reload(): void {
    this.loading.set(true);
    this.errorMessage.set('');
    forkJoin({
      health: this.api.gitAuthorizationHealth(),
      page: this.api.listGitAuthorizations(),
    })
      .pipe(finalize(() => this.loading.set(false)))
      .subscribe({
        next: ({ health, page }) => {
          this.health.set(health);
          this.authorizations.set(page.items);
        },
        error: () => {
          this.health.set(null);
          this.authorizations.set([]);
          this.errorMessage.set('Git-Autorisierungen sind derzeit nicht verfuegbar.');
        },
      });
  }

  provision(): void {
    const selection = this.selection();
    if (!this.canProvision() || !selection) {
      this.errorMessage.set('Die Autorisierung bleibt wegen ungueltiger oder nicht verfuegbarer Angaben gesperrt.');
      return;
    }
    this.mutating.set(true);
    this.errorMessage.set('');
    this.successMessage.set('');
    this.api
      .validateGitAuthorization(selection)
      .pipe(
        switchMap((validation) =>
          !validation.persisted && validation.authorization_state === 'active'
            ? this.api.provisionGitAuthorization(
                selection,
                `ui:git-authorization:create:${crypto.randomUUID()}`,
              )
            : throwError(() => new Error('authorization_validation_rejected')),
        ),
        finalize(() => this.mutating.set(false)),
      )
      .subscribe({
        next: (authorization) => {
          this.successMessage.set('Die Hub-Autorisierung wurde provisioniert.');
          this.provisioned.emit(authorization);
          this.reload();
        },
        error: () => {
          this.errorMessage.set('Der Hub hat die Git-Autorisierung nicht provisioniert.');
        },
      });
  }

  loadDetail(authorization: SourceControlGitAuthorizationView): void {
    this.transition(
      this.api.gitAuthorizationDetail(
        authorization.authorization_ref,
        authorization.repository,
      ),
      'Der Autorisierungsstatus wurde aktualisiert.',
    );
  }

  revoke(authorization: SourceControlGitAuthorizationView): void {
    if (!authorization.etag) {
      this.errorMessage.set('Die Autorisierung besitzt keinen revisionsgebundenen ETag.');
      return;
    }
    this.transition(
      this.api.revokeGitAuthorization(
        authorization.authorization_ref,
        authorization.repository,
        {
          etag: authorization.etag,
          idempotencyKey: `ui:git-authorization:revoke:${crypto.randomUUID()}`,
        },
      ),
      'Die Autorisierung wurde widerrufen.',
    );
  }

  recordScopeLoss(authorization: SourceControlGitAuthorizationView): void {
    if (!authorization.etag) {
      this.errorMessage.set('Die Autorisierung besitzt keinen revisionsgebundenen ETag.');
      return;
    }
    this.transition(
      this.api.recordGitAuthorizationScopeLoss(
        authorization.authorization_ref,
        authorization.repository,
        {
          etag: authorization.etag,
          idempotencyKey: `ui:git-authorization:scope-loss:${crypto.randomUUID()}`,
        },
      ),
      'Der Scope-Verlust wurde revisionsgebunden erfasst.',
    );
  }

  private selection() {
    const handle = this.authorizationHandle().trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,255}$/.test(handle)) {
      return null;
    }
    const kind = this.authorizationKind();
    if (kind === 'generic_git') {
      return {
        authorization_handle: handle,
        authorization_kind: kind,
        repository: null,
      } as const;
    }
    const repository = this.repository().trim();
    return isRepositoryCoordinate(repository)
      ? {
          authorization_handle: handle,
          authorization_kind: kind,
          repository,
        }
      : null;
  }

  private transition(
    operation: ReturnType<SourceControlV1GovernanceApiClient['gitAuthorizationDetail']>,
    successMessage: string,
  ): void {
    this.mutating.set(true);
    this.errorMessage.set('');
    this.successMessage.set('');
    operation.pipe(finalize(() => this.mutating.set(false))).subscribe({
      next: (updated) => {
        this.authorizations.update((items) => {
          const remaining = items.filter(
            (item) => item.authorization_ref !== updated.authorization_ref,
          );
          return [...remaining, updated];
        });
        this.successMessage.set(successMessage);
      },
      error: () => {
        this.errorMessage.set('Die revisionsgebundene Autorisierungsaktion ist fehlgeschlagen.');
      },
    });
  }
}

function isRepositoryCoordinate(value: string): boolean {
  return /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(value.trim());
}
