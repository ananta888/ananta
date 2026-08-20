import { normalizeOperationPolicyInventory } from './operation-policy-api.service';

describe('operation policy inventory contract', () => {
  it('normalizes the shared envelope and preserves unknown enum values', () => {
    const value = normalizeOperationPolicyInventory({
      status: 'success',
      data: {
        schema: 'ananta.operation_policy_inventory.v1',
        policy: { revision: 3 },
        future_top_level_field: true,
        items: [{
          operation_id: 'api.config.get',
          transport: 'api',
          name_or_route: '/config',
          http_method: 'GET',
          access_class: 'read',
          risk_class: 'low',
          lifecycle: 'future_state',
          policy_status: 'allowed',
          group_ids: ['api.read.v1'],
          decision: { allowed: true, reason_code: 'operation_allowed', matched_rule_id: 'allow:group:api.read.v1' },
          future_item_field: 'ignored safely',
        }],
      },
    });
    expect(value.items[0].lifecycle).toBe('future_state');
    expect(value.items[0].decision.allowed).toBeTruthy();
  });

  it('rejects malformed item collections', () => {
    expect(() => normalizeOperationPolicyInventory({ data: { items: 'not-an-array' } })).toThrowError(
      'Malformed operation-policy response',
    );
  });
});
