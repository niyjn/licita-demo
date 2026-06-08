# Pipeline PNCP — Extração e estruturação de licitações públicas

Pipeline de dados em Python que coleta licitações públicas do [PNCP](https://pncp.gov.br)
(Portal Nacional de Contratações Públicas), baixa os documentos, extrai os CNPJs
participantes via parsing de PDF (com fallback de OCR) e exporta um conjunto
estruturado em CSV / PostgreSQL.

> Projeto de demonstração. As listas de palavras-chave em `config.py` são exemplos
> ilustrativos — ajuste-as para o nicho de licitação que você quiser rastrear.

## O que faz

1. **Busca** licitações no PNCP por palavras-chave, tipo de documento e status.
2. **Qualifica** os resultados por termos de inclusão e exclusão (reduz falsos positivos).
3. **Baixa** os documentos PDF das licitações qualificadas (download atômico).
4. **Extrai** CNPJs dos PDFs via texto nativo (`pdftotext`/`pdfplumber`) com fallback de OCR.
5. **Limpa e exporta** o resultado em CSV (dedup + validação de dígito verificador de CNPJ).

## Destaques de engenharia

- Cliente HTTP com retry, backoff exponencial + jitter e respeito ao `Retry-After`.
- Download **atômico** (arquivo temporário + rename) — nunca deixa PDF parcial em disco.
- Parsing JSON resiliente com fallback entre endpoints da API.
- Extração de PDF com OCR página a página (controla uso de memória).
- Notificação de falha via **webhook** opcional (fail-safe).
- Checkpoint opcional em **PostgreSQL** com migrations versionadas.
- Suíte de **testes** (`pytest`) e linting com **Ruff**.
- **Docker** + `docker-compose` para subir o banco e rodar o pipeline.

## Estrutura

```
.
├── pipeline.py                 # Orquestrador completo (com checkpoint opcional)
├── run.py / downloader.py / parser_pdf.py / limpeza.py   # Entrypoints por fase
├── pncp_query/
│   ├── config.py               # Palavras-chave de exemplo e configurações
│   ├── models.py
│   ├── controllers/            # Orquestração de cada fase
│   ├── services/               # Lógica isolada por responsabilidade
│   └── migrations/             # Schema PostgreSQL
└── tests/
```

## Instalação

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt   # testes e lint
```

Para OCR de PDFs escaneados, instale o [Tesseract](https://github.com/tesseract-ocr/tesseract)
e o [Poppler](https://poppler.freedesktop.org/) no sistema.

## Uso

### Pipeline completo com PostgreSQL

```bash
cp .env.example .env
docker compose up -d postgres
python pipeline.py --migrate-only
python pipeline.py
```

| Argumento | Descrição |
|---|---|
| `--limite 50` | Força limite manual por termo/perfil |
| `--resume-run-id <id>` | Retoma uma execução registrada |
| `--force-parse` | Reprocessa PDFs já parseados |
| `--cleanup-retention` | Remove PDFs antigos registrados no banco |
| `--no-db-checkpoint` | Usa o modo simples por arquivos (sem PostgreSQL) |

### Modo simples (sem banco), por fase

```bash
python run.py --data-inicial 2025-04-01 --data-final 2025-04-30
python downloader.py
python parser_pdf.py
python limpeza.py
```

## Testes e lint

```bash
python -m pytest
ruff check .
```

## Stack

Python 3.11 · requests · pandas · pdfplumber · pytesseract · pdf2image · psycopg · python-dateutil
