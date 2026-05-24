import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';

export interface TuiToolProfile {
  id: string;
  command: string;
  args: string[];
  working_directory?: string;
}

export interface FiletypeRule {
  match: string;
  editor: string;
  args?: string[];
}

export interface TuiToolsConfig {
  default_editor: string;
  allow_environment_editor: boolean;
  allowed_tools: string[];
  filetype_editors: FiletypeRule[];
  tool_profiles: TuiToolProfile[];
}

export interface EditorResolveResult {
  editor_id: string;
  command: string;
  argv: string[];
  reason: string;
  readonly_supported: boolean;
}

@Injectable({ providedIn: 'root' })
export class TuiToolsService {
  private core = inject(HubApiCoreService);

  listTools(baseUrl: string, token?: string): Observable<TuiToolProfile[]> {
    return this.core.get<TuiToolProfile[]>(`${baseUrl}/tui/tools`, baseUrl, token);
  }

  resolveEditor(baseUrl: string, path: string, token?: string): Observable<EditorResolveResult> {
    const encoded = encodeURIComponent(path);
    return this.core.get<EditorResolveResult>(
      `${baseUrl}/tui/editors/resolve?path=${encoded}`,
      baseUrl,
      token,
    );
  }

  launchTool(baseUrl: string, toolId: string, workspace: string, token?: string): Observable<{ session_id: string }> {
    return this.core.post<{ session_id: string }>(
      `${baseUrl}/tui/tools/launch`,
      { tool_id: toolId, workspace },
      baseUrl,
      token,
    );
  }

  openEditor(
    baseUrl: string,
    filePath: string,
    workspace: string,
    opts: { withEditor?: string; readonly?: boolean } = {},
    token?: string,
  ): Observable<{ session_id: string; editor_id: string }> {
    return this.core.post<{ session_id: string; editor_id: string }>(
      `${baseUrl}/tui/editors/open`,
      { file_path: filePath, workspace, ...opts },
      baseUrl,
      token,
    );
  }
}
