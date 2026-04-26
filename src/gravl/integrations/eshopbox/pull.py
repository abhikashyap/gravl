"""Eshopbox incremental pulls — lands NDJSON into S3 bronze.

Streams:
  - orders    → workspace /api/v1/orders/erp, filter updatedAt >= last_window (ms)
  - shipments → wms.eshopbox.com /api/order/shipment (full scan; API has no updated filter)
  - inventory → workspace /api/v2/inventoryListing (snapshot)

Returns are event-driven only — use the webhook for return lifecycle.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from gravl.common.logging import get_logger
from gravl.common.s3 import upload_bronze
from gravl.db.sync_windows import last_window_end, record_window, reset_stream
from gravl.integrations.eshopbox.client import EshopboxClient
from gravl.orchestration.tracker import finish_job, start_job

log = get_logger("eshopbox.pull")

INTEGRATION = "eshopbox"


def _run(stream: str, collect_fn) -> int:
    run_start = datetime.now(timezone.utc)
    window_start = last_window_end(INTEGRATION, stream)
    job_id = start_job(INTEGRATION, f"eshopbox_{stream}_pull",
                       window_start=window_start, window_end=run_start)
    records: list[dict] = []
    try:
        with EshopboxClient() as c:
            collect_fn(c, window_start, records)
        s3_uri = upload_bronze(INTEGRATION, stream, records)
        record_window(INTEGRATION, stream,
                      window_start=window_start, window_end=run_start,
                      records=len(records), s3_uri=s3_uri)
        finish_job(job_id, rows_landed=len(records), status="success")
        log.info("eshopbox_pull_done", stream=stream, records=len(records),
                 window_start=str(window_start), window_end=str(run_start), s3_uri=s3_uri)
        return len(records)
    except Exception as e:
        finish_job(job_id, rows_landed=len(records), status="failed", error=str(e))
        log.error("eshopbox_pull_failed", stream=stream, error=str(e))
        raise


def pull_orders() -> int:
    def _collect(c: EshopboxClient, window_start, records):
        since_ms = int(window_start.timestamp() * 1000) if window_start else None
        for page in c.paginate_orders(updated_after_ms=since_ms):
            records.extend(page)
    return _run("orders", _collect)


def pull_shipments() -> int:
    def _collect(c: EshopboxClient, _ws, records):
        for page in c.paginate_shipments():
            records.extend(page)
    return _run("shipments", _collect)


def pull_inventory() -> int:
    def _collect(c: EshopboxClient, _ws, records):
        for page in c.paginate_inventory():
            records.extend(page)
    return _run("inventory", _collect)


STREAMS = {
    "orders": pull_orders,
    "shipments": pull_shipments,
    "inventory": pull_inventory,
}


def pull_all() -> dict[str, int]:
    out: dict[str, int] = {}
    for name, fn in STREAMS.items():
        try:
            out[name] = fn()
        except Exception as e:
            log.error("eshopbox_stream_failed", stream=name, error=str(e))
            out[name] = -1
    return out


def reset_all_windows() -> int:
    deleted = reset_stream(INTEGRATION)
    log.info("eshopbox_windows_reset", rows_deleted=deleted)
    return deleted


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stream", choices=[*STREAMS.keys(), "all"], default="all")
    ap.add_argument("--full-refresh", action="store_true")
    args = ap.parse_args()
    if args.full_refresh:
        reset_all_windows()
    if args.stream == "all":
        log.info("eshopbox_pull_all_done", result=pull_all())
    else:
        STREAMS[args.stream]()


if __name__ == "__main__":
    main()
