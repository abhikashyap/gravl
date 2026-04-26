"""Prefect flow for daily Cashfree pulls. Deploy to gravl-home work pool."""

from __future__ import annotations

from prefect import flow, task

from gravl.integrations.cashfree import pull as cashfree_pull


@task(name="gravl-cashfree-recon")
def recon() -> int:
    return cashfree_pull.pull_recon()


@flow(name="gravl-cashfree-pull")
def cashfree_flow() -> dict[str, int]:
    return {"recon": recon()}


if __name__ == "__main__":
    print(cashfree_flow())
