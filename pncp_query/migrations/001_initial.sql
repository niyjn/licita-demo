CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    segmento TEXT NOT NULL DEFAULT 'ti',
    status TEXT NOT NULL DEFAULT 'RUNNING',
    data_inicial DATE,
    data_final DATE,
    limite_usado INTEGER,
    limite_origem TEXT,
    target_weekly_leads INTEGER,
    licitacoes_persistidas INTEGER NOT NULL DEFAULT 0,
    compras_qualificadas INTEGER NOT NULL DEFAULT 0,
    pdfs_baixados INTEGER NOT NULL DEFAULT 0,
    pdfs_processados INTEGER NOT NULL DEFAULT 0,
    cnpjs_derrotados_brutos INTEGER NOT NULL DEFAULT 0,
    cnpjs_finais_unicos INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    rate_limit_count INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    args JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS pipeline_stages (
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error_message TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, stage)
);

CREATE TABLE IF NOT EXISTS search_results (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    termo_busca TEXT,
    status_busca TEXT,
    tipo_documento_busca TEXT,
    numero_controle_pncp TEXT,
    orgao_cnpj TEXT,
    ano TEXT,
    numero_sequencial TEXT,
    orgao_nome TEXT,
    uf TEXT,
    municipio_nome TEXT,
    modalidade_licitacao_nome TEXT,
    situacao_nome TEXT,
    valor_global TEXT,
    data_publicacao_pncp TEXT,
    data_atualizacao_pncp TEXT,
    title TEXT,
    description TEXT,
    item_url TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, numero_controle_pncp, orgao_cnpj, ano, numero_sequencial)
);

CREATE TABLE IF NOT EXISTS pncp_purchases (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    orgao_cnpj TEXT NOT NULL,
    ano TEXT NOT NULL,
    numero_sequencial TEXT NOT NULL,
    source_orgao_cnpj TEXT,
    source_ano TEXT,
    source_numero_sequencial TEXT,
    qualificado BOOLEAN NOT NULL DEFAULT false,
    motivos_qualificacao JSONB NOT NULL DEFAULT '[]'::jsonb,
    motivos_exclusao JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'RESOLVED',
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, orgao_cnpj, ano, numero_sequencial)
);

CREATE TABLE IF NOT EXISTS documents (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    purchase_key TEXT,
    titulo TEXT,
    url TEXT,
    file_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'FOUND',
    downloaded_at TIMESTAMPTZ,
    parsed_at TIMESTAMPTZ,
    file_deleted_at TIMESTAMPTZ,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, file_path)
);

CREATE TABLE IF NOT EXISTS pdf_parse_results (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    cnpjs_total JSONB NOT NULL DEFAULT '[]'::jsonb,
    cnpjs_vencedores JSONB NOT NULL DEFAULT '[]'::jsonb,
    cnpjs_derrotados JSONB NOT NULL DEFAULT '[]'::jsonb,
    qualificado_ti BOOLEAN NOT NULL DEFAULT false,
    motivos_qualificacao JSONB NOT NULL DEFAULT '[]'::jsonb,
    motivos_exclusao JSONB NOT NULL DEFAULT '[]'::jsonb,
    origem_texto TEXT,
    erro TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, file_path)
);

CREATE TABLE IF NOT EXISTS lead_candidates (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    segmento TEXT NOT NULL DEFAULT 'ti',
    cnpj TEXT NOT NULL,
    source_file_path TEXT,
    status TEXT NOT NULL DEFAULT 'READY_TO_EXPORT',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, segmento, cnpj)
);

CREATE TABLE IF NOT EXISTS execution_logs (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    stage TEXT,
    level TEXT NOT NULL DEFAULT 'INFO',
    message TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS execution_metrics (
    run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    metric_key TEXT NOT NULL,
    metric_value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, metric_key)
);
