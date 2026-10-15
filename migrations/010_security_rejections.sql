CREATE TABLE IF NOT EXISTS security_rejections (
    id bigserial PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    occurred_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS security_rejections_session_time
    ON security_rejections (session_id, occurred_at DESC);

ALTER TABLE security_rejections ENABLE ROW LEVEL SECURITY;
ALTER TABLE security_rejections FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS session_isolation ON security_rejections;
CREATE POLICY session_isolation ON security_rejections
    USING (session_id = current_setting('app.session_id', true)::uuid)
    WITH CHECK (session_id = current_setting('app.session_id', true)::uuid);

GRANT SELECT, INSERT, DELETE ON security_rejections TO demo_runtime;
GRANT USAGE, SELECT ON SEQUENCE security_rejections_id_seq TO demo_runtime;
