"""Prefect flow wrapping every Eshopbox pull. Deploy to the `gravl-home` work pool."""

from __future__ import annotations

from prefect import flow, task

from gravl.integrations.eshopbox import pull as eshopbox_pull


@task(name="gravl-eshopbox-orders")
def orders() -> int:
    return eshopbox_pull.pull_orders()


@task(name="gravl-eshopbox-shipments")
def shipments() -> int:
    return eshopbox_pull.pull_shipments()


@task(name="gravl-eshopbox-inventory")
def inventory() -> int:
    return eshopbox_pull.pull_inventory()


@flow(name="gravl-eshopbox-pull")
def eshopbox_flow() -> dict[str, int]:
    return {
        "orders": orders(),
        "shipments": shipments(),
        "inventory": inventory(),
    }


if __name__ == "__main__":
    print(eshopbox_flow())
