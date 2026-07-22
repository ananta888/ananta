import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';

import { HubApiCoreService } from './hub-api-core.service';
import type { ReceiverQualityObservationContract } from './sfu-broadcast-contracts';

export interface SfuQualityApiScope {
  readonly hubUrl: string;
  readonly sessionId: string;
  readonly membershipEpoch: number;
  readonly subscriptionRef: string;
}

export interface SfuQualityApiAck {
  readonly status: 'accepted' | 'duplicate' | 'dropped';
  readonly reasonCode: string;
}

@Injectable({ providedIn: 'root' })
export class SfuBroadcastQualityApiService {
  private readonly core = inject(HubApiCoreService);

  submit(
    scope: SfuQualityApiScope,
    report: ReceiverQualityObservationContract,
  ): Observable<SfuQualityApiAck> {
    const base = normalizeBase(scope.hubUrl);
    const query = new URLSearchParams({
      session_id: identifier(scope.sessionId),
      membership_epoch: String(positiveInteger(scope.membershipEpoch)),
    });
    const subscription = encodeURIComponent(identifier(scope.subscriptionRef));
    return this.core.request<unknown>(
      'POST',
      `${base}/v1/semantic-media/sfu/quality-observations/${subscription}?${query.toString()}`,
      base,
      { body: report, timeoutMs: 4_000 },
    ).pipe(map(parseAck));
  }
}

function parseAck(raw: unknown): SfuQualityApiAck {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('sfu_quality_ack_invalid');
  const value = raw as Record<string, unknown>;
  const status = String(value['status'] ?? '');
  const reasonCode = String(value['reason_code'] ?? '');
  if (value['ok'] !== true || !['accepted', 'duplicate', 'dropped'].includes(status)
      || !/^[a-z][a-z0-9_]{2,119}$/.test(reasonCode)) throw new Error('sfu_quality_ack_invalid');
  return Object.freeze({ status: status as SfuQualityApiAck['status'], reasonCode });
}

function normalizeBase(value: string): string {
  const base = String(value || '').trim().replace(/\/+$/, '');
  if (!/^https?:\/\/[^\s]+$/.test(base)) throw new Error('sfu_quality_hub_url_invalid');
  return base;
}

function identifier(value: string): string {
  if (typeof value !== 'string' || !value || new TextEncoder().encode(value).byteLength > 128
      || /[\u0000-\u0020\u007f]/.test(value)) throw new Error('sfu_quality_identifier_invalid');
  return value;
}

function positiveInteger(value: number): number {
  if (!Number.isSafeInteger(value) || value < 1) throw new Error('sfu_quality_epoch_invalid');
  return value;
}
