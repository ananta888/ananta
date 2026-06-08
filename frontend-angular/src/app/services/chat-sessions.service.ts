import { Injectable, inject } from '@angular/core';
import { BehaviorSubject } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';
import { AgentDirectoryService } from './agent-directory.service';

export interface ChatSession {
  id: string;
  name: string;
  icon: string;
  system_prompt: string;
  settings: Record<string, unknown>;
  created_at?: number;
  updated_at?: number;
}

export interface CreateSessionPayload {
  id?: string;
  name: string;
  icon?: string;
  system_prompt?: string;
  settings?: Record<string, unknown>;
}

@Injectable({ providedIn: 'root' })
export class ChatSessionsService {
  private core = inject(HubApiCoreService);
  private dir = inject(AgentDirectoryService);

  readonly sessions$ = new BehaviorSubject<ChatSession[]>([]);
  readonly activeSessionId$ = new BehaviorSubject<string>('');
  readonly loading$ = new BehaviorSubject<boolean>(false);
  readonly error$ = new BehaviorSubject<string>('');

  private get hubUrl(): string {
    return this.dir.list().find(a => a.role === 'hub')?.url ?? '';
  }

  load(): void {
    const url = this.hubUrl;
    if (!url) return;
    this.loading$.next(true);
    this.core.get<ChatSession[]>(`${url}/api/chat/sessions`, url).subscribe({
      next: sessions => {
        const list = Array.isArray(sessions) ? sessions : [];
        this.sessions$.next(list);
        if (!this.activeSessionId$.value && list.length) {
          this.activeSessionId$.next(list[0].id);
        }
        this.loading$.next(false);
        this.error$.next('');
      },
      error: err => {
        this.error$.next(String(err?.message || 'Fehler beim Laden der Sessions'));
        this.loading$.next(false);
      },
    });
  }

  activate(sessionId: string): void {
    const url = this.hubUrl;
    if (!url) return;
    this.activeSessionId$.next(sessionId);
    this.core.post<{ message: string }>(`${url}/api/chat/sessions/${sessionId}/activate`, {}, url).subscribe({
      next: () => this.load(),
      error: err => this.error$.next(String(err?.message || 'Fehler beim Aktivieren')),
    });
  }

  create(payload: CreateSessionPayload): void {
    const url = this.hubUrl;
    if (!url) return;
    const body = {
      id: payload.id || `session-${Date.now()}`,
      name: payload.name,
      icon: payload.icon || '💬',
      system_prompt: payload.system_prompt || '',
      settings: payload.settings || {},
    };
    this.core.post<ChatSession>(`${url}/api/chat/sessions`, body, url).subscribe({
      next: s => {
        this.load();
        this.activeSessionId$.next(s.id);
      },
      error: err => this.error$.next(String(err?.message || 'Fehler beim Erstellen')),
    });
  }

  update(sessionId: string, patch: Partial<Pick<ChatSession, 'name' | 'icon' | 'system_prompt' | 'settings'>>): void {
    const url = this.hubUrl;
    if (!url) return;
    this.core.patch<ChatSession>(`${url}/api/chat/sessions/${sessionId}`, patch, url).subscribe({
      next: () => this.load(),
      error: err => this.error$.next(String(err?.message || 'Fehler beim Aktualisieren')),
    });
  }

  remove(sessionId: string): void {
    const url = this.hubUrl;
    if (!url) return;
    this.core.delete<void>(`${url}/api/chat/sessions/${sessionId}`, url).subscribe({
      next: () => {
        if (this.activeSessionId$.value === sessionId) {
          this.activeSessionId$.next('');
        }
        this.load();
      },
      error: err => this.error$.next(String(err?.message || 'Fehler beim Löschen')),
    });
  }
}
