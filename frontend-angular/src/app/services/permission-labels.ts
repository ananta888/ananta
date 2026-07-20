/**
 * T11: Mapping between UI labels and backend permission keys.
 *
 * The UI uses long German labels; the backend and the validators
 * use the short snake_case keys. The mapping is centralised so
 * that the same key is sent in every request and the same key
 * is checked in every guard.
 */
import { ALL_PERMISSIONS, PermissionKey, PermissionSet } from './pair-view-sync.types';

export const PERMISSION_CONTRACT_VERSION = 1 as const;
export const LEGACY_PERMISSION_ALIAS_EXPIRES_AT_MS = Date.UTC(2027, 0, 1);

const LEGACY_ALIASES = Object.freeze({
  cursor: 'remote_cursor',
  control: 'remote_control',
  artifact_view: 'artifact_share',
  annotation: 'artifact_share',
} as const);

export class PermissionContractError extends Error {
  constructor(readonly reasonCode: string, readonly field?: string) {
    super(field ? `${reasonCode}:${field}` : reasonCode);
  }
}

export interface PermissionLabel {
  key: PermissionKey;
  label: string;
  description: string;
  /** UI defaults this permission to checked? */
  defaultChecked: boolean;
  /** When true, requires an explicit grant dialog. */
  requiresExplicitGrant: boolean;
}

export const PERMISSION_LABELS: Readonly<Record<PermissionKey, PermissionLabel>> = Object.freeze({
  chat: {
    key: 'chat',
    label: 'Chat',
    description: 'Partner kann am Session-Chat teilnehmen.',
    defaultChecked: true,
    requiresExplicitGrant: false,
  },
  view_tui: {
    key: 'view_tui',
    label: 'TUI-Ansicht',
    description: 'Partner sieht dieselbe Ansicht wie der Owner (read-only).',
    defaultChecked: true,
    requiresExplicitGrant: false,
  },
  remote_cursor: {
    key: 'remote_cursor',
    label: 'Remote-Cursor',
    description: 'Partner-Cursor wird im Overlay angezeigt.',
    defaultChecked: false,
    requiresExplicitGrant: false,
  },
  remote_control: {
    key: 'remote_control',
    label: 'Steuerung',
    description: 'Partner kann aktiv in der Session navigieren und auslösen.',
    defaultChecked: false,
    requiresExplicitGrant: true,
  },
  artifact_share: {
    key: 'artifact_share',
    label: 'Artefakte sehen',
    description: 'Partner kann referenzierte Artefakte öffnen.',
    defaultChecked: true,
    requiresExplicitGrant: false,
  },
});

/** Build a backend-compatible permissions dict from a UI selection. */
export function permissionsFromUiSelection(
  selection: Partial<Record<PermissionKey, boolean>>,
): PermissionSet {
  const out: Record<PermissionKey, boolean> = {
    chat: false,
    view_tui: false,
    remote_cursor: false,
    artifact_share: false,
    remote_control: false,
  };
  for (const key of ALL_PERMISSIONS) {
    out[key] = selection[key] === true;
  }
  return Object.freeze(out);
}

/** Strictly parse Hub or time-bounded v0 permission documents. */
export function normalizePermissions(
  raw: Readonly<Record<string, unknown>>,
  nowMs = Date.now(),
): PermissionSet {
  const out: Record<PermissionKey, boolean> = {
    chat: true, view_tui: false, remote_cursor: false,
    artifact_share: false, remote_control: false,
  };
  const assigned = new Map<PermissionKey, boolean>();
  for (const [sourceKey, value] of Object.entries(raw)) {
    if (typeof value !== 'boolean') {
      throw new PermissionContractError('permission_value_not_boolean', sourceKey);
    }
    let key: PermissionKey;
    if ((ALL_PERMISSIONS as readonly string[]).includes(sourceKey)) {
      key = sourceKey as PermissionKey;
    } else if (sourceKey in LEGACY_ALIASES) {
      if (nowMs >= LEGACY_PERMISSION_ALIAS_EXPIRES_AT_MS) {
        throw new PermissionContractError('permission_alias_expired', sourceKey);
      }
      key = LEGACY_ALIASES[sourceKey as keyof typeof LEGACY_ALIASES];
    } else {
      throw new PermissionContractError('permission_unknown', sourceKey);
    }
    const previous = assigned.get(key);
    if (previous !== undefined && previous !== value) {
      throw new PermissionContractError('permission_conflict', key);
    }
    assigned.set(key, value);
    out[key] = value;
  }
  return Object.freeze(out);
}

/** Check whether a permission is granted; null/undefined is "not granted". */
export function hasPermission(perms: PermissionSet | null | undefined, key: PermissionKey): boolean {
  if (!perms) return false;
  return perms[key] === true;
}
