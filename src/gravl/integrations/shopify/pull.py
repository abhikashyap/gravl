"""Shopify incremental pulls — all streams → S3 (NDJSON, Hive-partitioned).

Postgres stores only `sync_windows` (per-stream pull log) + `job_tracker`
(observability). Raw data never lands in Postgres — it goes to
  s3://$S3_BRONZE_BUCKET/shopify/account=$S3_ACCOUNT/report=<stream>/date=YYYY-MM-DD/

Cursor model: next window_start = MAX(window_end) from sync_windows.
On failure: no row is written, so the next run replays the same window.
Manual reset: INSERT or DELETE rows in sync_windows directly.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from gravl.common.logging import get_logger
from gravl.common.s3 import upload_bronze
from gravl.db.sync_windows import last_window_end, record_window, reset_stream
from gravl.integrations.shopify.client import ShopifyClient
from gravl.orchestration.tracker import finish_job, start_job

log = get_logger("shopify.pull")

INTEGRATION = "shopify"


def _run_stream(
    stream: str,
    paginate_method: str,
    filter_kwarg: str = "updated_at_min",
    on_node=None,
    no_cursor: bool = False,
) -> int:
    """Paginate one stream, buffer records, upload to S3, log the window."""
    run_start = datetime.now(timezone.utc)
    window_start = None if no_cursor else last_window_end(INTEGRATION, stream)
    job_id = start_job(INTEGRATION, f"shopify_{stream}_pull",
                       window_start=window_start, window_end=run_start)
    records: list[dict] = []
    try:
        with ShopifyClient() as c:
            pager_fn = getattr(c, paginate_method)
            if no_cursor:
                pager = pager_fn()
            else:
                since = window_start.isoformat() if window_start else None
                pager = pager_fn(**{filter_kwarg: since})
            for page in pager:
                for node in page:
                    records.append(node)
                    if on_node:
                        on_node(node)
        s3_uri = upload_bronze(INTEGRATION, stream, records)
        record_window(
            INTEGRATION, stream,
            window_start=window_start, window_end=run_start,
            records=len(records), s3_uri=s3_uri,
        )
        finish_job(job_id, rows_landed=len(records), status="success")
        log.info("shopify_pull_done", stream=stream, records=len(records),
                 window_start=str(window_start), window_end=str(run_start), s3_uri=s3_uri)
        return len(records)
    except Exception as e:
        finish_job(job_id, rows_landed=len(records), status="failed", error=str(e))
        log.error("shopify_pull_failed", stream=stream, error=str(e))
        raise


# ── per-stream wrappers ───────────────────────────────────────────

def _collect_fulfillments(order_node: dict, buf: list[dict]) -> None:
    for f in order_node.get("fulfillments") or []:
        buf.append({**f, "orderId": order_node["id"]})


def pull_orders() -> int:
    """Orders stream — also splits fulfillments into their own S3 report."""
    fulfillments: list[dict] = []

    def _fan_out(order):
        _collect_fulfillments(order, fulfillments)

    rows = _run_stream("orders", "paginate_orders", on_node=_fan_out)
    if fulfillments:
        # Fulfillments piggyback on orders' window — one row, same bounds.
        run_end = datetime.now(timezone.utc)
        window_start = last_window_end(INTEGRATION, "fulfillments")
        s3_uri = upload_bronze(INTEGRATION, "fulfillments", fulfillments)
        record_window(
            INTEGRATION, "fulfillments",
            window_start=window_start, window_end=run_end,
            records=len(fulfillments), s3_uri=s3_uri,
        )
        log.info("shopify_fulfillments_uploaded", count=len(fulfillments), s3_uri=s3_uri)
    return rows


def pull_products() -> int:
    return _run_stream("products", "paginate_products")


def pull_customers() -> int:
    return _run_stream("customers", "paginate_customers")


def pull_collections() -> int:
    return _run_stream("collections", "paginate_collections")


def pull_variants() -> int:
    """Per-location inventory levels live inside each variant. No updated_at filter."""
    return _run_stream("variants", "paginate_variants", no_cursor=True)


def pull_discounts() -> int:
    return _run_stream("discounts", "paginate_discounts", no_cursor=True)


def pull_abandoned_checkouts() -> int:
    return _run_stream(
        "abandoned_checkouts",
        "paginate_abandoned_checkouts",
        filter_kwarg="created_at_min",
    )


def pull_draft_orders() -> int:
    return _run_stream("draft_orders", "paginate_draft_orders")


def pull_locations() -> int:
    """One-shot — small list, no cursor, no pagination."""
    run_start = datetime.now(timezone.utc)
    job_id = start_job(INTEGRATION, "shopify_locations_pull", window_end=run_start)
    locations: list[dict] = []
    try:
        with ShopifyClient() as c:
            locations = c.get_locations()
        s3_uri = upload_bronze(INTEGRATION, "locations", locations)
        record_window(
            INTEGRATION, "locations",
            window_start=None, window_end=run_start,
            records=len(locations), s3_uri=s3_uri,
        )
        finish_job(job_id, rows_landed=len(locations), status="success")
        log.info("shopify_pull_done", stream="locations", records=len(locations), s3_uri=s3_uri)
        return len(locations)
    except Exception as e:
        finish_job(job_id, rows_landed=len(locations), status="failed", error=str(e))
        log.error("shopify_pull_failed", stream="locations", error=str(e))
        raise


# ── aggregate ────────────────────────────────────────────────────

STREAMS: dict[str, callable] = {
    "orders": pull_orders,
    "products": pull_products,
    "customers": pull_customers,
    "collections": pull_collections,
    "variants": pull_variants,
    "locations": pull_locations,
    "discounts": pull_discounts,
    "abandoned_checkouts": pull_abandoned_checkouts,
    "draft_orders": pull_draft_orders,
}


def pull_all() -> dict[str, int]:
    results: dict[str, int] = {}
    for name, fn in STREAMS.items():
        try:
            results[name] = fn()
        except Exception as e:
            log.error("shopify_stream_failed", stream=name, error=str(e))
            results[name] = -1
    return results


def reset_all_windows() -> int:
    """Delete all sync_windows rows for shopify so every stream replays history."""
    deleted = reset_stream(INTEGRATION)
    log.info("shopify_windows_reset", rows_deleted=deleted)
    return deleted


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stream", choices=[*STREAMS.keys(), "all"], default="all")
    ap.add_argument("--full-refresh", action="store_true",
                    help="delete sync_windows rows first so every stream pulls full history")
    args = ap.parse_args()

    if args.full_refresh:
        reset_all_windows()

    if args.stream == "all":
        result = pull_all()
        log.info("shopify_pull_all_done", result=result)
    else:
        STREAMS[args.stream]()


if __name__ == "__main__":
    main()
