# Como funciona

```text
 navegador
    │
    ▼
 Flask web ── cria ──► run queued
    │                         │
    │                         ▼
    └────────────── PostgreSQL ◄── worker
                                  │
                         PNCP / BrasilAPI / PDFs
```

## Ciclo de uma análise

1. O navegador envia os parâmetros para a API web.
2. A API valida os parâmetros e grava uma run `queued`.
3. O worker reivindica atomicamente a run mais antiga disponível.
4. O processamento consulta fontes externas, baixa documentos e atualiza o progresso.
5. A run termina em `done` ou `error`.
6. O painel consulta o estado e apresenta os resultados salvos.

O processo web não executa a análise dentro da requisição. Isso mantém a interface responsiva e
permite escalar workers separadamente.

## Estados

| Estado | Significado |
| --- | --- |
| `queued` | aguardando um worker |
| `running` | worker processando |
| `done` | execução concluída |
| `error` | execução encerrada com erro |

Uma run em execução não é automaticamente repetida após falha. A decisão de retry deve ser
explícita para evitar duplicar chamadas e downloads.
