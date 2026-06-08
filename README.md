# Analise PNCP

Aplicacao Python para analisar atas de licitacao do PNCP. A interface web e
server-rendered com Flask/Jinja2 e usa o CSS em `design-system/`.

## Uso local

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
flask --app app run --host 0.0.0.0 --port 8000
```

Para OCR de PDFs escaneados, instale Tesseract e Poppler no sistema.

## Testes e lint

```bash
python -m pytest
python -m ruff check .
```

## Docker

```bash
docker build -t licita-demo .
docker run --rm -p 8000:8000 licita-demo
```

## Stack

Python 3.11, Flask, Jinja2, SQLite, requests, pandas, pdfplumber, pytesseract,
pdf2image e python-dateutil.
