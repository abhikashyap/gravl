"""
S3 bronze-lake uploader — Hive-partitioned NDJSON dumps.

Layout:
    s3://$S3_BRONZE_BUCKET/<integration>/account=$S3_ACCOUNT/report=<stream>/date=YYYY-MM-DD/run-<uuid>.jsonl

Each line is one raw_json record (what we landed in Postgres bronze). NDJSON
plays well with Athena / Glue / DuckDB external tables.

Configured via .env:
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION
    S3_BRONZE_BUCKET (default: mkd-bronze-layer)
    S3_ACCOUNT (default: gravel)

If AWS_ACCESS_KEY_ID is empty the uploader becomes a no-op — Postgres still
lands normally, just nothing goes to S3. Lets us keep the module always-imported.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Iterable

from gravl.common.logging import get_logger

log = get_logger("s3")


def _enabled() -> bool:
    """Enabled if either explicit keys OR an AWS_PROFILE is set."""
    return bool(
        (os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"))
        or os.environ.get("AWS_PROFILE")
    )


def _client():
    """Build an S3 client. Prefers a named profile over inline keys when both exist."""
    import boto3

    region = os.environ.get("AWS_REGION", "ap-south-1")
    profile = os.environ.get("AWS_PROFILE")
    if profile:
        session = boto3.Session(profile_name=profile, region_name=region)
        return session.client("s3")
    return boto3.client(
        "s3",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=region,
    )


def _bucket() -> str:
    return os.environ.get("S3_BRONZE_BUCKET", "mkd-bronze-layer")


def _account() -> str:
    return os.environ.get("S3_ACCOUNT", "gravel")


def upload_bronze(
    integration: str,
    stream: str,
    records: Iterable[dict],
    *,
    date: str | None = None,
) -> str | None:
    """Dump `records` as NDJSON to the Hive-partitioned S3 key.

    Returns the full s3:// URI, or None if S3 is not configured / records empty.
    Failures are logged and swallowed — Postgres landing is authoritative.
    """
    records = list(records)
    if not records:
        return None
    if not _enabled():
        log.info("s3_skip", reason="AWS creds not set", stream=stream, records=len(records))
        return None

    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ts = int(datetime.now(timezone.utc).timestamp())
    filename = f"run-{uuid.uuid4().hex[:8]}-{ts}.jsonl"
    key = (
        f"{integration}/account={_account()}/report={stream}/date={date}/{filename}"
    )
    body = "\n".join(json.dumps(r, separators=(",", ":"), default=str) for r in records) + "\n"

    try:
        _client().put_object(
            Bucket=_bucket(),
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )
        uri = f"s3://{_bucket()}/{key}"
        log.info("s3_uploaded", stream=stream, uri=uri, records=len(records), bytes=len(body))
        return uri
    except Exception as e:
        log.error("s3_upload_failed", stream=stream, key=key, error=str(e))
        return None
