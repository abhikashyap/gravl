"""Ping Shopify with stored creds. Prints shop name/plan/currency or an error."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gravl.integrations.shopify.client import ShopifyClient  # noqa: E402


def main() -> None:
    with ShopifyClient() as c:
        info = c.shop_info()
    print("OK — connected to Shopify")
    print(f"  shop             : {info['name']}")
    print(f"  myshopifyDomain  : {info['myshopifyDomain']}")
    print(f"  primaryDomain    : {info['primaryDomain']['host']}")
    print(f"  email            : {info['email']}")
    print(f"  currency         : {info['currencyCode']}")
    print(f"  timezone         : {info['ianaTimezone']}")
    print(f"  plan             : {info['plan']['displayName']}")


if __name__ == "__main__":
    main()
