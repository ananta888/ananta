"""Bestellmodul: Auftragsverwaltung des Beispielshops."""


class OrderService:
    def create_order(self, customer_id: str, items: list) -> dict:
        return {"customer_id": customer_id, "items": items, "status": "open"}

    def create_invoice_for_order(self, order: dict) -> dict:
        """Rechnungserzeugung soll ins Bestellmodul: Einstiegspunkt."""
        return {"order": order, "invoice_status": "draft"}
