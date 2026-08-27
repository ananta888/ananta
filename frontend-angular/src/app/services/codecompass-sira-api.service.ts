import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiBaseService } from './api-base.service';

export interface CodeCompassSiraStatus {
  schema: 'codecompass.sira-status.v1';
  status: 'disabled' | 'ready' | 'degraded';
  config_status: string;
  config_reason: string;
  config: Record<string, unknown>;
  flags: Record<string, { enabled: boolean; status: string; reason: string; mode?: string }>;
  index: Record<string, unknown>;
  rollout: {
    mode: string;
    result_affecting: boolean;
    shadow_non_effecting: boolean;
    kill_switches: Record<string, boolean>;
  };
}

@Injectable({ providedIn: 'root' })
export class CodeCompassSiraApiService extends ApiBaseService {
  status(baseUrl: string, token?: string): Observable<{ data: CodeCompassSiraStatus }> {
    return this.core.get<{ data: CodeCompassSiraStatus }>(
      `${baseUrl}/api/codecompass/sira/status`,
      baseUrl,
      token,
    );
  }
}
