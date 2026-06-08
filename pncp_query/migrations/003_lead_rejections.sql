CREATE TABLE IF NOT EXISTS rejected_lead_candidates (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    segmento TEXT NOT NULL DEFAULT 'ti',
    cnpj TEXT NOT NULL,
    source_file_path TEXT,
    reason TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, segmento, cnpj, source_file_path, reason)
);

CREATE INDEX IF NOT EXISTS idx_rejected_lead_candidates_run_segment_reason
    ON rejected_lead_candidates (run_id, segmento, reason);
