import { signal } from '@angular/core';
import { Observable, Subscription, map, timeout } from 'rxjs';
import type { PersonaEffectiveProfile, PersonaOwnerKind, PersonaProfileScope } from '../organizations/persona-media/persona-profile.models';

/** Metadata-only request lifetime; neither grants nor publishes a media source. */
export class MeetProfileCandidateController<T> {
  readonly candidate = signal<T | null>(null);
  readonly busy = signal(false);
  readonly message = signal('');
  private pending?: Subscription;
  private revision = 0;
  private selectedScope: PersonaProfileScope | null = null;

  constructor(
    private readonly load: (scope: PersonaProfileScope) => Observable<PersonaEffectiveProfile>,
    private readonly convert: (value: PersonaEffectiveProfile, scope: PersonaProfileScope) => T,
    private readonly currentScope: () => PersonaProfileScope | null,
  ) {}

  clear(): void {
    ++this.revision; this.pending?.unsubscribe(); this.pending = undefined;
    this.selectedScope = null; this.candidate.set(null); this.busy.set(false); this.message.set('');
  }

  resolve(): void {
    this.clear(); const scope = this.currentScope(), revision = this.revision;
    if (!scope) { this.message.set('Bitte erreichbaren Hub sowie passende Organisations- und Profil-ID wählen.'); return; }
    this.busy.set(true);
    this.pending = this.load(scope).pipe(timeout(10_000), map(value => this.convert(value, scope))).subscribe({
      next: value => {
        if (revision !== this.revision) return;
        if (JSON.stringify(this.currentScope()) !== JSON.stringify(scope)) { this.clear(); return; }
        this.selectedScope = scope; this.candidate.set(value); this.busy.set(false);
      },
      error: () => {
        if (revision !== this.revision) return;
        this.busy.set(false); this.message.set('Profil nicht verfügbar, deaktiviert oder Prüfung fehlgeschlagen. Keine Ersatzquelle ausgewählt.');
      },
    });
  }

  current(): T | null {
    if (this.busy() || this.candidate() === null) return null;
    if (JSON.stringify(this.currentScope()) !== JSON.stringify(this.selectedScope)) { this.clear(); return null; }
    return this.candidate();
  }
}

export function meetProfileScope(hub: string | undefined, project: string, organization: string, kind: PersonaOwnerKind, owner: string): PersonaProfileScope | null {
  organization = organization.trim(); owner = kind === 'organization' ? organization : owner.trim();
  if (!hub || ![project, organization, owner].every(value => /^[A-Za-z0-9_.:-]{1,160}$/.test(value))
    || !['organization', 'team', 'agent'].includes(kind)) return null;
  return { hub, project, organization, kind, owner };
}
