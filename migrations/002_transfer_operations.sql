CREATE TABLE IF NOT EXISTS operations (
    id text NOT NULL,
    session_id uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    recipient text NOT NULL,
    amount_cents bigint NOT NULL,
    purpose text NOT NULL,
    summary_hash text NOT NULL,
    verified boolean NOT NULL DEFAULT false,
    confirmed boolean NOT NULL DEFAULT false,
    receipt text,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (session_id, id)
);

GRANT SELECT, INSERT, UPDATE ON operations TO demo_runtime;

ALTER TABLE operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE operations FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS session_isolation ON operations;
CREATE POLICY session_isolation ON operations
    USING (session_id = current_setting('app.session_id', true)::uuid)
    WITH CHECK (session_id = current_setting('app.session_id', true)::uuid);
