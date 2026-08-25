# Contribuição

## Ambiente

```bash
cp .env.example .env
docker compose up -d postgres
export TEST_DATABASE_URL=postgresql://licita:licita@localhost:5432/licita
export DATABASE_URL="$TEST_DATABASE_URL"
```

## Validação

```bash
ruff check .
mkdocs build --strict
python -m pytest
git diff --check
```

Os testes usam PostgreSQL e criam bancos isolados. Não adicione novamente uma implementação
SQLite: web, worker e testes devem exercitar o mesmo backend.

## Commits

Use Conventional Commits, por exemplo:

```text
feat(web): add analysis filter
fix(worker): handle malformed document
docs(operations): explain cloudfront invalidation
```

Mudanças de schema devem incluir migration, testes e instruções de rollout.
