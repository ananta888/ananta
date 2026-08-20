import { Injectable } from '@angular/core';
import { Observable, map } from 'rxjs';
import {
  OperationPolicyInventoryDto,
  OperationPolicyInventoryItemDto,
} from '../models/operation-policy.model';
import { ApiBaseService } from './api-base.service';

function recordOf(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function stringOf(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

function itemOf(value: unknown): OperationPolicyInventoryItemDto | null {
  const item = recordOf(value);
  const decision = recordOf(item['decision']);
  const operationId = stringOf(item['operation_id']);
  if (!operationId || typeof decision['allowed'] !== 'boolean') return null;
  return {
    operation_id: operationId,
    transport: stringOf(item['transport']) || 'unknown',
    name_or_route: stringOf(item['name_or_route']),
    http_method: stringOf(item['http_method']) || null,
    access_class: stringOf(item['access_class']) || 'unknown',
    risk_class: stringOf(item['risk_class']) || 'unknown',
    lifecycle: stringOf(item['lifecycle']) || 'unknown',
    description: stringOf(item['description']),
    policy_status: stringOf(item['policy_status']) || (decision['allowed'] ? 'allowed' : 'denied'),
    group_ids: stringList(item['group_ids']),
    decision: {
      allowed: decision['allowed'],
      reason_code: stringOf(decision['reason_code']) || 'unknown',
      matched_rule_id: stringOf(decision['matched_rule_id']) || 'unknown',
    },
  };
}

export function normalizeOperationPolicyInventory(value: unknown): OperationPolicyInventoryDto {
  const envelope = recordOf(value);
  const data = recordOf(envelope['data'] ?? envelope);
  if (!Array.isArray(data['items'])) throw new Error('Malformed operation-policy response');
  const items = data['items'].map(itemOf).filter((item): item is OperationPolicyInventoryItemDto => item !== null);
  return {
    schema: stringOf(data['schema']) || 'ananta.operation_policy_inventory.unknown',
    policy: recordOf(data['policy']),
    items,
    count: Number.isInteger(data['count']) ? Number(data['count']) : items.length,
    registered_count: Number.isInteger(data['registered_count']) ? Number(data['registered_count']) : items.length,
  };
}

@Injectable({ providedIn: 'root' })
export class OperationPolicyApiService extends ApiBaseService {
  inventory(baseUrl: string): Observable<OperationPolicyInventoryDto> {
    return this.core
      .get<unknown>(`${baseUrl}/governance/operations`, baseUrl)
      .pipe(map(normalizeOperationPolicyInventory));
  }
}
