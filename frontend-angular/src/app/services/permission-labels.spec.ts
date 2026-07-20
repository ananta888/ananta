import { describe, expect, it } from 'vitest';

import {
  LEGACY_PERMISSION_ALIAS_EXPIRES_AT_MS,
  PermissionContractError,
  normalizePermissions,
  permissionsFromUiSelection,
} from './permission-labels';

describe('canonical Pair View permissions', () => {
  it('writes canonical backend names only', () => {
    expect(permissionsFromUiSelection({ remote_cursor: true, remote_control: true })).toEqual({
      chat: false, view_tui: false, remote_cursor: true,
      artifact_share: false, remote_control: true,
    });
  });

  it('reads documented aliases before their deadline', () => {
    expect(normalizePermissions(
      { cursor: true, control: true, artifact_view: true, annotation: true },
      LEGACY_PERMISSION_ALIAS_EXPIRES_AT_MS - 1,
    )).toMatchObject({ remote_cursor: true, remote_control: true, artifact_share: true });
  });

  it.each([
    [{ unknown: true }, 'permission_unknown'],
    [{ chat: 'false' }, 'permission_value_not_boolean'],
    [{ remote_control: false, control: true }, 'permission_conflict'],
  ])('rejects invalid document %o', (raw, reason) => {
    expect(() => normalizePermissions(raw, LEGACY_PERMISSION_ALIAS_EXPIRES_AT_MS - 1))
      .toThrowError(expect.objectContaining<Partial<PermissionContractError>>({ reasonCode: reason }));
  });

  it('rejects aliases at the fixed expiry', () => {
    expect(() => normalizePermissions({ cursor: true }, LEGACY_PERMISSION_ALIAS_EXPIRES_AT_MS))
      .toThrow('permission_alias_expired');
  });
});
