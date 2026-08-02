import { CommonModule } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  Output,
  computed,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize, switchMap } from 'rxjs';

import type {
  SourceControlPublicRemoteCreation,
  SourceControlPublicRemoteIntent,
  SourceControlPublicRemoteValidation,
} from '../../models/source-control-v1-governance.model';
import { SourceControlV1GovernanceApiClient } from '../../services/source-control-v1-governance-api.client';

type PublicGitCoordinate =
  | { readonly provider: 'github_public'; readonly owner: string; readonly repository: string }
  | { readonly provider: 'https_git'; readonly host: string; readonly repository: string };

@Component({
  selector: 'app-public-git-remote-onboarding',
  standalone: true,
  imports: [CommonModule, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="public-remote" aria-labelledby="public-remote-title">
      <header>
          <p class="eyebrow">Credential-freier Import</p>
          <h3 id="public-remote-title">Öffentliches Git/GitHub-Repository</h3>
          <p>
            Repository-URL und Branch, Tag oder Commit werden vom Hub validiert und auf einen Commit fixiert.
          </p>
      </header>

      <div class="form-grid">
        <label for="public-git-url">Repository-URL</label>
        <input
          id="public-git-url"
          data-testid="public-git-url"
          autocomplete="off"
          inputmode="url"
          placeholder="https://github.com/octocat/Hello-World"
          [ngModel]="repositoryUrl()"
          (ngModelChange)="repositoryUrl.set($event)"
          aria-describedby="public-git-url-examples"
        />

        <label for="public-git-ref">Branch, Tag oder Commit</label>
        <input
          id="public-git-ref"
          data-testid="public-git-ref"
          autocomplete="off"
          placeholder="main"
          [ngModel]="requestedRef()"
          (ngModelChange)="requestedRef.set($event)"
        />

        <button
          type="button"
          data-testid="create-public-remote"
          [disabled]="!canSubmit()"
          (click)="submit()"
        >
          {{ submitting() ? 'Hub löst Commit auf...' : 'Validieren und registrieren' }}
        </button>
      </div>

      <p class="hint" id="public-git-url-examples">
        Beispiele: <code>https://github.com/octocat/Hello-World</code> mit
        <code>master</code>, <code>v1.2.0</code> oder einem vollständigen Commit-SHA.
        Erlaubt sind ausschließlich öffentliche HTTPS-URLs ohne Zugangsdaten, Port, Query oder Fragment.
      </p>
      @if (validation(); as preview) {
        <p class="notice" role="status">
          Aufgelöster Commit: <code>{{ preview.commit_sha }}</code>
        </p>
      }
      @if (errorMessage()) {
        <p class="notice error" role="alert">{{ errorMessage() }}</p>
      }
      @if (successMessage()) {
        <p class="notice success" role="status">{{ successMessage() }}</p>
      }
    </section>
  `,
  styles: [`
    :host { display: block; }
    .public-remote { display: grid; gap: .8rem; padding: 1rem; border: 1px solid var(--border-color, #8794a0); border-radius: .65rem; background: color-mix(in srgb, var(--surface-color, #fff) 93%, #d97816); }
    header p, h3, .eyebrow, .hint, .notice { margin: 0; }
    header { display: grid; gap: .3rem; }
    .eyebrow { font-size: .72rem; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; }
    .form-grid { display: grid; grid-template-columns: minmax(10rem, .7fr) minmax(13rem, 1.3fr); gap: .55rem .8rem; align-items: center; }
    .form-grid button { grid-column: 2; justify-self: start; }
    input, select, button { min-height: 2.35rem; padding: .45rem .65rem; font: inherit; }
    .hint { color: var(--muted, #516170); }
    .notice { padding: .55rem; background: #edf4fa; color: #17324a; }
    .error { background: #ffe9e7; color: #8f1711; }
    .success { background: #e5f6ea; color: #087c56; }
    code { overflow-wrap: anywhere; }
    @media (max-width: 42rem) { .form-grid { grid-template-columns: 1fr; } .form-grid button { grid-column: 1; } }
  `],
})
export class PublicGitRemoteOnboardingComponent {
  private readonly api = inject(SourceControlV1GovernanceApiClient);

  @Input() projectId = '';
  @Output() readonly remoteCreated = new EventEmitter<SourceControlPublicRemoteCreation>();

  readonly repositoryUrl = signal('');
  readonly requestedRef = signal('main');
  readonly submitting = signal(false);
  readonly validation = signal<SourceControlPublicRemoteValidation | null>(null);
  readonly errorMessage = signal('');
  readonly successMessage = signal('');

  readonly canSubmit = computed(() =>
    Boolean(this.projectId.trim()) && !this.submitting() && this.intent() !== null,
  );

  submit(): void {
    const projectId = this.projectId.trim();
    const intent = this.intent();
    if (!projectId || !intent || this.submitting()) {
      this.errorMessage.set(
        'Die öffentlichen Repository-Koordinaten sind unvollständig oder unsicher.',
      );
      return;
    }

    this.submitting.set(true);
    this.validation.set(null);
    this.errorMessage.set('');
    this.successMessage.set('');
    this.api
      .validatePublicRemote(projectId, intent)
      .pipe(
        switchMap((validation) => {
          this.validation.set(validation);
          return this.api.createPublicRemote(
            projectId,
            validation.validation_handle,
            `ui:public-remote:create:${crypto.randomUUID()}`,
          );
        }),
        finalize(() => this.submitting.set(false)),
      )
      .subscribe({
        next: (remote) => {
          this.successMessage.set('Das öffentliche Remote wurde vom Hub registriert.');
          this.remoteCreated.emit(remote);
        },
        error: () => {
          this.errorMessage.set(
            'Der Hub konnte das öffentliche Remote nicht validieren und registrieren.',
          );
        },
      });
  }

  private intent(): SourceControlPublicRemoteIntent | null {
    const requestedRef = this.requestedRef().trim();
    const coordinate = parsePublicGitRepositoryUrl(this.repositoryUrl());
    return coordinate && isSafeGitRef(requestedRef)
      ? { ...coordinate, requested_ref: requestedRef }
      : null;
  }
}

export function parsePublicGitRepositoryUrl(
  value: string,
): PublicGitCoordinate | null {
  const raw = String(value || '').trim();
  if (!raw || raw.length > 1024) return null;
  const authority = raw.match(/^https:\/\/([^/?#]+)/)?.[1] ?? '';
  if (!authority || authority.includes(':')) return null;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (
    url.protocol !== 'https:'
    || url.username
    || url.password
    || url.port
    || url.search
    || url.hash
  ) {
    return null;
  }
  const host = url.hostname.toLowerCase();
  const segments = url.pathname.split('/').filter(Boolean);
  if (host === 'github.com') {
    if (segments.length !== 2) return null;
    const owner = segments[0];
    const repository = segments[1].replace(/\.git$/, '');
    return isGitHubSlug(owner) && isGitHubSlug(repository)
      ? { provider: 'github_public', owner, repository }
      : null;
  }
  const repository = segments.join('/');
  return isPublicDnsHost(host) && isRepositoryPath(repository)
    ? { provider: 'https_git', host, repository }
    : null;
}

function isGitHubSlug(value: string): boolean {
  return /^(?![.-])(?!.*\.\.)(?!.*--)[A-Za-z0-9_.-]{1,100}(?<![.-])$/.test(value);
}

function isRepositoryPath(value: string): boolean {
  if (
    value.length === 0
    || value.length > 512
    || value.startsWith('/')
    || value.endsWith('/')
    || value.includes('\\')
  ) {
    return false;
  }
  const segments = value.split('/');
  return segments.length >= 2 && segments.every(
    (segment) =>
      segment !== '.'
      && segment !== '..'
      && /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/.test(segment),
  );
}

function isSafeGitRef(value: string): boolean {
  return value.length > 0
    && value.length <= 255
    && !value.startsWith('.')
    && !value.endsWith('.')
    && !value.endsWith('/')
    && !value.includes('..')
    && !value.includes('@{')
    && !/[\u0000-\u0020\u007f~^:?*[\\]/.test(value);
}

function isPublicDnsHost(value: string): boolean {
  if (
    value.length > 253
    || value.includes(':')
    || value.includes('/')
    || value.includes('[')
    || value.includes(']')
    || /^\d+(?:\.\d+){3}$/.test(value)
  ) {
    return false;
  }
  const labels = value.split('.');
  const reservedSuffix = labels.at(-1)?.toLowerCase();
  return labels.length >= 2
    && !['localhost', 'local', 'internal', 'home', 'lan'].includes(reservedSuffix ?? '')
    && labels.every(
      (label) =>
        /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/.test(label),
    );
}
