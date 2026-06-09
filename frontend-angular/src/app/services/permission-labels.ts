/**
 * T11: Mapping between UI labels and backend permission keys.
 *
 * The UI uses long German labels; the backend and the validators
 * use the short snake_case keys. The mapping is centralised so
 * that the same key is sent in every request and the same key
 * is checked in every guard.
 */
import { ALL_PERMISSIONS, PermissionKey, PermissionSet } from './pair-view-sync.types';

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
  cursor: {
    key: 'cursor',
    label: 'Remote-Cursor',
    description: 'Partner-Cursor wird im Overlay angezeigt.',
    defaultChecked: false,
    requiresExplicitGrant: false,
  },
  control: {
    key: 'control',
    label: 'Steuerung',
    description: 'Partner kann aktiv in der Session navigieren und auslösen.',
    defaultChecked: false,
    requiresExplicitGrant: true,
  },
  artifact_view: {
    key: 'artifact_view',
    label: 'Artefakte sehen',
    description: 'Partner kann referenzierte Artefakte öffnen.',
    defaultChecked: true,
    requiresExplicitGrant: false,
  },
  annotation: {
    key: 'annotation',
    label: 'Annotationen',
    description: 'Partner darf Anmerkungen hinterlassen.',
    defaultChecked: false,
    requiresExplicitGrant: true,
  },
});

/** Build a backend-compatible permissions dict from a UI selection. */
export function permissionsFromUiSelection(
  selection: Partial<Record<PermissionKey, boolean>>,
): PermissionSet {
  const out: Record<PermissionKey, boolean> = {
    chat: false,
    view_tui: false,
    cursor: false,
    control: false,
    artifact_view: false,
    annotation: false,
  };
  for (const key of ALL_PERMISSIONS) {
    out[key] = selection[key] === true;
  }
  return Object.freeze(out);
}

/** Check whether a permission is granted; null/undefined is "not granted". */
export function hasPermission(perms: PermissionSet | null | undefined, key: PermissionKey): boolean {
  if (!perms) return false;
  return perms[key] === true;
}
