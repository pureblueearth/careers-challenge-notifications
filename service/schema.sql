-- ============================================================================
-- Schema for the notification service.
-- ============================================================================

-- devices: one row per push token. A 410 deactivates the token (soft-delete) so
-- fan-out skips it while the row stays for history.
CREATE TABLE IF NOT EXISTS devices (
    token       TEXT        PRIMARY KEY,
    user_id     TEXT        NOT NULL,
    platform    TEXT        NOT NULL CHECK (platform IN ('ios', 'android', 'web')),
    active      BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Fan-out reads active devices per user.
CREATE INDEX IF NOT EXISTS idx_devices_active_user ON devices (user_id) WHERE active;

-- events: one row per accepted notification. event_id PRIMARY KEY makes intake
-- idempotent (a re-submitted notify is a no-op via ON CONFLICT DO NOTHING). seq
-- stamps global arrival order; per-user delivery follows seq.
CREATE TABLE IF NOT EXISTS events (
    event_id     TEXT        PRIMARY KEY,
    user_id      TEXT        NOT NULL,
    title        TEXT        NOT NULL,
    body         TEXT        NOT NULL,
    priority     TEXT        NOT NULL CHECK (priority IN ('high', 'normal')),
    occurred_at  TIMESTAMPTZ,
    seq          BIGSERIAL   NOT NULL,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- deliveries: the unit of work, one row per (event, device). PK (event_id, token)
-- enforces per-pair dedup. user_id/seq/priority are denormalized from events so the
-- claim needs no join. status: pending -> delivered | expired | failed (last three
-- terminal). next_attempt_at = backoff; leased_until = crash-recovery lease.
CREATE TABLE IF NOT EXISTS deliveries (
    event_id        TEXT        NOT NULL REFERENCES events(event_id),
    token           TEXT        NOT NULL,
    user_id         TEXT        NOT NULL,
    seq             BIGINT      NOT NULL,
    priority        TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'delivered', 'expired', 'failed')),
    attempts        INT         NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    leased_until    TIMESTAMPTZ,
    last_error      TEXT,
    delivered_at    TIMESTAMPTZ,
    PRIMARY KEY (event_id, token)
);

-- Due pending work. Partial index stays tiny (pending only).
CREATE INDEX IF NOT EXISTS idx_deliveries_due
    ON deliveries (next_attempt_at) WHERE status = 'pending';

-- Serves the ordering gate (any lower-seq pending row for a user). pending is the
-- only non-terminal status; a claimed row stays pending + leased.
CREATE INDEX IF NOT EXISTS idx_deliveries_user_seq
    ON deliveries (user_id, seq) WHERE status = 'pending';

-- Claimable-set lookup ordered by priority then seq.
CREATE INDEX IF NOT EXISTS deliveries_claimable_idx
    ON deliveries (
        priority,
        seq,
        user_id,
        next_attempt_at
    )
    WHERE status = 'pending';
