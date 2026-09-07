import { Injectable } from '@angular/core';
import { map } from 'rxjs';
import { ApiBaseService } from '../../../services/api-base.service';
import { PersonaEffectiveProfile, PersonaImageReference, PersonaVideoReference, PersonaProfile, PersonaProfileScope, PersonaProfileSnapshot } from './persona-profile.models';
import { videoPage, videoReference } from './persona-video-reference';
import { voicePage, voiceReference } from './persona-voice-reference';

@Injectable({ providedIn: 'root' })
export class PersonaProfileApiClient extends ApiBaseService {
  current(scope: PersonaProfileScope) {
    return this.core.get<PersonaProfileSnapshot>(this.profileUrl(scope), scope.hub, undefined, false);
  }

  effective(scope: PersonaProfileScope) {
    return this.core.get<PersonaEffectiveProfile>(`${this.profileUrl(scope)}/effective`, scope.hub, undefined, false);
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

  images(scope: PersonaProfileScope, cursor: string | null) {
    return this.core.request<{ items: readonly PersonaImageReference[]; next_cursor: string | null; purpose: 'preview' }>(
      'POST', `${this.base(scope)}/images/query`, scope.hub, { body: { cursor, limit: 20 } },
    );
  }

  video(scope: PersonaProfileScope, artifactId: string) {
    return this.core.get<{ reference: PersonaVideoReference }>(
      `${this.base(scope)}/videos/${encodeURIComponent(artifactId)}/reference`, scope.hub, undefined, false,
    ).pipe(map(result => videoReference(result.reference, scope.project, artifactId)));
  }

  voice(scope: PersonaProfileScope, artifactId: string) {
    return this.core.get<{ reference: unknown }>(
      `${this.base(scope)}/voices/${encodeURIComponent(artifactId)}/reference`, scope.hub, undefined, false,
    ).pipe(map(result => {
      if (!result || Object.keys(result).join() !== 'reference') throw new Error('persona_voice_reference_invalid');
      return voiceReference(result.reference, scope.project, artifactId);
    }));
  }

  voices(scope: PersonaProfileScope, cursor: string | null) {
    return this.core.request<unknown>(
      'POST', `${this.base(scope)}/voices/query`, scope.hub, { body: { cursor, limit: 20 } },
    ).pipe(map(result => voicePage(result, scope.project)));
  }

  videos(scope: PersonaProfileScope, cursor: string | null) {
    return this.core.request<unknown>(
      'POST', `${this.base(scope)}/videos/query`, scope.hub, { body: { cursor, limit: 20 } },
    ).pipe(map(result => videoPage(result, scope.project)));
  }

  preview(scope: PersonaProfileScope, artifactId: string) {
    return this.previewImage(scope, 'images', artifactId, 5 * 1024 * 1024);
  }

  videoPreview(scope: PersonaProfileScope, artifactId: string) {
    // Only the normalized private PNG preview; never an implicit MP4 download.
    return this.previewImage(scope, 'videos', artifactId, 350_000);
  }

  private previewImage(scope: PersonaProfileScope, kind: 'images' | 'videos', artifactId: string, maximum: number) {
    return this.core.requestBlob(`${this.base(scope)}/${kind}/${encodeURIComponent(artifactId)}/preview`, scope.hub).pipe(
      map(response => {
        if (!response.body || response.body.type !== 'image/png' || response.body.size > maximum) {
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
