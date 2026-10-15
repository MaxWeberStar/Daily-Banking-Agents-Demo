CREATE TABLE IF NOT EXISTS operation_audit (
    id bigserial PRIMARY KEY,
    operation_id uuid NOT NULL,
    session_id uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    action text NOT NULL,
    status text NOT NULL,
    parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
    confirmation jsonb,
    receipt jsonb,
    occurred_at timestamptz NOT NULL,
    error_code text
);

CREATE INDEX IF NOT EXISTS operation_audit_session_operation
    ON operation_audit (session_id, operation_id, id);

ALTER TABLE operation_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE operation_audit FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS session_isolation ON operation_audit;
CREATE POLICY session_isolation ON operation_audit
    USING (session_id = current_setting('app.session_id', true)::uuid)
    WITH CHECK (session_id = current_setting('app.session_id', true)::uuid);

GRANT SELECT, INSERT ON operation_audit TO demo_runtime;
GRANT USAGE, SELECT ON SEQUENCE operation_audit_id_seq TO demo_runtime;
