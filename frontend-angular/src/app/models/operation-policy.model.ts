export interface OperationPolicyDecisionDto {
  allowed: boolean;
  reason_code: string;
  matched_rule_id: string;
}

export interface OperationPolicyInventoryItemDto {
  operation_id: string;
  transport: string;
  name_or_route: string;
  http_method: string | null;
  access_class: string;
  risk_class: string;
  lifecycle: string;
  description: string;
  policy_status: string;
  group_ids: string[];
  decision: OperationPolicyDecisionDto;
}

export interface OperationPolicyInventoryDto {
  schema: string;
  policy: Record<string, unknown>;
  items: OperationPolicyInventoryItemDto[];
  count: number;
  registered_count: number;
}
