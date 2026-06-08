# Follow-ups

## Revisar deteccao de vencedores e derrotados

A regra atual identifica CNPJs vencedores por heuristicas textuais e classifica como derrotados os CNPJs restantes em PDFs qualificados. Isso e suficiente para triagem inicial, mas pode gerar falsos positivos quando documentos incluem CNPJs de orgaos, assinantes, consorcios, anexos ou fornecedores citados fora da classificacao.

Proxima etapa recomendada:

- Montar fixtures reais de atas, termos de homologacao e relatorios de julgamento.
- Guardar evidencia textual ao redor de cada CNPJ extraido.
- Atribuir score de confianca para vencedor/derrotado.
- Exportar a fonte da decisao junto do lead para revisao comercial.
