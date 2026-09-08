"""Auxiliary observation ledger; no change to the dialog/phase/task services."""

from agent.repositories.meet_dialog_diagnostics import SqlDialogDiagnostics
from agent.services.meet_dialog_diagnostics import MeetDialogDiagnostics


def configure_dialog_diagnostics(app, engine, binding):
    ledger = SqlDialogDiagnostics(engine)
    ledger.initialize()
    app.extensions["meet_dialog_diagnostics"] = MeetDialogDiagnostics(ledger, binding)
