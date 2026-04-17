"""Prefect flow wrapping every Shopify pull. Deploy to the `gravl-home` work pool."""

from __future__ import annotations

from prefect import flow, task

from gravl.integrations.shopify import pull as shopify_pull


@task(name="gravl-shopify-orders")
def orders() -> int:
    return shopify_pull.pull_orders()


@task(name="gravl-shopify-products")
def products() -> int:
    return shopify_pull.pull_products()


@task(name="gravl-shopify-customers")
def customers() -> int:
    return shopify_pull.pull_customers()


@task(name="gravl-shopify-collections")
def collections() -> int:
    return shopify_pull.pull_collections()


@task(name="gravl-shopify-variants")
def variants() -> int:
    return shopify_pull.pull_variants()


@task(name="gravl-shopify-locations")
def locations() -> int:
    return shopify_pull.pull_locations()


@task(name="gravl-shopify-discounts")
def discounts() -> int:
    return shopify_pull.pull_discounts()


@task(name="gravl-shopify-abandoned")
def abandoned_checkouts() -> int:
    return shopify_pull.pull_abandoned_checkouts()


@task(name="gravl-shopify-drafts")
def draft_orders() -> int:
    return shopify_pull.pull_draft_orders()


@flow(name="gravl-shopify-pull")
def shopify_flow() -> dict[str, int]:
    return {
        "orders": orders(),
        "products": products(),
        "customers": customers(),
        "collections": collections(),
        "variants": variants(),
        "locations": locations(),
        "discounts": discounts(),
        "abandoned_checkouts": abandoned_checkouts(),
        "draft_orders": draft_orders(),
    }


if __name__ == "__main__":
    print(shopify_flow())
