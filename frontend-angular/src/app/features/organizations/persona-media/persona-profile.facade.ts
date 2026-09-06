import { DestroyRef, Injectable, effect, inject, signal, untracked } from '@angular/core';
import { Observable, Subscription } from 'rxjs';
import { OrganizationTopologyStateService } from '../services/organization-topology-state.service';
import { PersonaProfileApiClient } from './persona-profile-api.client';
import { PersonaEffectiveProfile, PersonaImageReference, PersonaOwnerKind, PersonaProfile, PersonaProfileScope, PersonaProfileSnapshot, PersonaSelectionState } from './persona-profile.models';

@Injectable()
export class PersonaProfileFacade {
  private readonly api = inject(PersonaProfileApiClient);
  private readonly topology = inject(OrganizationTopologyStateService);
  private readonly destroyRef = inject(DestroyRef);
  private pending = new Subscription();
  private sequence = 0;
  private scope: PersonaProfileScope | null = null;
  readonly snapshot = signal<PersonaProfileSnapshot | null>(null);
  readonly effective = signal<PersonaEffectiveProfile | null>(null);
  readonly kind = signal<PersonaOwnerKind>('organization');
  readonly owner = signal('');
  readonly personaId = signal('');
  readonly imageState = signal<PersonaSelectionState>('missing');
  readonly imageId = signal('');
  readonly image = signal<PersonaImageReference | null>(null);
  readonly previewUrl = signal('');
  readonly busy = signal(false);
  readonly message = signal('');
  readonly error = signal('');

  constructor() {
    effect(() => {
      const hub = this.topology.hubUrl();
      const project = this.topology.projectId();
      const organization = this.topology.selectedOrganizationId() ?? '';
      untracked(() => {
        this.kind.set('organization');
        this.owner.set(organization);
        this.scope = hub && project && organization ? { hub, project, organization, kind: 'organization', owner: organization } : null;
        this.load();
      });
    });
    this.destroyRef.onDestroy(() => this.cancel());
  }

  chooseOwner(kind: PersonaOwnerKind, owner: string): void {
    if (!this.scope || !this.scopeCurrent()) return;
    this.kind.set(kind);
    this.owner.set(owner);
    this.scope = { ...this.scope, kind, owner };
    this.load();
  }

  load(): void {
    this.cancel();
    this.snapshot.set(null);
    this.effective.set(null);
    this.image.set(null);
    this.imageId.set('');
    this.personaId.set('');
    this.imageState.set('missing');
    this.message.set('');
    this.error.set('');
    if (!this.scope) return;
    const scope = this.scope;
    this.run(this.api.current(scope), snapshot => {
      this.snapshot.set(snapshot);
      const profile = snapshot.profile;
      this.personaId.set(profile?.persona_id ?? '');
      this.imageState.set(profile?.image.state ?? 'missing');
      this.image.set(profile?.image.asset ?? null);
      this.imageId.set(profile?.image.asset?.artifact_id ?? '');
      if (!snapshot.media_available) this.message.set('Das bisherige Medium ist nicht verfügbar. Das Profil kann ersetzt oder deaktiviert werden.');
      this.run(this.api.effective(scope), effective => this.effective.set(effective));
    });
  }

  selectImageState(state: PersonaSelectionState): void {
    this.cancel();
    this.imageState.set(state);
    this.image.set(null);
    this.imageId.set('');
  }

  changeImageId(id: string): void {
    this.cancel();
    this.imageId.set(id);
    this.image.set(null);
  }

  inspectImage(): void {
    if (!this.scope || !this.scopeCurrent() || this.busy() || !this.imageId().trim()) return;
    this.cancel();
    const scope = this.scope;
    this.run(this.api.image(scope, this.imageId().trim()), reference => {
      this.image.set(reference);
      this.run(this.api.preview(scope, reference.artifact_id), blob => this.previewUrl.set(URL.createObjectURL(blob)));
    });
  }

  previewEffective(): void {
    const selected = this.effective()?.media.find(item => item.kind === 'image');
    if (!this.scope || !this.scopeCurrent() || this.busy() || !selected?.preview_allowed || !selected.asset) return;
    this.cancel();
    this.run(this.api.preview(this.scope, selected.asset.artifact_id), blob => this.previewUrl.set(URL.createObjectURL(blob)));
  }

  save(): void {
    const snapshot = this.snapshot();
    if (!this.scope || !this.scopeCurrent() || !snapshot || this.busy() || !this.personaId().trim()) return;
    if (this.imageState() === 'asset' && !this.image()) {
      this.error.set('Bitte zuerst die Bild-ID prüfen.');
      return;
    }
    const empty = { state: 'missing', asset: null } as const;
    const profile: PersonaProfile = {
      schema_version: 'ananta.persona-media.v1', tenant_id: snapshot.tenant_id, project_id: this.scope.project,
      owner_kind: this.scope.kind, owner_id: this.scope.owner, persona_id: this.personaId().trim(), revision: snapshot.revision + 1,
      image: { state: this.imageState(), asset: this.imageState() === 'asset' ? this.image() : null },
      voice: snapshot.profile?.voice ?? empty, video: snapshot.profile?.video ?? empty, style: snapshot.profile?.style ?? empty,
      requested_usage: snapshot.profile?.requested_usage ?? [],
    };
    this.cancel();
    this.run(this.api.save(this.scope, profile, snapshot.revision), () => {
      this.load();
      this.message.set('Gespeichert. Dadurch wird kein Medium veröffentlicht und kein laufender Meet-Turn umgeschaltet.');
    });
  }

  private run<T>(request: Observable<T>, accept: (value: T) => void): void {
    if (!this.scopeCurrent()) return;
    const sequence = this.sequence;
    this.busy.set(true);
    this.error.set('');
    this.pending.add(request.subscribe({
      next: value => {
        if (sequence !== this.sequence || !this.scopeCurrent()) return;
        this.busy.set(false);
        accept(value);
      },
      error: () => {
        if (sequence !== this.sequence || !this.scopeCurrent()) return;
        this.busy.set(false);
        this.error.set('Nicht verfügbar, keine Berechtigung oder Revisionskonflikt. Neu laden und Rechte beziehungsweise Medienfreigabe prüfen.');
      },
    }));
  }

  private scopeCurrent(): boolean {
    return this.scope?.hub === this.topology.hubUrl()
      && this.scope?.project === this.topology.projectId()
      && this.scope?.organization === this.topology.selectedOrganizationId();
  }

  private cancel(): void {
    this.sequence++;
    this.pending.unsubscribe();
    this.pending = new Subscription();
    this.busy.set(false);
    const previous = this.previewUrl();
    if (previous) URL.revokeObjectURL(previous);
    this.previewUrl.set('');
  }
}
