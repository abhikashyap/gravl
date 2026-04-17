"""Shopify Admin GraphQL client — thin httpx wrapper."""

from __future__ import annotations

from typing import Any, Iterator

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from gravl.db.credentials import get_cred

API_VERSION = "2025-01"


def test_connection() -> dict[str, Any]:
    """Module-level test hook used by scripts/onboard.py."""
    with ShopifyClient() as c:
        return c.test_connection()


class ShopifyClient:
    def __init__(self) -> None:
        self.domain = get_cred("shopify", "domain")
        self.token = get_cred("shopify", "admin_token")
        self._client = httpx.Client(
            base_url=f"https://{self.domain}/admin/api/{API_VERSION}",
            headers={
                "X-Shopify-Access-Token": self.token,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=1, max=30))
    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = self._client.post("/graphql.json", json={"query": query, "variables": variables or {}})
        resp.raise_for_status()
        body = resp.json()
        if "errors" in body:
            raise RuntimeError(f"shopify graphql errors: {body['errors']}")
        return body["data"]

    # ── One-shot queries ──────────────────────────────────────────

    def shop_info(self) -> dict[str, Any]:
        q = """
        query {
          shop {
            name
            myshopifyDomain
            primaryDomain { host url }
            email
            currencyCode
            ianaTimezone
            plan { displayName }
          }
        }
        """
        return self.graphql(q)["shop"]

    def test_connection(self) -> dict[str, Any]:
        info = self.shop_info()
        return {
            "shop": info["name"],
            "myshopify": info["myshopifyDomain"],
            "primary_domain": info["primaryDomain"]["host"],
            "currency": info["currencyCode"],
            "timezone": info["ianaTimezone"],
            "plan": info["plan"]["displayName"],
        }

    # ── Paginated iterators ───────────────────────────────────────

    def paginate_orders(self, updated_at_min: str | None = None, page_size: int = 50) -> Iterator[list[dict]]:
        query = """
        query Orders($after: String, $first: Int!, $query: String) {
          orders(first: $first, after: $after, query: $query, sortKey: UPDATED_AT) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id
              name
              createdAt
              updatedAt
              cancelledAt
              processedAt
              displayFinancialStatus
              displayFulfillmentStatus
              currencyCode
              tags
              note
              email
              phone
              currentTotalPriceSet { shopMoney { amount currencyCode } }
              totalPriceSet { shopMoney { amount currencyCode } }
              subtotalPriceSet { shopMoney { amount currencyCode } }
              totalTaxSet { shopMoney { amount currencyCode } }
              totalShippingPriceSet { shopMoney { amount currencyCode } }
              totalDiscountsSet { shopMoney { amount currencyCode } }
              customer { id email phone firstName lastName }
              shippingAddress { name address1 address2 city provinceCode zip countryCode phone }
              billingAddress  { name address1 address2 city provinceCode zip countryCode phone }
              lineItems(first: 50) {
                nodes {
                  id sku quantity title
                  variant { id sku price }
                  originalUnitPriceSet { shopMoney { amount currencyCode } }
                  discountedUnitPriceSet { shopMoney { amount currencyCode } }
                }
              }
              fulfillments(first: 20) {
                id
                status
                createdAt
                updatedAt
                deliveredAt
                estimatedDeliveryAt
                trackingInfo { company number url }
                fulfillmentLineItems(first: 50) {
                  nodes { id quantity lineItem { id sku } }
                }
              }
              transactions(first: 20) {
                id
                kind
                status
                gateway
                createdAt
                processedAt
                amountSet { shopMoney { amount currencyCode } }
                parentTransaction { id }
              }
              refunds(first: 20) {
                id
                createdAt
                note
                totalRefundedSet { shopMoney { amount currencyCode } }
                refundLineItems(first: 50) {
                  nodes { lineItem { id sku } quantity }
                }
                transactions(first: 10) {
                  nodes { id amountSet { shopMoney { amount currencyCode } } }
                }
              }
            }
          }
        }
        """
        cursor: str | None = None
        q = f"updated_at:>='{updated_at_min}'" if updated_at_min else None
        while True:
            data = self.graphql(query, {"after": cursor, "first": page_size, "query": q})
            yield data["orders"]["nodes"]
            if not data["orders"]["pageInfo"]["hasNextPage"]:
                return
            cursor = data["orders"]["pageInfo"]["endCursor"]

    def paginate_products(self, updated_at_min: str | None = None, page_size: int = 50) -> Iterator[list[dict]]:
        query = """
        query Products($after: String, $first: Int!, $query: String) {
          products(first: $first, after: $after, query: $query, sortKey: UPDATED_AT) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id title handle status createdAt updatedAt
              variants(first: 50) { nodes { id sku price inventoryQuantity } }
            }
          }
        }
        """
        cursor: str | None = None
        q = f"updated_at:>='{updated_at_min}'" if updated_at_min else None
        while True:
            data = self.graphql(query, {"after": cursor, "first": page_size, "query": q})
            yield data["products"]["nodes"]
            if not data["products"]["pageInfo"]["hasNextPage"]:
                return
            cursor = data["products"]["pageInfo"]["endCursor"]

    def paginate_customers(self, updated_at_min: str | None = None, page_size: int = 50) -> Iterator[list[dict]]:
        query = """
        query Customers($after: String, $first: Int!, $query: String) {
          customers(first: $first, after: $after, query: $query, sortKey: UPDATED_AT) {
            pageInfo { hasNextPage endCursor }
            nodes { id email phone firstName lastName createdAt updatedAt numberOfOrders }
          }
        }
        """
        cursor: str | None = None
        q = f"updated_at:>='{updated_at_min}'" if updated_at_min else None
        while True:
            data = self.graphql(query, {"after": cursor, "first": page_size, "query": q})
            yield data["customers"]["nodes"]
            if not data["customers"]["pageInfo"]["hasNextPage"]:
                return
            cursor = data["customers"]["pageInfo"]["endCursor"]

    def paginate_collections(self, updated_at_min: str | None = None, page_size: int = 50) -> Iterator[list[dict]]:
        query = """
        query Collections($after: String, $first: Int!, $query: String) {
          collections(first: $first, after: $after, query: $query, sortKey: UPDATED_AT) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id title handle updatedAt sortOrder productsCount { count }
              ruleSet { appliedDisjunctively rules { column condition relation } }
            }
          }
        }
        """
        cursor: str | None = None
        q = f"updated_at:>='{updated_at_min}'" if updated_at_min else None
        while True:
            data = self.graphql(query, {"after": cursor, "first": page_size, "query": q})
            yield data["collections"]["nodes"]
            if not data["collections"]["pageInfo"]["hasNextPage"]:
                return
            cursor = data["collections"]["pageInfo"]["endCursor"]

    def paginate_variants(self, updated_at_min: str | None = None, page_size: int = 50) -> Iterator[list[dict]]:
        # productVariants has no `query` filter and no sortKey — always full scan.
        # Fine for variants; they're small.
        query = """
        query Variants($after: String, $first: Int!) {
          productVariants(first: $first, after: $after) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id sku title price compareAtPrice createdAt updatedAt
              product { id title handle }
              inventoryItem {
                id sku tracked
                inventoryLevels(first: 20) {
                  nodes {
                    location { id name }
                    quantities(names: ["available","committed","on_hand"]) {
                      name quantity
                    }
                  }
                }
              }
            }
          }
        }
        """
        cursor: str | None = None
        while True:
            data = self.graphql(query, {"after": cursor, "first": page_size})
            yield data["productVariants"]["nodes"]
            if not data["productVariants"]["pageInfo"]["hasNextPage"]:
                return
            cursor = data["productVariants"]["pageInfo"]["endCursor"]

    def get_locations(self) -> list[dict]:
        """One-shot — locations list is always small."""
        query = """
        query {
          locations(first: 100) {
            nodes {
              id name isActive isPrimary shipsInventory fulfillsOnlineOrders
              address { address1 address2 city province country zip phone }
            }
          }
        }
        """
        return self.graphql(query)["locations"]["nodes"]

    def paginate_discounts(self, page_size: int = 50) -> Iterator[list[dict]]:
        query = """
        query Discounts($after: String, $first: Int!) {
          discountNodes(first: $first, after: $after) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id
              discount {
                __typename
                ... on DiscountCodeBasic {
                  title status startsAt endsAt summary
                  codes(first: 10) { nodes { code } }
                  usageLimit customerSelection { __typename }
                }
                ... on DiscountCodeBxgy     { title status startsAt endsAt summary }
                ... on DiscountCodeFreeShipping { title status startsAt endsAt summary }
                ... on DiscountAutomaticBasic  { title status startsAt endsAt summary }
                ... on DiscountAutomaticBxgy   { title status startsAt endsAt summary }
                ... on DiscountAutomaticFreeShipping { title status startsAt endsAt summary }
              }
            }
          }
        }
        """
        cursor: str | None = None
        while True:
            data = self.graphql(query, {"after": cursor, "first": page_size})
            yield data["discountNodes"]["nodes"]
            if not data["discountNodes"]["pageInfo"]["hasNextPage"]:
                return
            cursor = data["discountNodes"]["pageInfo"]["endCursor"]

    def paginate_abandoned_checkouts(self, created_at_min: str | None = None, page_size: int = 50) -> Iterator[list[dict]]:
        query = """
        query Abandoned($after: String, $first: Int!, $query: String) {
          abandonedCheckouts(first: $first, after: $after, query: $query) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id name createdAt updatedAt completedAt abandonedCheckoutUrl
              totalPriceSet { shopMoney { amount currencyCode } }
              subtotalPriceSet { shopMoney { amount currencyCode } }
              customer { id email phone firstName lastName }
              lineItems(first: 50) {
                nodes { title quantity originalTotalPriceSet { shopMoney { amount currencyCode } } variant { id sku } }
              }
            }
          }
        }
        """
        cursor: str | None = None
        q = f"created_at:>='{created_at_min}'" if created_at_min else None
        while True:
            data = self.graphql(query, {"after": cursor, "first": page_size, "query": q})
            yield data["abandonedCheckouts"]["nodes"]
            if not data["abandonedCheckouts"]["pageInfo"]["hasNextPage"]:
                return
            cursor = data["abandonedCheckouts"]["pageInfo"]["endCursor"]

    def paginate_draft_orders(self, updated_at_min: str | None = None, page_size: int = 50) -> Iterator[list[dict]]:
        query = """
        query Drafts($after: String, $first: Int!, $query: String) {
          draftOrders(first: $first, after: $after, query: $query, sortKey: UPDATED_AT) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id name status createdAt updatedAt completedAt
              totalPriceSet { shopMoney { amount currencyCode } }
              subtotalPriceSet { shopMoney { amount currencyCode } }
              customer { id email }
              lineItems(first: 50) {
                nodes { title quantity variant { id sku } }
              }
            }
          }
        }
        """
        cursor: str | None = None
        q = f"updated_at:>='{updated_at_min}'" if updated_at_min else None
        while True:
            data = self.graphql(query, {"after": cursor, "first": page_size, "query": q})
            yield data["draftOrders"]["nodes"]
            if not data["draftOrders"]["pageInfo"]["hasNextPage"]:
                return
            cursor = data["draftOrders"]["pageInfo"]["endCursor"]
