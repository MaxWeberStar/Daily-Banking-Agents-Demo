-- Apply with an admin connection; set demo_runtime's password outside this file.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'demo_runtime') THEN
        CREATE ROLE demo_runtime LOGIN NOBYPASSRLS;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS sessions (
    id uuid PRIMARY KEY,
    cookie_hash text UNIQUE NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    id text NOT NULL,
    session_id uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    name text NOT NULL,
    account_type text NOT NULL,
    balance_cents bigint NOT NULL,
    PRIMARY KEY (session_id, id)
);

CREATE TABLE IF NOT EXISTS transactions (
    id text NOT NULL,
    source_entry_id text NOT NULL,
    session_id uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    ledger_order integer NOT NULL,
    posting_date text NOT NULL,
    posting_time text NOT NULL,
    account text NOT NULL,
    account_type text NOT NULL,
    direction text NOT NULL,
    amount_cents bigint NOT NULL,
    description text NOT NULL,
    prd_category text NOT NULL,
    initial_category text NOT NULL,
    allocation text NOT NULL,
    payment_channel text NOT NULL,
    bank_balance_after_cents bigint,
    notes text NOT NULL,
    entry_type text NOT NULL,
    gross_amount_cents bigint NOT NULL,
    include_in_monthly_spend boolean NOT NULL,
    card_balance_after_cents bigint,
    PRIMARY KEY (session_id, id)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id text NOT NULL,
    source_id text NOT NULL,
    session_id uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    merchant text NOT NULL,
    amount_cents bigint NOT NULL,
    period text NOT NULL,
    consecutive_charges integer NOT NULL,
    last_used_date date NOT NULL,
    status text NOT NULL,
    PRIMARY KEY (session_id, id)
);

DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['accounts', 'transactions', 'subscriptions'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('DROP POLICY IF EXISTS session_isolation ON %I', table_name);
        EXECUTE format(
            'CREATE POLICY session_isolation ON %I USING (session_id = current_setting(''app.session_id'', true)::uuid) WITH CHECK (session_id = current_setting(''app.session_id'', true)::uuid)',
            table_name
        );
    END LOOP;
END $$;

GRANT USAGE ON SCHEMA public TO demo_runtime;
GRANT SELECT, INSERT ON sessions TO demo_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON accounts, transactions, subscriptions TO demo_runtime;
