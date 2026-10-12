-- Traceview schema: consumption-based GTM data model, dimension/fact split.
--
-- Run automatically by docker-compose on first `db` container creation
-- (mounted into /docker-entrypoint-initdb.d) - the fast path for a brand
-- new database. Re-running against an existing database is safe - every
-- statement is idempotent - but this file is no longer the ongoing
-- source of truth for the schema now that migrations/ exists: it's also
-- executed as-is by the initial Alembic migration
-- (migrations/versions/..._initial_schema_baseline.py), and any schema
-- change from here forward belongs in a new migration
-- (`alembic revision -m "..."`), not a hand-edit here.
--
-- Two roles exist on purpose:
--   traceview            - owns the schema, used for migrations and seeding.
--   traceview_readonly   - SELECT-only, granted nothing else. This is the
--                          actual safety boundary for AI- or user-generated
--                          SQL (see backend/query_engine.py), enforced by
--                          Postgres itself rather than application code -
--                          the direct equivalent of SQLite's set_authorizer
--                          hook, but as a real multi-user privilege instead
--                          of a compile-time callback.

CREATE EXTENSION IF NOT EXISTS vector;

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'traceview_readonly') THEN
        CREATE ROLE traceview_readonly WITH LOGIN PASSWORD 'traceview_readonly_dev_only';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE traceview TO traceview_readonly;
GRANT USAGE ON SCHEMA public TO traceview_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO traceview_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO traceview_readonly;

