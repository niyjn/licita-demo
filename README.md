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
4. **Persiste** tudo em PostgreSQL e exibe numa interface server-rendered (Flask + Jinja2).

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
- **Fila concorrente** — várias runs podem permanecer `queued`; cada worker usa `FOR UPDATE SKIP LOCKED` em uma única transação para reivindicar uma run sem duplicação.
- **Histórico e modelos** — runs ficam disponíveis em `/runs`; termos livres podem ser salvos como modelos reutilizáveis.
- **PostgreSQL + Alembic** — web e worker usam a mesma `DATABASE_URL`; migrations são executadas separadamente do startup.
- **Serviços** — busca PNCP, resultado estruturado, downloader de atas (download atômico), parser PDF/OCR, validação de CNPJ e enriquecimento por BrasilAPI.

## Rodando

### Local

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt

# opcional: copie e ajuste as configurações locais
cp .env.example .env

# aplica schema (repita a cada atualização que contenha migrations)
alembic upgrade head

# terminal 1: servidor web
flask --app app run --host 0.0.0.0 --port 8000

# terminal 2: consumidor da fila
python -m pncp_query.worker
```

O processo web apenas cria runs em estado `queued`. Sem o worker, a interface continua
acessível, mas as análises permanecem na fila.

Para OCR de PDFs escaneados, instale o [Tesseract](https://github.com/tesseract-ocr/tesseract) e o [Poppler](https://poppler.freedesktop.org/).

### Docker

O modo recomendado é o Docker Compose: ele inicia PostgreSQL, executa uma task de migration
e só então inicia web e worker:

```bash
docker compose up --build
# http://localhost:8000
```

O volume persistente pertence somente ao PostgreSQL. Cada web/worker mantém seu próprio
cache local de PDFs, efêmero e não compartilhado; com `S3_BUCKET_NAME` configurado, cada
documento é gravado com key por run/compra e sua URL original, hash e key são rastreados na
tabela `documentos`.

Executar somente a imagem inicia apenas o servidor web e é útil para diagnóstico, mas não
consome a fila:

```bash
docker build --build-arg APP_VERSION=$(git rev-parse --short HEAD) -t licita-demo .
docker run --rm -p 8000:8000 \
  -e DATABASE_URL=postgresql://user:password@db.example:5432/licita \
  -e APP_VERSION=$(git rev-parse --short HEAD) \
  licita-demo
```

### Worker CLI

```bash
# processa no máximo uma run queued e encerra
python -m pncp_query.worker --once

# worker contínuo; o intervalo padrão de polling é 2 segundos
python -m pncp_query.worker

# ajusta o intervalo de polling
python -m pncp_query.worker --poll-interval 5
```

Workers podem escalar horizontalmente. Ao iniciar ou manter um worker ativo, uma run `running`
sem heartbeat por mais de uma hora é marcada como erro; ela não é retentada nem retomada
automaticamente. Runs `queued` permanecem persistidas até que um worker esteja disponível.

### Deploy AWS ECS Fargate & CloudFront (Produção / MVP)

Para rodar a aplicação em produção na AWS com custos otimizados (sem utilizar o custo de um Application Load Balancer), adote a seguinte arquitetura de rede:

1. **ECS Fargate (Computação)**:
   - Crie duas Task Definitions (uma para `web` e outra para `worker`) utilizando a imagem Docker.
   - **Comando do Worker**: Defina o comando de inicialização na Task Definition como `python,-m,pncp_query.worker`.
   - **Importação de Módulos**: O `Dockerfile` já injeta `PYTHONPATH=/app` por padrão para garantir a correta importação de módulos no contêiner.

2. **CloudFront (CDN & HTTPS)**:
   - Aponte a **Origem** do CloudFront para o DNS reverso do IP público da tarefa do ECS Fargate (formato `ec2-XX-XX-XX-XX.[region].compute.amazonaws.com`).
   - Configure a porta de origem como `8000` e o protocolo de comunicação como **`HTTP Only`** (o Gunicorn executa em HTTP limpo).
   - **Origin Request Policy**: Altere no Behavior da distribuição para **`AllViewerExceptHostHeader`**. Isso evita que o roteamento de borda da AWS descarte as requisições por incompatibilidade do cabeçalho `Host`.
   - **Desativação de IPv6**: Se os contêineres rodarem apenas em IPv4, desative a opção `IPv6` nas configurações gerais da distribuição CloudFront para evitar erros de Timeout 504 no roteamento.

3. **Security Groups (Firewall)**:
   - **Porta 8000**: Liberada para `0.0.0.0/0` (Anywhere-IPv4) permitindo o tráfego do CloudFront.
   - **ICMP (MTU Path Discovery)**: Adicione uma regra de entrada para `All ICMP - IPv4` vinda de `0.0.0.0/0` no Security Group dos contêineres para evitar que pacotes grandes do CloudFront sejam bloqueados por incompatibilidade de fragmentação de rede.
   - **Porta 5432 (PostgreSQL)**: Trancada estritamente para o IP de desenvolvimento e para o bloco de rede interna da VPC (ex: `172.31.0.0/16`) permitindo a comunicação segura das tarefas Fargate com o banco RDS.

### Configuração

As variáveis de ambiente documentadas em `.env.example` controlam o banco, os PDFs, as
tentativas HTTP e os limites de OCR. Os padrões locais são:

```dotenv
DATABASE_URL=postgresql://licita:licita@localhost:5432/licita
DB_POOL_MIN=1
DB_POOL_MAX=5
APP_VERSION=dev
PDF_DIR=output/pdfs
```

`DATABASE_URL` é obrigatória; não existe fallback SQLite. `APP_VERSION` identifica a imagem
nos logs de startup de web e worker. `S3_BUCKET_NAME` e `AWS_REGION`
mantêm compatibilidade com as task definitions existentes; `S3_BUCKET` e `S3_REGION` são
aliases locais opcionais.

### Operação ECS/RDS

Não execute migrations no startup do web ou worker. Para cada deploy, publique uma imagem
imutável (tag pelo SHA do commit e `APP_VERSION` igual ao SHA), tire um snapshot do RDS e execute uma task one-off com
`alembic upgrade head`; só depois atualize o serviço web e os workers. O ALB deve consultar
`/readyz` (retorna 503 até banco e revision Alembic estarem prontos); `/healthz` é somente
liveness. Inicie com um worker, valide uma run pequena, então escale. Logs de startup devem
identificar a imagem/revision sem imprimir a DSN.

## Testes e lint

```bash
TEST_DATABASE_URL=postgresql://licita:licita@localhost:5432/licita python -m pytest
ruff check .
```

## Ressalvas honestas

- **"Participantes" são inferidos das atas**, a partir dos CNPJs válidos encontrados no PDF após limpeza — não é uma classificação oficial do PNCP. Contratos sem ata ou sem participantes legíveis ficam fora do resultado principal (acessíveis por toggle).
- **O nome do participante é best-effort**: vem da BrasilAPI, que tem rate limit. Sob carga, alguns participantes aparecem só com o CNPJ. O vencedor sempre tem nome (fonte estruturada).

## Stack

Python 3.11 · Flask · Jinja2 · PostgreSQL · Alembic · requests · pdfplumber · pytesseract · pdf2image · python-dateutil
