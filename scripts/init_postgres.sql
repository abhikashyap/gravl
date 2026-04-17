-- gravl Postgres schema — single-brand.
-- One gravl DB = one client. Multiple integrations (Shopify, Cashfree, Eshopbox,
-- Freshdesk, Meta WhatsApp) live side-by-side. Credentials are flat per
-- (integration, key, env). Bronze tables land raw JSON per integration.
--
-- Apply: psql "$DATABASE_URL" -f scripts/init_postgres.sql
-- For a clean rebuild during early development:
--   DROP SCHEMA public CASCADE; CREATE SCHEMA public;
-- then apply.

-- ── CONTROL ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS integrations (
    id            SERIAL PRIMARY KEY,
    slug          TEXT UNIQUE NOT NULL,
    kind          TEXT NOT NULL,       -- storefront | payment | support | wms | notify | config
    display_name  TEXT,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS credentials (
    id              SERIAL PRIMARY KEY,
    integration_id  INT NOT NULL REFERENCES integrations(id) ON DELETE CASCADE,
    key             TEXT NOT NULL,
    value           TEXT NOT NULL,
    env             TEXT NOT NULL DEFAULT 'prod',     -- prod | staging | dev
    rotated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (integration_id, key, env)
);

CREATE TABLE IF NOT EXISTS endpoints (
    id              SERIAL PRIMARY KEY,
    integration_id  INT NOT NULL REFERENCES integrations(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    url             TEXT NOT NULL,
    method          TEXT NOT NULL DEFAULT 'GET',
    version         TEXT,
    UNIQUE (integration_id, name)
);

CREATE TABLE IF NOT EXISTS templates (
    id                SERIAL PRIMARY KEY,
    channel           TEXT NOT NULL,                  -- whatsapp, email, sms
    name              TEXT NOT NULL,
    category          TEXT NOT NULL,                  -- utility | marketing | authentication | service
    locale            TEXT NOT NULL DEFAULT 'en',
    body_json         JSONB NOT NULL,
    meta_template_id  TEXT,
    approved          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (channel, name, locale)
);

CREATE TABLE IF NOT EXISTS webhooks_registry (
    id              SERIAL PRIMARY KEY,
    integration_id  INT NOT NULL REFERENCES integrations(id) ON DELETE CASCADE,
    event           TEXT NOT NULL,
    path            TEXT NOT NULL,
    secret_key      TEXT,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (integration_id, event)
);

-- ── ORCHESTRATION ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS job_tracker (
    id              BIGSERIAL PRIMARY KEY,
    integration_id  INT NOT NULL REFERENCES integrations(id),
    flow            TEXT NOT NULL,
    window_start    TIMESTAMPTZ,
    window_end      TIMESTAMPTZ,
    rows_landed     INT NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'running',
    error           TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_job_tracker_integration ON job_tracker (integration_id, started_at DESC);

CREATE TABLE IF NOT EXISTS run_state (
    integration_id  INT NOT NULL REFERENCES integrations(id) ON DELETE CASCADE,
    cursor_key      TEXT NOT NULL,
    cursor_value    TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (integration_id, cursor_key)
);

-- sync_windows: human-readable per-stream pull log. Cursor = MAX(window_end).
-- On success: INSERT one row. On failure: no row (next run retries same start).
-- Manual reset: INSERT a row with status='manual_reset' to force a re-pull range.
CREATE TABLE IF NOT EXISTS sync_windows (
    id              BIGSERIAL PRIMARY KEY,
    integration_id  INT NOT NULL REFERENCES integrations(id) ON DELETE CASCADE,
    stream          TEXT NOT NULL,
    window_start    TIMESTAMPTZ,
    window_end      TIMESTAMPTZ NOT NULL,
    records         INT NOT NULL DEFAULT 0,
    s3_uri          TEXT,
    status          TEXT NOT NULL DEFAULT 'success',
    ran_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_sync_windows_cursor
    ON sync_windows (integration_id, stream, window_end DESC);

CREATE TABLE IF NOT EXISTS webhook_events_audit (
    id              BIGSERIAL PRIMARY KEY,
    integration_id  INT REFERENCES integrations(id),
    received_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    path            TEXT NOT NULL,
    signature_ok    BOOLEAN NOT NULL,
    dedup_hit       BOOLEAN NOT NULL DEFAULT FALSE,
    bronze_table    TEXT,
    bronze_id       BIGINT
);
CREATE INDEX IF NOT EXISTS idx_webhook_audit_received ON webhook_events_audit (received_at DESC);

-- ── BRONZE ───────────────────────────────────────────────────────
-- DEPRECATED: raw JSON bronze now lives exclusively in S3
--   s3://$S3_BRONZE_BUCKET/<integration>/account=$S3_ACCOUNT/report=<stream>/date=YYYY-MM-DD/
-- Postgres keeps only control/orchestration metadata (credentials, run_state,
-- job_tracker). If you have an old DB with these tables, drop them:
--   DROP TABLE IF EXISTS bronze_shopify_orders, bronze_shopify_products,
--     bronze_shopify_customers, bronze_shopify_fulfillments, bronze_shopify_variants,
--     bronze_shopify_collections, bronze_shopify_locations, bronze_shopify_discounts,
--     bronze_shopify_abandoned_checkouts, bronze_shopify_draft_orders,
--     bronze_cashfree_orders, bronze_cashfree_settlements,
--     bronze_freshdesk_tickets, bronze_eshopbox_events CASCADE;
-- The block below is retained (commented) for historical reference only.

/*
CREATE TABLE IF NOT EXISTS bronze_shopify_orders (
    id                 BIGSERIAL PRIMARY KEY,
    source_event_id    TEXT UNIQUE NOT NULL,
    raw_json           JSONB NOT NULL,
    received_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at       TIMESTAMPTZ,
    source_updated_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_bsh_orders_raw ON bronze_shopify_orders USING GIN (raw_json);
CREATE INDEX IF NOT EXISTS idx_bsh_orders_unprocessed ON bronze_shopify_orders (received_at) WHERE processed_at IS NULL;

CREATE TABLE IF NOT EXISTS bronze_shopify_products (
    id                 BIGSERIAL PRIMARY KEY,
    source_event_id    TEXT UNIQUE NOT NULL,
    raw_json           JSONB NOT NULL,
    received_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at       TIMESTAMPTZ,
    source_updated_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_bsh_products_raw ON bronze_shopify_products USING GIN (raw_json);

CREATE TABLE IF NOT EXISTS bronze_shopify_customers (
    id                 BIGSERIAL PRIMARY KEY,
    source_event_id    TEXT UNIQUE NOT NULL,
    raw_json           JSONB NOT NULL,
    received_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at       TIMESTAMPTZ,
    source_updated_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_bsh_customers_raw ON bronze_shopify_customers USING GIN (raw_json);

CREATE TABLE IF NOT EXISTS bronze_shopify_fulfillments (
    id                 BIGSERIAL PRIMARY KEY,
    source_event_id    TEXT UNIQUE NOT NULL,
    raw_json           JSONB NOT NULL,
    received_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at       TIMESTAMPTZ,
    source_updated_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_bsh_fulfillments_raw ON bronze_shopify_fulfillments USING GIN (raw_json);

CREATE TABLE IF NOT EXISTS bronze_shopify_collections (
    id                 BIGSERIAL PRIMARY KEY,
    source_event_id    TEXT UNIQUE NOT NULL,
    raw_json           JSONB NOT NULL,
    received_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at       TIMESTAMPTZ,
    source_updated_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_bsh_collections_raw ON bronze_shopify_collections USING GIN (raw_json);

CREATE TABLE IF NOT EXISTS bronze_shopify_variants (
    id                 BIGSERIAL PRIMARY KEY,
    source_event_id    TEXT UNIQUE NOT NULL,   -- variant id; includes per-location inventory
    raw_json           JSONB NOT NULL,
    received_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at       TIMESTAMPTZ,
    source_updated_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_bsh_variants_raw ON bronze_shopify_variants USING GIN (raw_json);

CREATE TABLE IF NOT EXISTS bronze_shopify_locations (
    id                 BIGSERIAL PRIMARY KEY,
    source_event_id    TEXT UNIQUE NOT NULL,
    raw_json           JSONB NOT NULL,
    received_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at       TIMESTAMPTZ,
    source_updated_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_bsh_locations_raw ON bronze_shopify_locations USING GIN (raw_json);

CREATE TABLE IF NOT EXISTS bronze_shopify_discounts (
    id                 BIGSERIAL PRIMARY KEY,
    source_event_id    TEXT UNIQUE NOT NULL,
    raw_json           JSONB NOT NULL,
    received_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at       TIMESTAMPTZ,
    source_updated_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_bsh_discounts_raw ON bronze_shopify_discounts USING GIN (raw_json);

CREATE TABLE IF NOT EXISTS bronze_shopify_abandoned_checkouts (
    id                 BIGSERIAL PRIMARY KEY,
    source_event_id    TEXT UNIQUE NOT NULL,
    raw_json           JSONB NOT NULL,
    received_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at       TIMESTAMPTZ,
    source_updated_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_bsh_abandoned_raw ON bronze_shopify_abandoned_checkouts USING GIN (raw_json);

CREATE TABLE IF NOT EXISTS bronze_shopify_draft_orders (
    id                 BIGSERIAL PRIMARY KEY,
    source_event_id    TEXT UNIQUE NOT NULL,
    raw_json           JSONB NOT NULL,
    received_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at       TIMESTAMPTZ,
    source_updated_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_bsh_drafts_raw ON bronze_shopify_draft_orders USING GIN (raw_json);

CREATE TABLE IF NOT EXISTS bronze_cashfree_orders (
    id                 BIGSERIAL PRIMARY KEY,
    source_event_id    TEXT UNIQUE NOT NULL,
    raw_json           JSONB NOT NULL,
    received_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at       TIMESTAMPTZ,
    source_updated_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_bcf_orders_raw ON bronze_cashfree_orders USING GIN (raw_json);

CREATE TABLE IF NOT EXISTS bronze_cashfree_settlements (
    id                 BIGSERIAL PRIMARY KEY,
    source_event_id    TEXT UNIQUE NOT NULL,
    raw_json           JSONB NOT NULL,
    received_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at       TIMESTAMPTZ,
    source_updated_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_bcf_settlements_raw ON bronze_cashfree_settlements USING GIN (raw_json);

CREATE TABLE IF NOT EXISTS bronze_freshdesk_tickets (
    id                 BIGSERIAL PRIMARY KEY,
    source_event_id    TEXT UNIQUE NOT NULL,
    raw_json           JSONB NOT NULL,
    received_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at       TIMESTAMPTZ,
    source_updated_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_bfd_tickets_raw ON bronze_freshdesk_tickets USING GIN (raw_json);

CREATE TABLE IF NOT EXISTS bronze_eshopbox_events (
    id                 BIGSERIAL PRIMARY KEY,
    source_event_id    TEXT UNIQUE NOT NULL,
    event_type         TEXT,
    raw_json           JSONB NOT NULL,
    received_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at       TIMESTAMPTZ,
    source_updated_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_beb_events_raw ON bronze_eshopbox_events USING GIN (raw_json);
CREATE INDEX IF NOT EXISTS idx_beb_events_type ON bronze_eshopbox_events (event_type, received_at DESC);
*/

-- ── OUTBOUND AUDIT ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS whatsapp_sends (
    id                BIGSERIAL PRIMARY KEY,
    template_id       INT REFERENCES templates(id),
    to_e164           TEXT NOT NULL,
    variables_json    JSONB,
    meta_message_id   TEXT,
    status            TEXT NOT NULL DEFAULT 'queued',
    error             TEXT,
    sent_at           TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wa_sends_status ON whatsapp_sends (status, created_at DESC);

-- ── INBOUND: WhatsApp webhook events ─────────────────────────────
-- Raw payload from Meta Cloud API webhooks. One row per Meta `entry.changes` item.
-- event_type: 'status' (delivery receipt) | 'message' (inbound) | 'other'
-- external_id: meta_message_id (for status) or message id (for inbound) — used for dedup
CREATE TABLE IF NOT EXISTS bronze_whatsapp_events (
    id              BIGSERIAL PRIMARY KEY,
    event_type      TEXT NOT NULL,
    external_id     TEXT,
    from_e164       TEXT,
    raw_json        JSONB NOT NULL,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at    TIMESTAMPTZ,
    UNIQUE (event_type, external_id)
);
CREATE INDEX IF NOT EXISTS idx_bwa_events_type ON bronze_whatsapp_events (event_type, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_bwa_events_unprocessed ON bronze_whatsapp_events (received_at) WHERE processed_at IS NULL;

-- ── SEED: integrations registry ──────────────────────────────────

INSERT INTO integrations (slug, kind, display_name) VALUES
    ('shopify',       'storefront', 'Shopify'),
    ('cashfree',      'payment',    'Cashfree Payments'),
    ('freshdesk',     'support',    'Freshdesk'),
    ('eshopbox',      'wms',        'Eshopbox'),
    ('meta_whatsapp', 'notify',     'Meta WhatsApp Cloud API'),
    ('google_sheets', 'config',     'Google Sheets')
ON CONFLICT (slug) DO UPDATE
   SET kind = EXCLUDED.kind, display_name = EXCLUDED.display_name;
