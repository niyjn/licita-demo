# Primeiros passos

## Usar o site

1. Abra o [LicitaFinder](https://licitafinder.com.br/).
2. Escolha uma área fixa ou a busca livre.
3. Informe UF, período e limite.
4. Clique em **Analisar**.
5. Acompanhe a execução no painel ou em **Histórico**.

A página inicial é uma introdução curta. O painel fica em `/app` e pode ser acessado diretamente
depois que a introdução for concluída.

## Executar localmente

O projeto exige PostgreSQL. Não existe fallback SQLite.

```bash
cp .env.example .env
docker compose up --build
```

Abra [http://localhost:8000](http://localhost:8000). O Compose inicia o PostgreSQL, executa a
migration e sobe web e worker.

Para executar apenas o servidor web em modo diagnóstico:

```bash
docker compose up --build postgres migrate web
```

## Configuração mínima

```env
DATABASE_URL=postgresql://licita:licita@localhost:5432/licita
CSRF_SECRET=um-segredo-longo-e-aleatorio
```

`CSRF_SECRET` deve ser secreto, estável entre reinícios e compartilhado somente pelas tasks web.
