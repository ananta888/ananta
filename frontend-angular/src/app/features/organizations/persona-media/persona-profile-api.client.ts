import { Injectable } from '@angular/core';
import { map } from 'rxjs';
import { ApiBaseService } from '../../../services/api-base.service';
import { PersonaImageReference, PersonaProfile, PersonaProfileScope, PersonaProfileSnapshot } from './persona-profile.models';

@Injectable({ providedIn: 'root' })
export class PersonaProfileApiClient extends ApiBaseService {
  current(scope: PersonaProfileScope) {
    return this.core.get<PersonaProfileSnapshot>(this.profileUrl(scope), scope.hub, undefined, false);
  }

  save(scope: PersonaProfileScope, profile: PersonaProfile, expectedRevision: number) {
    return this.core.request<{ revision: number; content_hash: string }>('PUT', this.profileUrl(scope), scope.hub, {
      body: { profile, expected_revision: expectedRevision },
    });
  }

  image(scope: PersonaProfileScope, artifactId: string) {
    return this.core.get<{ reference: PersonaImageReference }>(
      `${this.base(scope)}/images/${encodeURIComponent(artifactId)}/reference`, scope.hub, undefined, false,
    ).pipe(map(result => result.reference));
  }

  preview(scope: PersonaProfileScope, artifactId: string) {
    return this.core.requestBlob(`${this.base(scope)}/images/${encodeURIComponent(artifactId)}/preview`, scope.hub).pipe(
      map(response => {
        if (!response.body || response.body.type !== 'image/png' || response.body.size > 5 * 1024 * 1024) {
          throw new Error('persona_preview_invalid');
        }
        return response.body;
      }),
    );
  }

  private base(scope: PersonaProfileScope): string {
    return `${scope.hub.replace(/\/+$/, '')}/api/persona-media/v1/projects/${encodeURIComponent(scope.project)}`;
  }

  private profileUrl(scope: PersonaProfileScope): string {
    return `${this.base(scope)}/organizations/${encodeURIComponent(scope.organization)}/profiles/${scope.kind}/${encodeURIComponent(scope.owner)}`;
  }
}
