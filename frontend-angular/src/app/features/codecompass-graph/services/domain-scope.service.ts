import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, Observable, map } from 'rxjs';
import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { AiSnakeConfigService } from '../../../services/ai-snake-config.service';
import { DetectedDomain, DomainListResponse, ResolvedDomainScopePreview } from '../models/domain-scope.model';

// CCRDS-015: domain list + scope preview against the hub API. Selecting a
// domain writes `domain:<id>` into the existing chat_retrieval_domain_hint
// (CCRDS-DD-006) — no parallel config key.
const DOMAIN_HINT_KEY = 'chat_retrieval_domain_hint';
const DOMAIN_HINT_PREFIX = 'domain:';

interface ApiEnvelope<T> {
  status: string;
  data?: T;
  message?: string;
}

@Injectable({ providedIn: 'root' })
export class DomainScopeService {
  private core = inject(HubApiCoreService);
  private dir = inject(AgentDirectoryService);
  private snakeConfig = inject(AiSnakeConfigService);

  readonly domains$ = new BehaviorSubject<DetectedDomain[]>([]);
  readonly scopeEnabled$ = new BehaviorSubject<boolean>(false);
  readonly listErrors$ = new BehaviorSubject<string[]>([]);

  private get hubUrl(): string {
    return this.dir.list().find(a => a.role === 'hub')?.url ?? '';
  }

  loadDomains(): void {
    const url = this.hubUrl;
    if (!url) return;
    this.core.get<ApiEnvelope<DomainListResponse>>(`${url}/api/codecompass/domains`, url).subscribe({
      next: r => {
        const data = r?.data;
        if (!data) return;
        this.domains$.next(data.domains ?? []);
        this.scopeEnabled$.next(!!data.scope_enabled);
        this.listErrors$.next(data.errors ?? []);
      },
      error: () => {},
    });
  }

  previewScope(domainIds: string[], strict = true): Observable<ResolvedDomainScopePreview | null> {
    const url = this.hubUrl;
    return this.core
      .post<ApiEnvelope<ResolvedDomainScopePreview>>(
        `${url}/api/codecompass/domain-scope/preview`,
        { selected_domain_ids: domainIds, strict },
        url,
      )
      .pipe(map(r => r?.data ?? null));
  }

  /** Aktuelle Auswahl aus dem bestehenden Hint lesen (nur domain:-Werte). */
  currentSelection(): string | null {
    const hint = String(this.snakeConfig.config$.value[DOMAIN_HINT_KEY] ?? '');
    return hint.startsWith(DOMAIN_HINT_PREFIX) ? hint.slice(DOMAIN_HINT_PREFIX.length) : null;
  }

  /** Domain als aktiven Scope setzen; null deaktiviert die Auswahl. */
  selectDomain(domainId: string | null): void {
    this.snakeConfig.updateField(DOMAIN_HINT_KEY, domainId ? `${DOMAIN_HINT_PREFIX}${domainId}` : '');
  }
}
