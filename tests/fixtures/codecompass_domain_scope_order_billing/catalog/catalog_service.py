"""Artikelkatalog: Produktstammdaten — nicht Teil des Bestellmoduls."""


class CatalogService:
    def find_article(self, sku: str) -> dict:
        return {"sku": sku, "name": "Beispielartikel"}

    def invoice_relevant_price(self, sku: str) -> float:
        """Erwähnt 'invoice', gehört aber zum Katalog, nicht zur Bestellung."""
        return 9.99
