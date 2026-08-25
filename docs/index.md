# LicitaFinder

O LicitaFinder organiza dados públicos de licitações para ajudar a investigar vencedores,
participantes e sinais de resultado com mais contexto.

Ele consulta o [PNCP](https://www.gov.br/pncp/pt-br), processa editais, contratos e atas, e
apresenta um funil reconciliável no painel web.

## Comece pelo painel

[Abrir o LicitaFinder](https://licitafinder.com.br/){ .md-button .md-button--primary }

Uma análise é criada rapidamente pela API e processada em segundo plano pelo worker. Enquanto
ela roda, o histórico preserva o estado e o progresso da execução.

## O que esta documentação cobre

- uso do painel e criação de análises;
- funcionamento da fila e dos workers;
- isolamento por navegador;
- fontes, limites e interpretação dos resultados;
- arquitetura local e AWS;
- operação, deploy e contribuição.

!!! warning "Interpretação"
    Os resultados são evidências organizadas para investigação. Eles não substituem auditoria,
    conferência documental ou uma decisão oficial.
