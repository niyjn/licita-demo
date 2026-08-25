# Análises

## Área fixa

Escolha uma área pré-configurada, como TI, Engenharia ou Saúde. O sistema transforma a área em
termos de busca conhecidos.

## Busca livre

Informe termos separados por vírgula, por exemplo:

```text
firewall, data center, licenciamento
```

Termos repetidos são removidos e a busca aceita até doze termos válidos. Termos muito curtos são
rejeitados para evitar consultas pouco úteis.

## Parâmetros

- **UF:** estado usado no recorte da consulta;
- **Início/Fim:** período da busca;
- **Limite:** quantidade máxima retornada por combinação;
- **Modelo:** conjunto salvo de termos da busca livre.

Limites maiores aumentam chamadas externas, downloads e duração. Para validar o fluxo, comece
com limite 1 ou 2.

## Funil reconciliável

O painel separa contratos descartados, vazios e com resultado. CNPJs podem ser encontrados em
atas e documentos, mas a presença textual não prova, sozinha, participação ou vitória.

A análise pode ser exportada em JSON e consultada novamente pelo histórico.
