import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable, map } from 'rxjs';

@Injectable({ providedIn: 'root' })
export class SourceChatService {
  constructor(private readonly http: HttpClient) {}

  ask(sourceId: string, prompt: string, includeInsights: boolean, includeNotes: boolean): Observable<any> {
    return this.http.post<any>(`/sources/${encodeURIComponent(sourceId)}/chat`, {
      prompt,
      include_insights: includeInsights,
      include_notes: includeNotes,
    }).pipe(map(payload => payload?.data || {}));
  }
}
