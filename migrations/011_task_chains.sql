CREATE TABLE IF NOT EXISTS task_chains (
    id text NOT NULL,
    session_id uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    status text NOT NULL,
    plan jsonb NOT NULL,
    step_state jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (session_id, id)
);

CREATE TABLE IF NOT EXISTS task_events (
    id bigserial PRIMARY KEY,
    task_id text NOT NULL,
    session_id uuid NOT NULL,
    step_id text,
    operation_id text,
    action text NOT NULL,
    status text NOT NULL,
    details jsonb NOT NULL,
    occurred_at timestamptz NOT NULL,
    FOREIGN KEY (session_id, task_id) REFERENCES task_chains(session_id, id) ON DELETE CASCADE
);

ALTER TABLE operations ADD COLUMN IF NOT EXISTS task_id text;
ALTER TABLE operations ADD COLUMN IF NOT EXISTS step_id text;

DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['task_chains', 'task_events'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('DROP POLICY IF EXISTS session_isolation ON %I', table_name);
        EXECUTE format(
            'CREATE POLICY session_isolation ON %I USING (session_id = current_setting(''app.session_id'', true)::uuid) WITH CHECK (session_id = current_setting(''app.session_id'', true)::uuid)',
            table_name
        );
    END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON task_chains TO demo_runtime;
GRANT SELECT, INSERT, DELETE ON task_events TO demo_runtime;
GRANT USAGE, SELECT ON SEQUENCE task_events_id_seq TO demo_runtime;
