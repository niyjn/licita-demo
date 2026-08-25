# Sessões anônimas

O LicitaFinder não possui login tradicional. Na primeira requisição ao painel, o servidor cria
uma identidade anônima e entrega o cookie `licita_anon`.

## O que fica no navegador

- um token opaco em `licita_anon`;
- a preferência visual da introdução em `localStorage`;
- o tema claro/escuro, quando escolhido.

O servidor armazena apenas o hash do token anônimo. Runs e perfis são vinculados a essa identidade
e não aparecem para outro navegador.

## Consequências

- limpar cookies pode fazer o usuário perder acesso ao histórico anterior;
- o `localStorage` não autoriza operações e não substitui o cookie;
- o token CSRF é derivado da identidade anônima;
- sessões anônimas não oferecem recuperação de conta.

Para um projeto público com contas reais, essa camada deve ser substituída ou complementada por
autenticação persistente.
