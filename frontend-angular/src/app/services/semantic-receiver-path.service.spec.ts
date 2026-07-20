import { SemanticReceiverPathService } from './semantic-receiver-path.service';

describe('SemanticReceiverPathService', () => {
  it('keeps receiver choices independent and fail-closes unknown admission to ordinary', () => {
    const service = new SemanticReceiverPathService();
    service.setReceivers([{ receiverId: 'bob', label: 'Bob' }, { receiverId: 'carol', label: 'Carol' }]);
    service.request('bob', 'sfu');
    service.request('carol', 'ordinary');
    expect(service.rows$.value).toEqual([
      expect.objectContaining({ receiverId: 'bob', preference: 'sfu', effectivePath: 'ordinary', pendingHubConfirmation: true }),
      expect.objectContaining({ receiverId: 'carol', preference: 'ordinary', effectivePath: 'ordinary', pendingHubConfirmation: false }),
    ]);
  });

  it('uses SFU only for receivers present in the connected Hub admission', () => {
    const service = new SemanticReceiverPathService();
    service.setReceivers([{ receiverId: 'bob', label: 'Bob' }, { receiverId: 'carol', label: 'Carol' }]);
    service.setHubState({
      sfuConnected: true, sfuFeatureEnabled: true, sfuAuthorizedReceiverIds: new Set(['bob']),
    });
    expect(service.rows$.value.find(row => row.receiverId === 'bob')?.effectivePath).toBe('sfu');
    expect(service.rows$.value.find(row => row.receiverId === 'carol')?.effectivePath).toBe('ordinary');
  });

  it('distinguishes a completed Hub denial from an in-flight preference', () => {
    const service = new SemanticReceiverPathService();
    service.setReceivers([{ receiverId: 'bob', label: 'Bob' }]);
    service.request('bob', 'sfu');
    service.setOperationState('bob', true, 'receiver_path_hub_confirmation_required');
    expect(service.rows$.value[0].pendingHubConfirmation).toBe(true);
    service.setOperationState('bob', false, 'sfu_membership_required');
    expect(service.rows$.value[0]).toMatchObject({
      effectivePath: 'ordinary', pendingHubConfirmation: false, reasonCode: 'sfu_membership_required',
    });
  });
});
