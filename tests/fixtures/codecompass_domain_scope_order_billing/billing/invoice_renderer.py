"""Billing: bestehende Rechnungs-Rendering-Logik (Nachbardomain)."""


class InvoiceRenderer:
    def render_invoice(self, invoice: dict) -> str:
        return f"INVOICE for {invoice.get('order', {}).get('customer_id', '?')}"
