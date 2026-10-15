CREATE TABLE IF NOT EXISTS subscription_cancellations (
    id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    subscription_id text NOT NULL,
    status text NOT NULL,
    completed_at timestamptz NOT NULL,
    receipt text NOT NULL,
    UNIQUE (session_id, subscription_id),
    FOREIGN KEY (session_id, subscription_id) REFERENCES subscriptions(session_id, id) ON DELETE CASCADE
);

ALTER TABLE subscription_cancellations ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscription_cancellations FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS session_isolation ON subscription_cancellations;
CREATE POLICY session_isolation ON subscription_cancellations
    USING (session_id = current_setting('app.session_id', true)::uuid)
    WITH CHECK (session_id = current_setting('app.session_id', true)::uuid);

GRANT SELECT, INSERT, DELETE ON subscription_cancellations TO demo_runtime;
