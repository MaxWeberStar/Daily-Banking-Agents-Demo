ALTER TABLE sessions ADD COLUMN IF NOT EXISTS reset_generation integer NOT NULL DEFAULT 0;
GRANT UPDATE ON sessions TO demo_runtime;

CREATE TABLE IF NOT EXISTS reset_audit (
    session_id uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    reset_generation integer NOT NULL,
    occurred_at timestamptz NOT NULL,
    result text NOT NULL,
    PRIMARY KEY (session_id, reset_generation)
);

ALTER TABLE reset_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE reset_audit FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS session_isolation ON reset_audit;
CREATE POLICY session_isolation ON reset_audit
    USING (session_id = current_setting('app.session_id', true)::uuid)
    WITH CHECK (session_id = current_setting('app.session_id', true)::uuid);

GRANT SELECT, INSERT ON reset_audit TO demo_runtime;
GRANT DELETE ON reset_audit, operation_audit, subscription_cancellations,
    bill_category_corrections, operations, intent_contexts, accounts, transactions, subscriptions
    TO demo_runtime;
