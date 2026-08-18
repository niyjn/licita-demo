# Análise PNCP

Aplicação web que analisa contratações públicas do [PNCP](https://pncp.gov.br)
(Portal Nacional de Contratações Públicas). Você escolhe **área**, **período** e
**UF**, e a aplicação monta, por contrato, **quem venceu e quem participou** — com
CNPJ e nome — junto de um **relatório de qualidade reconciliável** dos dados extraídos.

> Projeto de demonstração sobre dados abertos. As listas de palavras-chave em
> `pncp_query/config.py` são exemplos ilustrativos — ajuste-as para o nicho que quiser.

## O que faz

1. **Busca** compras no PNCP por palavras-chave da área, período e UF (filtro `ufs` na API).
2. **Vencedores** vêm da API estruturada de resultados do PNCP (CNPJ, razão social e valor homologado) — sem depender de PDF.
3. **Demais participantes** só são confirmados quando um PDF apresenta contexto explícito de participação e o CNPJ não pertence ao vencedor nem ao órgão comprador.
4. **Persiste** tudo em SQLite e exibe numa interface server-rendered (Flask + Jinja2).

As áreas de exemplo disponíveis são `TI`, `ENGENHARIA` e `SAUDE`, e o filtro cobre as 27 UFs.

## Relatório de qualidade (o diferencial)

Em vez de cuspir uma lista, a aplicação mostra um funil **reconciliável** por execução,
em que cada número é rastreável e a aritmética fecha:

```
CNPJs únicos extraídos das atas ...... X
  − dígito verificador inválido ...... a
  − órgão comprador .................. b
  − coincidente com o vencedor ....... c
  − candidato inconclusivo ............ d
= Perdedores confirmados .............. Y     (X = a + b + c + d + Y)

Vencedores (fonte estruturada PNCP) .. Z     (não passam pela limpeza)
Resultado final = Y + Z
```

Cada número é clicável e lista os CNPJs daquele estágio, com a fonte (`estruturada` ou `ata`)
e o motivo da remoção. Vencedores nunca entram em `X`, porque vêm de fonte diferente dos PDFs.

Os documentos são processados em duas passagens: primeiro atas, julgamentos, classificações,
habilitações, propostas e resultados; se ninguém for confirmado, até três PDFs adicionais são
processados como fallback. Cada arquivo é limitado a 15 MB. Sem vencedor estruturado ou sem
contexto explícito no PDF, o CNPJ permanece inconclusivo e não entra no resultado final.

> [!NOTE]
> **Heurística de Órgão Comprador Aprimorada:** O descarte do órgão comprador (filtro `b`) 
> é realizado comparando apenas o **CNPJ Raiz (primeiros 8 dígitos)**. Isso garante que o órgão 
> comprador seja devidamente identificado e excluído mesmo que a ata em PDF cite o CNPJ da matriz 
> e a licitação no PNCP tenha sido cadastrada sob o CNPJ de uma de suas filiais/departamentos.

## Arquitetura

- **Flask + Jinja2** — frontend server-rendered, estilizado pelo CSS em `design-system/`.
- **Web e worker separados** — `POST /analises` apenas persiste uma run `queued`; o worker (`python -m pncp_query.worker`) a reivindica e executa. O front faz polling em `GET /analises/<run_id>/status`.
- **Uma análise ativa por vez** — o SQLite impede atomicamente uma segunda run em estado `queued/running`; o claim usa uma transação SQLite para que dois workers não processem a mesma run.
- **Histórico e modelos** — runs ficam disponíveis em `/runs`; termos livres podem ser salvos como modelos reutilizáveis.
- **SQLite** (WAL + `busy_timeout`) — sem servidor de banco; roda inteiro em um container.
- **Serviços** — busca PNCP, resultado estruturado, downloader de atas (download atômico), parser PDF/OCR, validação de CNPJ e enriquecimento por BrasilAPI.

## Rodando

### Local

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
flask --app app run --host 0.0.0.0 --port 8000
```

Para OCR de PDFs escaneados, instale o [Tesseract](https://github.com/tesseract-ocr/tesseract) e o [Poppler](https://poppler.freedesktop.org/).

### Docker

#### Container Único (sem persistência simples)

```bash
docker build -t licita-demo .
docker run --rm -p 8000:8000 licita-demo
# http://localhost:8000
```

#### Docker Compose (com persistência de banco e PDFs)

```bash
docker compose up --build
# http://localhost:8000
```

O Compose inicia os serviços `web` e `worker` a partir da mesma imagem e compartilha o volume
`/app/output`, que contém o banco SQLite e os PDFs. Para depuração, também é possível executar:

```bash
# processa no máximo uma run queued e encerra
python -m pncp_query.worker --once

# worker contínuo
python -m pncp_query.worker
```

SQLite continua sendo uma solução single-host nesta etapa. Não escale workers horizontalmente
sem revisar a operação do banco. Uma run interrompida é marcada como erro por timeout; ela não é
retentada nem retomada automaticamente.

## Testes e lint

```bash
python -m pytest
ruff check .
```

## Ressalvas honestas

- **"Participantes" são inferidos das atas**, a partir dos CNPJs válidos encontrados no PDF após limpeza — não é uma classificação oficial do PNCP. Contratos sem ata ou sem participantes legíveis ficam fora do resultado principal (acessíveis por toggle).
- **O nome do participante é best-effort**: vem da BrasilAPI, que tem rate limit. Sob carga, alguns participantes aparecem só com o CNPJ. O vencedor sempre tem nome (fonte estruturada).

## Stack

Python 3.11 · Flask · Jinja2 · SQLite · requests · pdfplumber · pytesseract · pdf2image · python-dateutil
