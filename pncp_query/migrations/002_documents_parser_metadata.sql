ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS content_sha256 TEXT,
    ADD COLUMN IF NOT EXISTS file_size_bytes BIGINT,
    ADD COLUMN IF NOT EXISTS content_type TEXT,
    ADD COLUMN IF NOT EXISTS magic_type TEXT,
    ADD COLUMN IF NOT EXISTS parent_document_id BIGINT REFERENCES documents(id),
    ADD COLUMN IF NOT EXISTS extracted_from_zip BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_documents_content_sha256 ON documents(content_sha256);
CREATE INDEX IF NOT EXISTS idx_documents_run_status ON documents(run_id, status);
CREATE INDEX IF NOT EXISTS idx_documents_parent_document_id ON documents(parent_document_id);

ALTER TABLE pdf_parse_results
    ADD COLUMN IF NOT EXISTS ocr_attempted BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS ocr_success BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS ocr_error TEXT,
    ADD COLUMN IF NOT EXISTS page_count INTEGER,
    ADD COLUMN IF NOT EXISTS parse_duration_ms INTEGER;
