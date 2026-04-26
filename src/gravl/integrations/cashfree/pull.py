"""Cashfree incremental pulls — NDJSON → S3 bronze.

Streams:
  - recon       → /recon POST, chunked 29-day windows, all event types
                  (PAYMENT, REFUND, SETTLEMENT, ADJUSTMENT)
  - settlements → alias for recon, kept for backward compat

Cursor model: next window_start = MAX(window_end) from sync_windows.
On failure: no window row written → next run replays same window.
Manual reset: python -m gravl.integrations.cashfree.pull --full-refresh
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from gravl.common.logging import get_logger
from gravl.common.s3 import upload_bronze
from gravl.db.sync_windows import last_window_end, record_window, reset_stream
from gravl.integrations.cashfree.client import CashfreeClient
from gravl.orchestration.tracker import finish_job, start_job

log = get_logger("cashfree.pull")

INTEGRATION = "cashfree"


def _run(stream: str, collect_fn) -> int:
    run_start = datetime.now(timezone.utc)
    window_start = last_window_end(INTEGRATION, stream)
    job_id = start_job(INTEGRATION, f"cashfree_{stream}_pull",
                       window_start=window_start, window_end=run_start)
    records: list[dict] = []
    try:
        with CashfreeClient() as c:
            collect_fn(c, window_start, run_start, records)
        s3_uri = upload_bronze(INTEGRATION, stream, records)
        record_window(INTEGRATION, stream,
                      window_start=window_start, window_end=run_start,
                      records=len(records), s3_uri=s3_uri)
        finish_job(job_id, rows_landed=len(records), status="success")
        log.info("cashfree_pull_done", stream=stream, records=len(records),
                 window_start=str(window_start), window_end=str(run_start), s3_uri=s3_uri)
        return len(records)
    except Exception as e:
        finish_job(job_id, rows_landed=len(records), status="failed", error=str(e))
        log.error("cashfree_pull_failed", stream=stream, error=str(e))
        raise


def pull_recon() -> int:
    """All payment events (PAYMENT/REFUND/SETTLEMENT/ADJUSTMENT) via /recon."""
    def _collect(c: CashfreeClient, window_start, run_start, records):
        for page in c.paginate_recon(from_dt=window_start, to_dt=run_start):
            records.extend(page)
    return _run("recon", _collect)


def pull_settlements() -> int:
    """Alias — delegates to pull_recon for backward compat."""
    return pull_recon()


STREAMS: dict[str, callable] = {
    "recon": pull_recon,
    "settlements": pull_settlements,
}


def pull_all() -> dict[str, int]:
    # recon covers everything; run only once
    return {"recon": pull_recon()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stream", choices=[*STREAMS.keys(), "all"], default="all")
    ap.add_argument("--full-refresh", action="store_true",
                    help="delete sync_windows rows so pull replays full history")
    args = ap.parse_args()

    if args.full_refresh:
        reset_stream(INTEGRATION)

    if args.stream == "all":
        print(pull_all())
    else:
        STREAMS[args.stream]()


if __name__ == "__main__":
    main()
