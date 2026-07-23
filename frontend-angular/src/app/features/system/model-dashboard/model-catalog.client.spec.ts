import { canUseModelMutation } from './model-catalog.client';

describe('model dashboard capabilities', () => {
  it('grants only explicit capabilities or admin compatibility', () => {
    expect(canUseModelMutation({ role: 'admin' }, 'model_catalog.refresh')).toBe(true);
    expect(canUseModelMutation(
      { role: 'operator', capabilities: ['model_catalog.set_default'] },
      'model_catalog.set_default',
    )).toBe(true);
    expect(canUseModelMutation({ role: 'operator' }, 'model_catalog.refresh')).toBe(false);
  });

  it('denies mutations in auth-disabled mode, including admin claims', () => {
    expect(canUseModelMutation(
      { role: 'admin', auth_mode: 'auth_disabled' },
      'model_catalog.refresh',
    )).toBe(false);
  });
});

