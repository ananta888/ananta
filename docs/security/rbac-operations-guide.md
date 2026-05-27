# RBAC Operations Guide

**Track:** KRITIS-P3-ENTERPRISE-RBAC  
**Task:** K3-RBAC-T09  
**Audience:** Operators, system administrators

## Overview

Ananta uses a two-role model: **admin** and **user**. Role assignment is stored in the database and carried in JWT claims. The frontend enforces roles at route level and on individual UI actions.

## Roles and Capabilities

| Role  | Can do                                                             | Cannot do                                      |
|-------|--------------------------------------------------------------------|------------------------------------------------|
| admin | Manage users, view audit logs, approve proposals, change policies, access terminals | — |
| user  | Run tasks, view own results, use workspace                         | Manage users, approve proposals, view audit logs, access terminal, change policies |

## Managing Users (Admin UI)

Navigate to **System → Benutzerverwaltung** (requires admin login).

### Create a user
1. Enter username, password, and role.
2. Click **Benutzer Erstellen**.
3. The audit log records the `user_created` event.

### Change a role
1. Find the user in the table.
2. Change the role dropdown.
3. The change is applied immediately and logged as `user_role_updated`.

### Delete a user
1. Click **Löschen** next to the target user.
2. Confirm the dialog.
3. Event `user_deleted` is written to the audit log.

### Reset a password
1. Click **Reset PW** next to the user.
2. Enter the new password. Complexity rules apply (≥12 chars, upper, lower, digit, special).
3. Event `password_reset` is logged.

## Audit and Visibility

Navigate to **System → Rollenänderungen** to see a filtered audit view of:
- `user_role_updated` — who changed which user to which role
- `user_created` / `user_deleted` — user lifecycle events
- `account_lockout` / `ip_banned` — security events

The full audit log is at **System → Audit-Logs**.

## Backend vs. UI Enforcement

- Backend: `@admin_required` decorator on all user management and sensitive endpoints. Role comes from the verified JWT.
- Frontend: `adminGuard` blocks route access; `PermissionService.can()` gates individual UI actions.
- These layers are independent. Removing frontend gating does not bypass the backend.

## Deployment Checklist for Enterprise Use

- [ ] Set a strong `SECRET_KEY` (≥32 bytes) for JWT signing.
- [ ] Enable MFA for admin accounts.
- [ ] Restrict the `/users` and `/users/<username>/role` endpoints to internal networks at the load balancer level.
- [ ] Review **Admin-Diagnose** after deployment: confirm mutation gate (`Mutation Gate (Approval)`) is active.
- [ ] Rotate admin credentials after initial setup.

## Known Limitations

- Only two roles. Granular per-resource permissions are not currently supported.
- Role changes take effect on the next request (JWT must be re-issued on next login for full effect if the old token is still valid within its TTL).
- The `viewer` role referenced in `TerminalPolicyService` defaults is not a first-class auth role; do not rely on it without further implementation.
