ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS scenario_date date NOT NULL DEFAULT DATE '2026-10-31';

ALTER TABLE operations
    ADD COLUMN IF NOT EXISTS operation_type text NOT NULL DEFAULT 'transfer';

CREATE UNIQUE INDEX IF NOT EXISTS transactions_session_source_entry_id
    ON transactions (session_id, source_entry_id);

CREATE TABLE IF NOT EXISTS scenario_event_applications (
    session_id uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    source_entry_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('not_applied', 'applied')),
    operation_id text,
    applied_at timestamptz,
    PRIMARY KEY (session_id, source_entry_id),
    FOREIGN KEY (session_id, operation_id) REFERENCES operations(session_id, id) ON DELETE CASCADE,
    CHECK (
        (status = 'not_applied' AND operation_id IS NULL AND applied_at IS NULL)
        OR (status = 'applied' AND operation_id IS NOT NULL AND applied_at IS NOT NULL)
    )
);

ALTER TABLE scenario_event_applications ENABLE ROW LEVEL SECURITY;
ALTER TABLE scenario_event_applications FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS session_isolation ON scenario_event_applications;
CREATE POLICY session_isolation ON scenario_event_applications
    USING (session_id = current_setting('app.session_id', true)::uuid)
    WITH CHECK (session_id = current_setting('app.session_id', true)::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON scenario_event_applications TO demo_runtime;
