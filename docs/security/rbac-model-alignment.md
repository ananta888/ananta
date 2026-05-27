# RBAC Model Alignment Review

**Track:** KRITIS-P3-ENTERPRISE-RBAC  
**Task:** K3-RBAC-T01  
**Status:** Done

## Canonical Roles

| Role    | Description                                           |
|---------|-------------------------------------------------------|
| `admin` | Full system access including user management, policies, audit and terminal |
| `user`  | Operational access: tasks, workspace, own artifacts    |

No additional roles are currently modelled. A `viewer` role is partially referenced in `TerminalPolicyService` defaults but is not a first-class auth role — it has no JWT claim and must not be used in enforcement logic.

## Action Classes

| Action Class        | Description                                              | admin | user |
|---------------------|----------------------------------------------------------|-------|------|
| `view_any`          | Read any resource regardless of ownership                | ✓     |      |
| `view_own`          | Read own tasks, workspace, artifacts                     | ✓     | ✓    |
| `operate_tasks`     | Create, run, cancel tasks and goals                      | ✓     | ✓    |
| `manage_templates`  | Create, edit, delete system templates                    | ✓     |      |
| `admin_users`       | Create, delete, reset, reassign users                    | ✓     |      |
| `admin_policies`    | Create and modify context access policies                | ✓     |      |
| `terminal_access`   | Access terminal sessions on worker or hub targets        | ✓     |      |
| `write_approvals`   | Approve mutation gate requests                           | ✓     |      |
| `audit_read`        | Read audit log events                                    | ✓     |      |
| `diagnostics_read`  | View admin diagnostics and policy summaries              | ✓     |      |

## Backend Enforcement

- Role is stored in `UserDB.role` (`user` | `admin`).
- JWT payload carries `role` claim; `g.is_admin` is set from `role == "admin"`.
- `@admin_required` decorator enforces `g.is_admin` for sensitive endpoints.
- `TerminalPolicyService._DEFAULT_ROLE_PERMISSIONS` maps roles to fine-grained terminal permissions.

## Frontend Gaps Identified (before this fix)

1. `authGuard` only checks `isLoggedIn()` — no role enforcement at route level.
2. Admin routes (`audit-log`, `context-access-policy`, `user-management`) are in nav metadata as `adminOnly` but lack an Angular route guard.
3. Role checks scattered ad hoc in individual components (`templates.component.ts`, `teams.component.ts`) instead of a shared service.
4. No Angular directive for declarative permission gating in templates.

## Resolution

- `PermissionService` is the single source of truth for frontend role/action checks.
- `adminGuard` gates all `adminOnly` routes.
- `hasPermission` structural directive hides UI elements for unauthorized roles.
- All new admin routes use `canActivate: [adminGuard]`.
