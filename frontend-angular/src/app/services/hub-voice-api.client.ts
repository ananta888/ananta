import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { HubApiCoreService } from './hub-api-core.service';

@Injectable({ providedIn: 'root' })
export class HubVoiceApiClient {
  private core = inject(HubApiCoreService);

  getCapabilities(baseUrl: string, token?: string): Observable<any> {
    return this.core.get<any>(`${baseUrl}/v1/voice/capabilities`, baseUrl, token, true);
  }

  transcribe(baseUrl: string, payload: { file: Blob | File; fileName?: string; language?: string }, token?: string): Observable<any> {
    const form = new FormData();
    form.append('file', payload.file, payload.fileName || 'audio.webm');
    if (payload.language) {
      form.append('language', payload.language);
    }
    return this.core.post<any>(`${baseUrl}/v1/voice/transcribe`, form, baseUrl, token, false, 120000);
  }

  command(baseUrl: string, payload: { file: Blob | File; fileName?: string; commandContext?: any }, token?: string): Observable<any> {
    const form = new FormData();
    form.append('file', payload.file, payload.fileName || 'audio.webm');
    if (payload.commandContext) {
      form.append('command_context', JSON.stringify(payload.commandContext));
    }
    return this.core.post<any>(`${baseUrl}/v1/voice/command`, form, baseUrl, token, false, 120000);
  }

  goal(
    baseUrl: string,
    payload: { file: Blob | File; fileName?: string; createTasks?: boolean; governanceMode?: string; approved?: boolean },
    token?: string,
  ): Observable<any> {
    const form = new FormData();
    form.append('file', payload.file, payload.fileName || 'audio.webm');
    form.append('create_tasks', payload.createTasks ? 'true' : 'false');
    form.append('approved', payload.approved ? 'true' : 'false');
    if (payload.governanceMode) {
      form.append('governance_mode', payload.governanceMode);
    }
    return this.core.post<any>(`${baseUrl}/v1/voice/goal`, form, baseUrl, token, false, 120000);
  }
}