-- ---------------------------------------------------------------------
-- Dimensions
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS accounts (
    account_id  INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        TEXT NOT NULL,
    segment     TEXT NOT NULL CHECK (segment IN ('enterprise', 'strategic', 'commercial')),
    industry    TEXT NOT NULL CHECK (industry IN
                    ('financial_services', 'retail', 'healthcare', 'media', 'public_sector', 'technology', 'other')),
    region      TEXT NOT NULL,
    created_at  DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS workloads (
    workload_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS account_team (
    team_member_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id      INTEGER NOT NULL REFERENCES accounts(account_id),
    name            TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('AE', 'SE', 'CSM'))
);

CREATE TABLE IF NOT EXISTS partners (
    partner_id  INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        TEXT NOT NULL,
    partner_type TEXT NOT NULL CHECK (partner_type IN ('SI', 'cloud_marketplace', 'ISV'))
);

-- ---------------------------------------------------------------------
-- Facts
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS deals (
    deal_id             INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id          INTEGER NOT NULL REFERENCES accounts(account_id),
    workload_id         INTEGER NOT NULL REFERENCES workloads(workload_id),
    owner_team_member_id INTEGER REFERENCES account_team(team_member_id),
    partner_id          INTEGER REFERENCES partners(partner_id),
    stage               TEXT NOT NULL CHECK (stage IN
                            ('discovery', 'technical_validation', 'business_case', 'negotiation', 'closed_won', 'closed_lost')),
    poc_status          TEXT NOT NULL CHECK (poc_status IN ('not_started', 'in_progress', 'passed', 'failed')),
    deal_value          NUMERIC(12, 2) NOT NULL,
    created_date        DATE NOT NULL,
    close_date          DATE
);

CREATE TABLE IF NOT EXISTS capacity_contracts (
    contract_id       INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id        INTEGER NOT NULL REFERENCES accounts(account_id),
    workload_id       INTEGER NOT NULL REFERENCES workloads(workload_id),
    deal_id           INTEGER REFERENCES deals(deal_id),
    committed_amount  NUMERIC(12, 2) NOT NULL,
    contract_type     TEXT NOT NULL CHECK (contract_type IN ('new', 'expansion', 'renewal', 'true_up')),
    status            TEXT NOT NULL CHECK (status IN ('active', 'at_risk', 'churned')),
    term_start        DATE NOT NULL,
    term_end          DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS consumption_usage (
    usage_id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id        INTEGER NOT NULL REFERENCES accounts(account_id),
    workload_id       INTEGER NOT NULL REFERENCES workloads(workload_id),
    usage_month       DATE NOT NULL,
    credits_consumed  NUMERIC(12, 2) NOT NULL,
    active_warehouses INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS activities (
    activity_id     INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deal_id         INTEGER NOT NULL REFERENCES deals(deal_id),
    team_member_id  INTEGER NOT NULL REFERENCES account_team(team_member_id),
    activity_type   TEXT NOT NULL CHECK (activity_type IN
                        ('discovery_call', 'poc_kickoff', 'poc_technical_review', 'exec_review', 'qbr', 'renewal_call')),
    activity_date   DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS marketing_touches (
    touch_id        INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id      INTEGER NOT NULL REFERENCES accounts(account_id),
    campaign_name   TEXT NOT NULL,
    channel         TEXT NOT NULL CHECK (channel IN ('webinar', 'paid_ad', 'summit', 'field_event', 'partner_cosell', 'email')),
    touch_date      DATE NOT NULL,
    engagement_type TEXT NOT NULL CHECK (engagement_type IN ('sent', 'opened', 'clicked', 'attended'))
);

CREATE INDEX IF NOT EXISTS idx_deals_account ON deals(account_id);
CREATE INDEX IF NOT EXISTS idx_contracts_account ON capacity_contracts(account_id);
CREATE INDEX IF NOT EXISTS idx_usage_account_workload_month ON consumption_usage(account_id, workload_id, usage_month);
CREATE INDEX IF NOT EXISTS idx_activities_deal ON activities(deal_id);
CREATE INDEX IF NOT EXISTS idx_touches_account ON marketing_touches(account_id);

-- ---------------------------------------------------------------------
-- Unstructured content - the two source types feeding document_chunks.
-- account_notes are account-scoped (CS/sales free text); enablement_content
-- is global (battlecards, sales plays) and carries no account_id at all,
-- which is what makes it structurally impossible to leak into another
-- account's context - there's no account to leak from in the first place.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS account_notes (
    note_id     INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES accounts(account_id),
    author_role TEXT NOT NULL CHECK (author_role IN ('AE', 'SE', 'CSM')),
    note_date   DATE NOT NULL,
    content     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS enablement_content (
    content_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title      TEXT NOT NULL,
    category   TEXT NOT NULL CHECK (category IN ('battlecard', 'sales_play', 'objection_handling', 'faq')),
    content    TEXT NOT NULL
);

-- Both source types get chunked into this one table, which is what
-- Phase 08's retrieval actually searches over. account_id is NULL for
-- enablement_content chunks and NOT NULL for account_notes chunks - that
-- column is the isolation boundary Phase 11's upload feature depends on.
CREATE TABLE IF NOT EXISTS document_chunks (
    chunk_id    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_type TEXT NOT NULL CHECK (source_type IN ('account_note', 'enablement_content')),
    source_id   INTEGER NOT NULL,
    account_id  INTEGER REFERENCES accounts(account_id),
    chunk_index INTEGER NOT NULL,
    chunk_text  TEXT NOT NULL,
    embedding   vector(384)
);

CREATE INDEX IF NOT EXISTS idx_notes_account ON account_notes(account_id);
CREATE INDEX IF NOT EXISTS idx_chunks_account ON document_chunks(account_id);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON document_chunks(source_type, source_id);

-- Verified-query repository, ported off in-memory FAISS onto pgvector -
-- the embedding lives here precomputed, not rebuilt from a JSON file on
-- every process start. verified_queries.json stays the human-edited
-- source (each entry's provenance recorded in verified_via); this table
-- is what find_verified_match() actually queries.
CREATE TABLE IF NOT EXISTS verified_queries (
    verified_id  INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    question     TEXT NOT NULL,
    sql          TEXT NOT NULL,
    verified_via TEXT NOT NULL,
    embedding    vector(384)
);

-- Business-glossary retrieval, same pgvector pattern - this is what grounds
-- SQL generation in the project's own precise definitions ("active deal",
-- "win rate") instead of the model guessing a generic one. Deliberately a
-- separate table from document_chunks: short term/definition pairs used to
-- ground SQL generation are a different retrieval target than chunked
-- unstructured narrative content used for hybrid answers.
CREATE TABLE IF NOT EXISTS glossary_terms (
    term_id    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    term       TEXT NOT NULL,
    definition TEXT NOT NULL,
    embedding  vector(384)
);

-- Conversation history, keyed by the client-generated conversation_id -
-- what makes /ask support follow-ups ("now break that down by industry")
-- instead of every question starting from zero. Previously an in-memory
-- Python dict (conversations.py), which meant a server restart silently
-- forgot every conversation in progress; storing it here means it
-- survives a restart and would be shared correctly across multiple
-- backend instances too, since every instance reads the same table
-- instead of its own private memory. `turn` is the same small JSON shape
-- conversations.py has always built (type/question/sql/message/etc,
-- varying per response type) - stored as JSONB rather than split into
-- rigid columns, since its shape genuinely varies by response type and
-- there's nothing here that needs to be queried by its internal fields.
CREATE TABLE IF NOT EXISTS conversation_turns (
    turn_id         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    turn            JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_conversation_turns_conv ON conversation_turns(conversation_id, turn_id);
