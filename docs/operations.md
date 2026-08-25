# Operação

## Deploy da aplicação

1. Gere a imagem com o hash do commit.
2. Envie a imagem para o ECR.
3. Crie uma nova revisão da task definition.
4. Atualize o service web e aguarde estabilizar.
5. Execute migration antes de usar um schema novo.
6. Atualize o worker quando houver mudança no código de processamento.
7. Invalide o CloudFront quando HTML ou assets mudarem.

Use tags imutáveis, como o hash do commit, em vez de depender apenas de `latest`.

## Health checks

```bash
curl -i https://licitafinder.com.br/healthz
curl -i https://licitafinder.com.br/readyz
```

`/healthz` verifica liveness. `/readyz` verifica conexão com banco e revisão Alembic esperada.

## Diagnóstico rápido

| Sintoma | Primeira verificação |
| --- | --- |
| web não inicia | `DATABASE_URL`, `CSRF_SECRET` e logs da task |
| 502/504 | origin do CloudFront e task web saudável |
| 403 em POST | métodos permitidos no behavior CloudFront |
| histórico vazio | cookie `licita_anon` e `DATABASE_URL` |
| run parada em queued | desired count, logs e claim do worker |
| site antigo | status do CloudFront e invalidação |

## Documentação estática

Gerar o site localmente:

```bash
python -m pip install -r requirements-dev.txt
mkdocs serve
```

Para publicar manualmente no S3:

```bash
mkdocs build --strict
aws s3 sync site/ s3://SEU_BUCKET_DE_DOCUMENTACAO --delete
aws cloudfront create-invalidation --distribution-id ID --paths '/*'
```
