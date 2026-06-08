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
3. **Demais participantes** são extraídos do texto das **atas em PDF** (parsing nativo + fallback de OCR), validados e enriquecidos com a razão social via [BrasilAPI](https://brasilapi.com.br).
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
= Participantes no resultado final ... Y     (X = a + b + c + Y, por construção)

Vencedores (fonte estruturada PNCP) .. Z     (não passam pela limpeza)
Resultado final = Y + Z
```

Cada número é clicável e lista os CNPJs daquele estágio, com a fonte (`estruturada` ou `ata`)
e o motivo da remoção. Vencedores nunca entram em `X`, porque vêm de fonte diferente dos PDFs.

> [!NOTE]
> **Heurística de Órgão Comprador Aprimorada:** O descarte do órgão comprador (filtro `b`) 
> é realizado comparando apenas o **CNPJ Raiz (primeiros 8 dígitos)**. Isso garante que o órgão 
> comprador seja devidamente identificado e excluído mesmo que a ata em PDF cite o CNPJ da matriz 
> e a licitação no PNCP tenha sido cadastrada sob o CNPJ de uma de suas filiais/departamentos.

## Arquitetura

- **Flask + Jinja2** — frontend server-rendered, estilizado pelo CSS em `design-system/`.
- **Execução assíncrona** — `POST /analises` cria uma run, dispara a análise numa thread e retorna `run_id`; o front faz polling em `GET /analises/<run_id>/status`. O status vive no SQLite (não em memória), então funciona com múltiplos workers.
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

## Testes e lint

```bash
python -m pytest
ruff check .
```

## Ressalvas honestas

- **"Participantes" são inferidos das atas**, a partir dos CNPJs válidos encontrados no PDF após limpeza — não é uma classificação oficial do PNCP. Contratos sem ata ou sem participantes legíveis ficam fora do resultado principal (acessíveis por toggle).
- **O nome do participante é best-effort**: vem da BrasilAPI, que tem rate limit. Sob carga, alguns participantes aparecem só com o CNPJ. O vencedor sempre tem nome (fonte estruturada).

## Stack

Python 3.11 · Flask · Jinja2 · SQLite · requests · pandas · pdfplumber · pytesseract · pdf2image · python-dateutil

## Melhorias de Portfólio Implementadas

O projeto passou por uma auditoria completa e recebeu as seguintes melhorias para garantir prontidão de portfólio técnico:

1. **Segurança e Concorrência**:
   - **XSS Mitigado**: Inserção dinâmica no modal sanitizada com função de escape HTML customizada e filtros de detecção de caracteres perigosos no JS.
   - **Usuário Não-Root**: Configuração do `Dockerfile` alterada para rodar a aplicação sob o usuário seguro `appuser` (UID 1000).
   - **Gunicorn Concorrente**: Ajuste para `--workers 2 --threads 4` no container Docker, evitando bloqueio do loop de eventos principal em requisições de polling.
   - **Tratamento de Órfãs e Timeouts**: Recuperação automática de análises travadas no startup do Flask e mecanismo defensivo contra runs travadas no `finally`.

2. **Estabilidade e Banco de Dados**:
   - **Soma do Funil Robusta**: Remoção do `assert` no pipeline principal por validação via exceção explícita, garantindo execução mesmo com compilações python otimizadas (`-O`).
   - **Migrações Não-Destrutivas**: Sistema de upgrade incremental usando `ALTER TABLE` no banco SQLite em substituição à recreação destrutiva (`DROP TABLE`).
   - **Validação de Entrada**: Endpoint `/analises` agora valida o formato das datas (YYYY-MM-DD), intervalo temporal lógico, sigla de UFs e limites de registros (1-100), retornando 400 Bad Request em erros.
   - **Paginação e Fallback no PNCP**: Implementado loop de paginação no `ResultadoService` para buscar mais de 100 itens adjudicados.
   - **Correção da Situação Cadastral**: Campo `situacao_cadastral` recuperado do BrasilAPI agora é persistido corretamente em todos os registros de auditoria gerados das atas.

3. **Frontend e Estilo Visual**:
   - **Modularidade Visual**: Limpeza de estilos não-utilizados e implementação das classes de layout e UI que estavam ausentes (`.btn`, `.btn-primary`, `.btn-secondary`, `.blank-state`, `.pill.success`, `.eyebrow`, `.panel-header`, `.page-header`, `.table-card`).
   - **Tipografia**: Carregamento forçado das fontes oficiais (Inter e Sora) a partir do Google Fonts direto no cabeçalho.
   - **Responsividade**: Ajustada a largura mínima global de tabelas para `100%` (dentro de contêineres com scroll horizontal), evitando quebra do layout em resoluções mais baixas.
   - **Limpeza**: Remoção de classes e arquivos de filtro mortos (`CandidateFilter`).

4. **Testes Unitários**:
   - Adicionada suíte de testes unitários para o parser de atas (`pdf_parser_service.py`) cobrindo detecção de vencedores, proximidade espacial e conversão.
   - Adicionados testes para a heurística de descarte direto (`_motivo_descarte`) de dispensas e inexigibilidades.
