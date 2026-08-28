import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { ApiBaseService } from '../../../services/api-base.service';
import {
  KnowledgeExpertControlCommand,
  KnowledgeExpertControlSnapshot,
} from './knowledge-expert.models';

@Injectable({ providedIn: 'root' })
export class KnowledgeExpertApiService extends ApiBaseService {
  snapshot(hubUrl: string): Observable<KnowledgeExpertControlSnapshot> {
    return this.core.get<KnowledgeExpertControlSnapshot>(
      `${hubUrl}/api/knowledge-experts`, hubUrl, undefined, false,
    );
  }

  command(hubUrl: string, command: KnowledgeExpertControlCommand): Observable<Record<string, unknown>> {
    return this.core.post<Record<string, unknown>>(
      `${hubUrl}/api/knowledge-experts/commands`, command, hubUrl, undefined, false,
    );
  }
}
