"""Business names without invented upstream parameter names or price fields."""

from .client import McDonaldClient

OPERATIONS = {
    "stores": "query-nearby-stores",
    "menu": "query-meals",
    "detail": "query-meal-detail",
    "quote": "calculate-price",
    "coupons": "query-store-coupons",
    "nutrition": "list-nutrition-foods",
}


class McDonaldAdapter:
    def __init__(self, client: McDonaldClient):
        self.client = client

    async def execute(self, operation: str, arguments: dict) -> dict:
        # Keep upstream payloads intact until the real business schema is verified.
        return await self.client.call(OPERATIONS[operation], arguments)
