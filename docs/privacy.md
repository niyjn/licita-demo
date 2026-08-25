# Privacidade

O projeto usa uma identidade anônima por navegador para impedir que usuários compartilhem runs e
perfis acidentalmente.

Não há conta, senha ou e-mail. O cookie é `HttpOnly`, usa `SameSite=Lax` e, em produção, deve
estar com `Secure` habilitado.

O sistema pode armazenar no PostgreSQL parâmetros de análise, progresso, resultados e metadados
necessários para o histórico. Documentos podem ser armazenados localmente durante o processamento
ou no S3 quando a integração estiver configurada.

Não envie segredos, credenciais ou dados pessoais desnecessários nos termos de busca. Para apagar
uma run concluída, use a ação de exclusão do histórico.
