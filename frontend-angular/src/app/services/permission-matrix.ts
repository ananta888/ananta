export type AnantaRole = 'admin' | 'user';

export type ActionClass =
  | 'view_any'
  | 'view_own'
  | 'operate_tasks'
  | 'manage_templates'
  | 'admin_users'
  | 'admin_policies'
  | 'terminal_access'
  | 'write_approvals'
  | 'audit_read'
  | 'diagnostics_read';

export const PERMISSION_MATRIX: Readonly<Record<AnantaRole, ReadonlySet<ActionClass>>> = {
  admin: new Set<ActionClass>([
    'view_any',
    'view_own',
    'operate_tasks',
    'manage_templates',
    'admin_users',
    'admin_policies',
    'terminal_access',
    'write_approvals',
    'audit_read',
    'diagnostics_read',
  ]),
  user: new Set<ActionClass>([
    'view_own',
    'operate_tasks',
  ]),
} as const;

export function roleFromString(raw: string | null | undefined): AnantaRole {
  if (raw === 'admin') return 'admin';
  return 'user';
}
