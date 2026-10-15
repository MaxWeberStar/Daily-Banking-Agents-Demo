CREATE TABLE IF NOT EXISTS bill_category_corrections (
    session_id uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    source_entry_id text NOT NULL,
    original_category text NOT NULL,
    new_category text NOT NULL,
    corrected_at text NOT NULL,
    PRIMARY KEY (session_id, source_entry_id)
);

ALTER TABLE bill_category_corrections ENABLE ROW LEVEL SECURITY;
ALTER TABLE bill_category_corrections FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS session_isolation ON bill_category_corrections;
CREATE POLICY session_isolation ON bill_category_corrections
    USING (session_id = current_setting('app.session_id', true)::uuid)
    WITH CHECK (session_id = current_setting('app.session_id', true)::uuid);

GRANT SELECT, INSERT ON bill_category_corrections TO demo_runtime;
