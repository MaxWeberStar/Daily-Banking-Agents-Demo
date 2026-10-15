CREATE TABLE IF NOT EXISTS intent_contexts (
    session_id uuid PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    intent text NOT NULL,
    slots jsonb NOT NULL
);
ALTER TABLE intent_contexts ENABLE ROW LEVEL SECURITY;
ALTER TABLE intent_contexts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS session_isolation ON intent_contexts;
CREATE POLICY session_isolation ON intent_contexts
    USING (session_id = current_setting('app.session_id', true)::uuid)
    WITH CHECK (session_id = current_setting('app.session_id', true)::uuid);
GRANT SELECT, INSERT, UPDATE ON intent_contexts TO demo_runtime;
