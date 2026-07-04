import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, Observable } from 'rxjs';
import { tap } from 'rxjs/operators';
import { HubApiCoreService } from './hub-api-core.service';
import { AgentDirectoryService } from './agent-directory.service';

export interface ChatSession {
  id: string;
  name: string;
  icon: string;
  group: string;
  folder_id: string;
  session_type: string;
  type_description: string;
  last_message_preview: string;
  message_count: number;
  system_prompt: string;
  settings: Record<string, unknown>;
  settings_delta: Record<string, unknown>;
  created_at?: number;
  updated_at?: number;
}

export interface ChatFolder {
  id: string;
  name: string;
  icon: string;
  parent_id: string;
  color?: string;
  created_at?: number;
  updated_at?: number;
}

export interface ReorganizeProposal {
  folders: ChatFolder[];
  assignments: Record<string, string>;
  summary: string;
}

export interface ContextOverview {
  session_id: string;
  system_prompt: { text: string; chars: number; enabled: boolean };
  history: { enabled: boolean; max_turns: number; max_chars: number };
  summary: { enabled: boolean; max_chars: number };
  rag: { enabled: boolean; profile: string; top_k: number; max_chars: number };
}

export interface CreateSessionPayload {
  id?: string;
  name: string;
  icon?: string;
  group?: string;
  folder_id?: string;
  session_type?: string;
  type_description?: string;
  system_prompt?: string;
  settings?: Record<string, unknown>;
}

@Injectable({ providedIn: 'root' })
export class ChatSessionsService {
  private core = inject(HubApiCoreService);
  private dir = inject(AgentDirectoryService);

  readonly sessions$ = new BehaviorSubject<ChatSession[]>([]);
  readonly folders$ = new BehaviorSubject<ChatFolder[]>([]);
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
    this.loadFolders();
  }

  loadFolders(): void {
    const url = this.hubUrl;
    if (!url) return;
    this.core.get<ChatFolder[]>(`${url}/api/chat/folders`, url).subscribe({
      next: folders => {
        this.folders$.next(Array.isArray(folders) ? folders : []);
      },
      error: () => { /* silently ignore until endpoint is available */ },
    });
  }

  createFolder(name: string, icon?: string, parentId?: string): Observable<ChatFolder> {
    const url = this.hubUrl;
    const body: Partial<ChatFolder> = { name, icon: icon || '📁' };
    if (parentId) body.parent_id = parentId;
    return this.core.post<ChatFolder>(`${url}/api/chat/folders`, body, url).pipe(
      tap(() => this.loadFolders()),
    );
  }

  updateFolder(id: string, patch: Partial<ChatFolder>): Observable<ChatFolder> {
    const url = this.hubUrl;
    return this.core.patch<ChatFolder>(`${url}/api/chat/folders/${id}`, patch, url).pipe(
      tap(() => this.loadFolders()),
    );
  }

  deleteFolder(id: string): Observable<void> {
    const url = this.hubUrl;
    return this.core.delete<void>(`${url}/api/chat/folders/${id}`, url).pipe(
      tap(() => this.loadFolders()),
    );
  }

  aiReorganize(): Observable<ReorganizeProposal> {
    const url = this.hubUrl;
    return this.core.post<ReorganizeProposal>(`${url}/api/chat/sessions/ai-reorganize`, {}, url);
  }

  getContextOverview(sessionId: string): Observable<ContextOverview> {
    const url = this.hubUrl;
    return this.core.get<ContextOverview>(`${url}/api/chat/sessions/${sessionId}/context-overview`, url);
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
      group: payload.group || '',
      folder_id: payload.folder_id || '',
      session_type: payload.session_type || '',
      type_description: payload.type_description || '',
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

  update(sessionId: string, patch: Partial<Pick<ChatSession,
    'name' | 'icon' | 'group' | 'folder_id' | 'session_type' | 'type_description' | 'system_prompt' | 'settings'
  >>): void {
    const url = this.hubUrl;
    if (!url) return;
    this.core.patch<ChatSession>(`${url}/api/chat/sessions/${sessionId}`, patch, url).subscribe({
      next: () => this.load(),
      error: err => this.error$.next(String(err?.message || 'Fehler beim Aktualisieren')),
    });
  }

  /** Patch a single session setting optimistically (no full reload; background sync). */
  patchSetting(sessionId: string, key: string, value: unknown): void {
    const updated = this.sessions$.value.map(s => {
      if (s.id !== sessionId) return s;
      return { ...s, settings: { ...(s.settings || {}), [key]: value } };
    });
    this.sessions$.next(updated);
    const url = this.hubUrl;
    if (!url) return;
    this.core.patch<ChatSession>(
      `${url}/api/chat/sessions/${sessionId}`,
      { settings: { [key]: value } },
      url,
    ).subscribe({
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
