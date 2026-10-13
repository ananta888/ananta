"""Bounded Hub reconciliation; never dispatches or fabricates execution outcomes."""


def reconcile_native_dispatches(*, journal, task_repository, tenant_id: str, limit: int = 100,
                               complete_pi=None) -> dict:
    results = []
    for task_id in journal.pending_task_ids(tenant_id=tenant_id, limit=limit):
        try:
            task = task_repository.get_by_id(task_id)
            if task is None or task.tenant_id != tenant_id:
                raise ValueError("native_dispatch_task_scope_denied")
            if task.task_kind == "pi_coding_agent":
                if complete_pi is None:
                    raise ValueError("pi_recovery_evidence_composition_required")
                complete_pi(task_id=task.id)
            journal.reconcile(task=task)
        except Exception:
            # Transport/SQL/receipt uncertainty is a bounded unresolved result.
            # It never authorizes resend, changes Task status or bypasses policy.
            results.append({"task_id": task_id, "state": "unresolved",
                            "reason_code": "native_dispatch_reconciliation_unresolved"})
        else:
            results.append({"task_id": task_id, "state": "accepted"})
    return {"schema": "ananta.native-dispatch-reconciliation.v1", "tenant_id": tenant_id,
            "scanned": len(results), "accepted": sum(item["state"] == "accepted" for item in results),
            "unresolved": sum(item["state"] == "unresolved" for item in results), "results": results}
