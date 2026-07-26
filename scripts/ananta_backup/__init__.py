"""Host-side backup and restore support for the local Ananta stack.

The package deliberately has no eager service imports.  Backup and restore are
independent host-side entry points; importing one must not load the other's
dependencies or perform infrastructure discovery.
"""
