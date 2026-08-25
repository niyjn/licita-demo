# Arquitetura AWS

## Componentes

```text
Route 53 → CloudFront/WAF → ECS Fargate web → RDS PostgreSQL
                                      │
                                      └── ECS Fargate worker

ECR guarda a imagem privada das tasks.
S3 armazena documentos quando habilitado.
Secrets Manager fornece segredos às tasks.
```

## Web e worker

Web e worker são services independentes no ECS, mas usam a mesma imagem e `DATABASE_URL`.

- web expõe a porta 8000;
- worker executa `python -m pncp_query.worker`;
- migration roda separadamente antes da aplicação;
- RDS é o estado persistente compartilhado.

## CloudFront

A landing `/` pode ser cacheada. Rotas privadas não devem usar cache compartilhado:

```text
/app*
/runs*
/analises*
/perfis*
/healthz
/readyz
```

Para essas rotas, encaminhe cookies e query strings e use `CachingDisabled`. O cookie
`licita_anon` é necessário para isolar o histórico por navegador.

## Operação segura

- mantenha o bucket de documentação e o bucket de documentos privados;
- use IAM mínimo para web, worker e deploy;
- mantenha `CSRF_SECRET` no Secrets Manager;
- configure alertas de custo;
- prefira um ALB entre CloudFront e ECS em vez de apontar para IP efêmero de task.
