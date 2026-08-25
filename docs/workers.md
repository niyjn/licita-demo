# Workers

O worker é um processo independente do Flask. Ele lê a fila persistida no PostgreSQL e pode ser
executado localmente com:

```bash
python -m pncp_query.worker
```

Para processar uma única run e encerrar:

```bash
python -m pncp_query.worker --once
```

## Claim concorrente

O worker usa uma operação transacional de claim para que dois processos não processem a mesma
run. O banco registra worker, tentativa, heartbeat e timestamps.

Isso permite mais de um worker sem duplicar a execução da mesma run, mas não remove limites das
fontes externas. Concorrência deve ser dimensionada de acordo com PNCP, BrasilAPI e capacidade
de CPU/memória.

## ECS

No ECS, web e worker usam a mesma imagem, mas são services distintos. O worker pode ficar com
`desiredCount = 0` quando não houver processamento; nesse caso, é necessário um gatilho para
iniciar uma task quando uma run for criada.
