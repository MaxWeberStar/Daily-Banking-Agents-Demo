ALTER TABLE operations ADD COLUMN IF NOT EXISTS idempotency_key text;
ALTER TABLE operations ADD COLUMN IF NOT EXISTS requires_verification boolean NOT NULL DEFAULT true;
ALTER TABLE operations ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'pending';
UPDATE operations SET status = COALESCE((receipt::jsonb ->> 'status'), 'pending') WHERE receipt IS NOT NULL;
UPDATE operations SET idempotency_key = id WHERE idempotency_key IS NULL;
ALTER TABLE operations ALTER COLUMN idempotency_key SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS operations_session_idempotency_key
    ON operations (session_id, idempotency_key);

CREATE TABLE IF NOT EXISTS visitor_security (
    session_id uuid PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    failed_codes integer NOT NULL DEFAULT 0,
    locked_until timestamptz
);
ALTER TABLE visitor_security ENABLE ROW LEVEL SECURITY;
ALTER TABLE visitor_security FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS session_isolation ON visitor_security;
CREATE POLICY session_isolation ON visitor_security
    USING (session_id = current_setting('app.session_id', true)::uuid)
    WITH CHECK (session_id = current_setting('app.session_id', true)::uuid);
GRANT SELECT, INSERT, UPDATE ON visitor_security TO demo_runtime;
