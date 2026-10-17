/**
 * The control API reference in Portuguese: the contract descriptions and those
 * the documentation adds (control.en.ts). Keys carry the `control:` prefix.
 */
export const PT: Record<string, string> = {
  // Descriptions from the contract itself
  'control:op:get /v1/snapshot/{cell_id}':
    'Com `after`, a chamada espera: responde assim que existir uma versão mais nova, ou devolve 304 no prazo.',
  'control:ConfigTypeOut.roles': 'Os papéis que podem escrever e aprovar este tipo, além do admin.',
  'control:IssuedKeyOut.secret': 'A chave completa. Aparece uma vez só; a Niadra guarda apenas dois hashes dela.',
  'control:KeyCreate.scopes': 'Por padrão, os escopos seguem a audiência da fonte.',
  'control:MeOut.name': 'Ainda não coletado; reservado para os perfis vindos do SSO.',
  'control:MeOut.spaces': 'Cada espaço que a pessoa pode abrir, com os papéis que ela tem em cada um.',
  'control:RevocationFeedOut':
    '`body` é `{"cell_id", "seq", "keys": [{"space_id", "key_id"}], "sources": [...]}`, o feed de revogações da célula; `signature` (Ed25519, base64url) cobre o JSON canônico dele. Na próxima chamada, passe `after=seq`.',
  'control:Role': 'Papéis do Console, combináveis.',
  'control:SnapshotOut':
    '`signature` cobre o JSON canônico de `manifest`, e `keys_signature`, o de `keys` (os dois em Ed25519, base64url). O SHA-256 do JSON canônico de cada documento precisa bater com o manifesto.',
  'control:UserOut.totp_uri': 'Aparece uma vez só, na criação, para cadastrar o segundo fator.',
  'control:UserOut.invitation': 'Aparece uma vez só: na criação, para uma pessoa criada sem senha.',
  'control:UserOut.invitation_expires_at': 'Até quando o link de convite aberto da pessoa vale; nulo quando não há convite aberto.',
  'control:UserOut.password_set': 'Falso enquanto uma pessoa convidada não escolheu a senha, e para quem o SSO criou.',
  'control:UserOut.second_factor_confirmed_at': 'Quando o autenticador em uso foi confirmado; nulo enquanto não há um.',
  'control:UserOut.sso': 'O domínio do e-mail pertence à conexão de SSO do tenant.',
  'control:UserStateRequest.reason': 'O motivo, guardado no histórico de acesso da pessoa.',
  'control:RolesSet.reason': 'O motivo, guardado no histórico de acesso da pessoa.',
  'control:InvitationOut.token':
    'Aparece uma vez só. O Console transforma o token no link `<console>/console/convite#<token>`, que entra uma vez e até `expires_at`. A Niadra guarda só um hash com chave dele.',
  'control:InvitationAcceptOut.email': 'O e-mail da pessoa, para o login que vem em seguida.',
  'control:AccessChangeOut.actor_id': 'Quem fez a mudança; nulo quando o provedor de identidade do tenant a fez num login.',
  'control:MeOut.password_set': 'Falso para uma pessoa criada pelo SSO do tenant: ela entra só por ele.',
  'control:MeOut.sso': 'O domínio do e-mail pertence à conexão de SSO do tenant.',
  'control:PasswordRequest.totp':
    'No lugar da senha, só para uma pessoa criada pelo SSO do tenant, que não tem senha: o código atual do aplicativo autenticador.',
  'control:CertificateOut.pem': 'O certificado como está guardado: é público, e o Console o mostra para o próximo salvamento.',
  'control:OidcIn.client_secret':
    'Obrigatório quando a conexão é criada ou passa a ser OIDC; nulo mantém o guardado. Nunca volta nas respostas: fica guardado cifrado com a chave do plano de controle.',
  'control:OidcIn.issuer': 'O emissor do provedor, com https. A Niadra lê `<issuer>/.well-known/openid-configuration`.',
  'control:OidcIn.scopes': 'Escopos além de `openid email profile`, que vão sempre, como `groups`.',
  'control:OidcOut.client_secret_set': 'Sempre verdadeiro: o segredo em si nunca volta.',
  'control:RoleMappingModel.group':
    'Um valor da declaração (OIDC) ou do atributo (SAML) de grupos: o nome de um grupo, ou um id, como o id de objeto que o Microsoft Entra ID manda. A comparação ignora maiúsculas.',
  'control:RoleMappingModel.role': 'Vale no tenant inteiro. O papel vendor nunca vem de um grupo.',
  'control:SamlIn.idp_certificate': 'O certificado que assina as asserções, em PEM ou o conteúdo em base64 dos metadados.',
  'control:SamlIn.idp_entity_id': 'O entity ID (emissor) do provedor.',
  'control:SamlIn.idp_sso_url': 'O endereço de login do provedor para o binding HTTP-Redirect, com https.',
  'control:ServiceProviderOut': 'O que colar no provedor de identidade.',
  'control:ServiceProviderOut.oidc_redirect_uri': 'OIDC: a URI de redirecionamento (callback) da aplicação.',
  'control:ServiceProviderOut.saml_acs_url': 'SAML: o assertion consumer service, no binding HTTP-POST.',
  'control:ServiceProviderOut.saml_entity_id': 'SAML: o entity ID do provedor de serviço (o público da asserção).',
  'control:ServiceProviderOut.saml_metadata_url': 'SAML: os metadados do provedor de serviço, depois que a conexão existe.',
  'control:SsoConnectionIn.default_role': 'Para quem não está em nenhum grupo mapeado; nulo deixa entrar só quem está num grupo mapeado.',
  'control:SsoConnectionIn.domains':
    'Os domínios de e-mail cujas pessoas entram por esta conexão. Cada domínio é de um tenant só; e-mails públicos (gmail.com e parecidos) são recusados.',
  'control:SsoConnectionIn.enforced':
    'Pessoas dos domínios entram só pelo SSO. Quem é admin no tenant inteiro mantém a senha, como acesso quando o provedor falha.',
  'control:SsoConnectionIn.groups_attribute': 'A declaração (OIDC) ou o atributo (SAML) que traz os grupos.',
  'control:SsoConnectionIn.reason': 'O motivo, guardado no histórico de acesso do tenant.',
  'control:SsoConnectionIn.require_second_factor': 'Pede o código do autenticador da Niadra depois do provedor, além do MFA do provedor.',
  'control:SsoDiscoverOut.enforced': 'Só os admins do tenant inteiro nesse domínio ainda podem usar senha.',
  'control:SsoDiscoverOut.sso': 'O domínio do e-mail entra pelo SSO de um tenant. Não diz nada sobre a pessoa.',
  'control:SsoExchangeRequest.code': 'O código de login de uso único que o plano de controle pôs no fragmento do endereço do Console.',
  'control:SsoExchangeRequest.totp': 'Quando o tenant pede o segundo fator da Niadra.',
  'control:SsoExchangeRequest.verifier': 'O valor cujo SHA-256 foi como `binding`.',
  'control:SsoOut.connection': 'Nulo enquanto o tenant não tem conexão.',
  'control:SsoRemoveRequest.reason': 'O motivo, guardado no histórico de acesso do tenant.',
  'control:SsoStartOut.authorize_url': 'Para onde mandar o navegador: o login do provedor.',
  'control:SsoStartRequest.binding':
    'base64url, sem preenchimento, do SHA-256 de um valor aleatório que o Console guarda para esta aba e manda de novo na troca.',
  'control:SsoTestOut.authorize_url': 'Abra em outra aba: o login do provedor, em modo de teste.',
  'control:SsoTestResultOut.email': 'O e-mail que o provedor mandou para a conta usada no teste.',
  'control:SsoTestResultOut.failure':
    'Por que um login de verdade seria recusado: denied, expired, invalid, domain, no_role, other_tenant, deactivated, unavailable ou disabled.',
  'control:SsoTestResultOut.ok': 'Nulo enquanto o teste espera o provedor.',
  'control:SsoTestResultOut.roles': 'Os papéis que essa conta receberia.',
  'control:Verification': 'Níveis de verificação da sessão. `no_customer` fica fora da escala de V0 a V4.',

  // Access
  'control:op:post /v1/auth/login':
    'Autentica uma pessoa com e-mail, senha e o código TOTP e devolve o token que o Console e a API de controle usam. O token vale 15 minutos e abre um espaço; quem ainda não confirmou o autenticador recebe 401 "second factor enrollment required" e o configura com POST /v1/auth/second-factor. O login que confirma o autenticador devolve também dez códigos de recuperação, uma vez só; quem perdeu o aplicativo manda um deles em `recovery_code`, no lugar de `totp`. Pessoas de um domínio com o SSO do tenant entram por POST /v1/auth/sso/start; quando o tenant exige o SSO, a senha fica só para quem é admin no tenant inteiro, e as demais recebem 401 "single sign-on required".',
  'control:res:post /v1/auth/login:200':
    'O token, o prazo dele em segundos e o espaço que ele abre, com os códigos de recuperação quando este login confirmou o autenticador.',
  'control:op:post /v1/auth/second-factor':
    'A configuração do autenticador, para quem recebeu 401 "second factor enrollment required" no login: a senha de novo, e a chave volta. O primeiro código certo no login confirma a chave, e esse login devolve os códigos de recuperação da pessoa; depois disso, esta rota responde 409.',
  'control:SecondFactorOut.secret': 'A chave TOTP em base32, para digitar no aplicativo autenticador.',
  'control:SecondFactorOut.totp_uri': 'O endereço otpauth:// da mesma chave, para um QR code.',
  'control:op:post /v1/auth/token':
    'Emite um token novo para a pessoa autenticada, para renovar a sessão sem novo login ou trocar de espaço. Os papéis são lidos de novo, então um papel removido há pouco já some do token novo.',
  'control:res:post /v1/auth/token:200': 'Um token novo.',
  'control:op:get /.well-known/jwks.json':
    'As chaves públicas (Ed25519) que assinam os tokens de pessoa. As células verificam os tokens com elas sem consultar o controle, então uma leitura no Console nunca espera o plano de controle. Fica em cache por 5 minutos.',
  'control:res:get /.well-known/jwks.json:200': 'Um documento JWKS.',
  'control:op:get /v1/users/me':
    'A pessoa autenticada: tenant, e-mail e cada espaço que ela pode abrir, com os papéis que ela tem em cada um. O Console monta os menus a partir desta resposta.',
  'control:res:get /v1/users/me:200': 'A pessoa e os espaços dela.',
  'control:op:get /v1/users/me/preferences':
    'O que o Console guarda para a pessoa que está na sessão, entre navegadores: os tutoriais feitos, a boas-vindas vista. Só a própria pessoa lê e grava as suas.',
  'control:res:get /v1/users/me/preferences:200': 'As preferências da pessoa, por chave.',
  'control:op:put /v1/users/me/preferences/{key}':
    'Grava uma chave e devolve todas. Uma pessoa guarda no máximo 16 chaves de 4096 bytes de JSON cada; passando de qualquer um dos dois, 422.',
  'control:res:put /v1/users/me/preferences/{key}:200': 'Todas as preferências da pessoa, com a chave gravada.',
  'control:param:put /v1/users/me/preferences/{key}:key': 'O nome da preferência: de 1 a 64 caracteres entre a-z, 0-9, `_`, `.` e `-`, começando por letra.',
  'control:PreferenceIn.value': 'Qualquer valor JSON menos null, com no máximo 4096 bytes depois de serializado.',
  'control:PreferencesOut': 'O que o Console guarda para a pessoa que está na sessão, entre navegadores, por chave.',
  'control:op:get /v1/users/me/second-factor':
    'O segundo fator da pessoa autenticada: quando o aplicativo autenticador em uso foi confirmado e quantos códigos de recuperação restam.',
  'control:res:get /v1/users/me/second-factor:200': 'O segundo fator da pessoa.',
  'control:op:post /v1/users/me/recovery-codes':
    'Dez códigos de recuperação novos para a pessoa autenticada, mostrados uma vez só; os anteriores param de valer. Pede a senha de novo, e uma senha errada responde 403, para que um erro de digitação nunca encerre a sessão. Responde 409 enquanto a pessoa não tem um autenticador confirmado.',
  'control:res:post /v1/users/me/recovery-codes:200': 'Os códigos de recuperação novos, mostrados uma vez só.',
  'control:op:post /v1/users/me/second-factor':
    'Começa a troca para outro aplicativo autenticador, depois da senha de novo (403 quando errada). A chave nova volta e espera ao lado da atual, que continua valendo até POST /v1/users/me/second-factor/confirm receber o primeiro código do aplicativo novo. Começar de novo substitui uma chave que nunca foi confirmada.',
  'control:res:post /v1/users/me/second-factor:200': 'A chave nova, pendente até o primeiro código.',
  'control:op:post /v1/users/me/second-factor/confirm':
    'Troca para o aplicativo autenticador novo com o primeiro código dele: o aplicativo anterior para de valer, e dez códigos de recuperação novos voltam, mostrados uma vez só. Um código errado responde 403; sem troca começada, 409.',
  'control:res:post /v1/users/me/second-factor/confirm:200': 'Os códigos de recuperação novos, mostrados uma vez só.',
  'control:op:get /v1/users':
    'Todas as pessoas do tenant, com os papéis, como cada uma entra e o estado do segundo fator.',
  'control:res:get /v1/users:200': 'As pessoas do tenant.',
  'control:op:post /v1/users':
    'Uma pessoa nova no tenant. Sem `password`, a resposta traz o convite, mostrado uma vez só: um link de uso único em que a pessoa escolhe a senha; o autenticador vem no primeiro login. Uma pessoa cujo domínio de e-mail é do SSO do tenant entra por ele e não recebe convite.',
  'control:res:post /v1/users:201': 'A pessoa, com o convite (mostrado uma vez só) quando criada sem senha.',
  'control:op:put /v1/users/{user_id}/roles':
    'Troca todos os papéis da pessoa. O tenant mantém ao menos um admin (409 no caso contrário). Para quem entra pelo SSO do tenant, o próximo login volta a definir os papéis pelos grupos do provedor.',
  'control:res:put /v1/users/{user_id}/roles:200': 'Os papéis em vigor.',
  'control:op:post /v1/users/{user_id}/second-factor/reset':
    'Para uma pessoa que perdeu o aplicativo autenticador e os códigos de recuperação: apaga a chave, a confirmação e todos os códigos, e a pessoa configura um autenticador de novo no próximo login. A redefinição fica guardada com quem fez e por quê. Exige o papel admin; uma pessoa de outro tenant responde 404.',
  'control:res:post /v1/users/{user_id}/second-factor/reset:200': 'A redefinição, com quem fez e por quê.',
  'control:op:post /v1/users/{user_id}/deactivate':
    'A pessoa deixa de entrar, e o plano de controle recusa o token dela na hora; a API de dados aceita um token já emitido até ele vencer, em 15 minutos no máximo. Convites abertos param de valer. Responde 409 para a própria pessoa que chama e para o último admin do tenant.',
  'control:res:post /v1/users/{user_id}/deactivate:200': 'A pessoa, desativada.',
  'control:op:post /v1/users/{user_id}/reactivate':
    'Deixa uma pessoa desativada entrar de novo, com os mesmos papéis, a mesma senha e o mesmo segundo fator.',
  'control:res:post /v1/users/{user_id}/reactivate:200': 'A pessoa, ativa de novo.',
  'control:op:post /v1/users/{user_id}/invitation':
    'Um link novo de uso único para a pessoa escolher a senha: para quem nunca usou o primeiro link ou esqueceu a senha. Os links anteriores param de valer. Aparece uma vez só.',
  'control:res:post /v1/users/{user_id}/invitation:201': 'O link novo, mostrado uma vez só.',
  'control:op:get /v1/users/{user_id}/history':
    'O que mudou no acesso da pessoa, do mais novo para o mais antigo: convites, papéis, desativação, redefinições do segundo fator e o que o SSO do tenant fez num login, com quem e por quê.',
  'control:res:get /v1/users/{user_id}/history:200': 'As mudanças, da mais nova para a mais antiga.',
  'control:op:post /v1/auth/invitation/preview':
    'De quem é o convite, antes de a pessoa escolher a senha. Responde 401 do mesmo jeito para um link desconhecido, usado ou vencido.',
  'control:res:post /v1/auth/invitation/preview:200': 'De quem é o convite e até quando ele vale.',
  'control:op:post /v1/auth/invitation':
    'Define a senha da pessoa convidada (12 caracteres ou mais) e gasta o link. Depois, a pessoa entra e configura o aplicativo autenticador, como todo mundo.',
  'control:res:post /v1/auth/invitation:200': 'O e-mail, para o login que vem em seguida.',

  // Single sign-on
  'control:op:post /v1/auth/sso/discover':
    'Se o domínio do e-mail entra pelo SSO. Responde pelo domínio, então nunca diz se uma pessoa existe.',
  'control:res:post /v1/auth/sso/discover:200': 'Se o domínio entra pelo SSO.',
  'control:op:post /v1/auth/sso/start':
    'O endereço do provedor de identidade para este e-mail, com state, nonce e desafio PKCE novos. Só as origens do Console podem começar (403 no caso contrário): a resposta do provedor volta para essa origem.',
  'control:res:post /v1/auth/sso/start:200': 'O endereço do provedor de identidade, para onde o navegador vai.',
  'control:op:get /v1/auth/sso/oidc/callback':
    'A URI de redirecionamento do OIDC. Responde 303 para o Console com um código de login de uso único no fragmento do endereço, ou com o motivo de uma recusa; um teste chega numa página curta em vez disso.',
  'control:res:get /v1/auth/sso/oidc/callback:200':
    'Para um login de teste, uma página curta que diz que acabou. Um login de verdade responde 303 para o Console, com `#code=` ou `#error=` no fragmento.',
  'control:op:get /v1/auth/sso/saml/{tenant_id}/metadata':
    'Os metadados SAML do provedor de serviço: entity ID, assertion consumer service e formato do NameID.',
  'control:res:get /v1/auth/sso/saml/{tenant_id}/metadata:200': 'Os metadados SAML do provedor de serviço (application/samlmetadata+xml).',
  'control:op:post /v1/auth/sso/saml/{tenant_id}/acs':
    'O assertion consumer service (binding HTTP-POST): `SAMLResponse` e `RelayState` como formulário. A asserção precisa vir assinada pelo certificado configurado e responder ao pedido que a Niadra mandou.',
  'control:res:post /v1/auth/sso/saml/{tenant_id}/acs:200':
    'Para um login de teste, uma página curta que diz que acabou. Um login de verdade responde 303 para o Console, com `#code=` ou `#error=` no fragmento.',
  'control:op:post /v1/auth/sso/exchange':
    'A sessão para um código de login de uso único e o valor que o Console guardou para a aba. Quando o tenant pede o segundo fator da Niadra, as respostas são as do login com senha: 401 "second factor enrollment required" (então POST /v1/auth/sso/second-factor) ou "invalid second factor"; o código continua valendo por cinco códigos errados e dez minutos.',
  'control:res:post /v1/auth/sso/exchange:200': 'O token, como no login com senha.',
  'control:op:post /v1/auth/sso/second-factor':
    'A configuração do autenticador de quem entrou pelo SSO, quando o tenant pede o segundo fator da Niadra: o código de login e o valor guardado fazem o papel da senha.',
  'control:res:post /v1/auth/sso/second-factor:200': 'A chave para configurar o aplicativo autenticador.',
  'control:op:get /v1/sso': 'O SSO do tenant e o que colar no provedor de identidade. Exige o papel admin.',
  'control:res:get /v1/sso:200': 'A conexão, ou nulo, e o que colar no provedor de identidade.',
  'control:op:put /v1/sso':
    'Cria ou troca a conexão do tenant, OIDC ou SAML. Um emissor OIDC é conferido pelo documento de descoberta dele; um certificado SAML precisa ser válido. Um domínio que outro tenant usa responde 409. Exige o papel admin.',
  'control:res:put /v1/sso:200': 'A conexão como ficou guardada, e o que colar no provedor de identidade.',
  'control:op:delete /v1/sso':
    'Remove a conexão do tenant. Quem ela criou mantém os papéis e só entra com senha depois de um admin mandar um convite.',
  'control:res:delete /v1/sso:204': 'A conexão foi removida.',
  'control:op:post /v1/sso/test':
    'Um login de teste pelo provedor: abra `authorize_url` em outra aba e entre; depois, GET /v1/sso/tests/{test_id} diz o que um login de verdade faria (e-mail, grupos, papéis). Ninguém é criado e nenhum papel muda. Funciona com a conexão ainda desligada.',
  'control:res:post /v1/sso/test:201': 'O endereço do login de teste.',
  'control:op:get /v1/sso/tests/{test_id}':
    'O resultado de um login de teste; `pending` até o provedor responder (dez minutos no máximo).',
  'control:res:get /v1/sso/tests/{test_id}:200': 'O resultado do teste, ou pendente.',

  'control:op:get /v1/roles':
    'Os seis papéis do Console e o que cada um cobre: admin, security, integration, review, analysis e vendor. Os papéis se combinam.',
  'control:res:get /v1/roles:200': 'Os papéis.',

  // Tenant and projects
  'control:op:post /v1/tenants':
    'Cria a empresa cliente. Usada pela operação da Niadra quando um contrato começa; o código do cliente nunca chama esta rota.',
  'control:res:post /v1/tenants:201': 'O tenant.',
  'control:op:get /v1/tenants/{tenant_id}': 'O tenant da pessoa autenticada. Qualquer outro id de tenant responde 404.',
  'control:res:get /v1/tenants/{tenant_id}:200': 'O tenant.',
  'control:op:get /v1/projects': 'Os projetos do tenant, cada um com os espaços de sandbox e de produção.',
  'control:res:get /v1/projects:200': 'Os projetos.',
  'control:op:post /v1/projects':
    'Cria um projeto na região informada, uma das regiões do seu contrato. Ele nasce com dois espaços, sandbox e produção, que nunca compartilham dados. O nome vira o slug do endereço de cada espaço, então os nomes que a borda já serve (`mtls`, `api`, `www`, `control`, `cells`, `grafana`) são recusados. Exige o papel admin.',
  'control:res:post /v1/projects:201': 'O projeto e os dois espaços dele.',
  'control:op:get /v1/spaces/{space_id}':
    'Um espaço: projeto, ambiente, região, slug e a célula que o atende. O endereço do espaço é `https://<slug>.<region>.api.niadra.com`.',
  'control:res:get /v1/spaces/{space_id}:200': 'O espaço.',
  'control:op:delete /v1/spaces/{space_id}':
    'Apaga um espaço para sempre. As chaves dele param de valer na hora; a célula que o atende apaga o banco de dados do espaço, destrói as chaves de dados dele, apaga os objetos guardados, as cópias noturnas, os segredos e as chaves de cache, e devolve um comprovante de apagamento, que `GET /v1/spaces/{space_id}/deletion-receipt` mostra. A resposta vem antes de a célula terminar, com `status` `deleting`; pedir de novo devolve o mesmo estado. Exige o papel admin.',
  'control:res:delete /v1/spaces/{space_id}:202': 'O espaço apagado, com `status` `deleting` até a célula terminar.',
  'control:op:get /v1/spaces/{space_id}/deletion-receipt':
    'Um espaço apagado e o que a célula dele apagou: `status` fica `deleting` até a célula informar, depois `deleted`, com o comprovante e o SHA-256 dele. Um espaço que não foi apagado responde 404. Exige o papel admin ou security.',
  'control:res:get /v1/spaces/{space_id}/deletion-receipt:200': 'O espaço apagado, com o comprovante depois que a célula informou.',

  // Sources and keys
  'control:op:get /v1/sources':
    'Todo agente, fornecedor, canal ou sistema que fala com um espaço é uma fonte, com chaves, finalidades e classe de audiência próprias. Uma pessoa com o papel vendor vê só as fontes ligadas a ela.',
  'control:res:get /v1/sources:200': 'As fontes do espaço. Uma pessoa com o papel vendor vê só as dela.',
  'control:op:post /v1/sources':
    'Cria uma fonte num espaço. O acesso à memória é negado por padrão e liberado por finalidade na política; o teto de verificação limita o nível que a fonte pode declarar. Exige o papel admin.',
  'control:res:post /v1/sources:201': 'A fonte.',
  'control:op:get /v1/sources/{source_id}/keys':
    'As chaves de uma fonte, com escopos, estado e datas. O segredo nunca volta depois da criação. Exige o papel admin ou security.',
  'control:res:get /v1/sources/{source_id}/keys:200': 'As chaves.',
  'control:op:post /v1/sources/{source_id}/keys':
    'Emite uma chave para a fonte. Sem `scopes`, os escopos seguem a audiência da fonte. O segredo volta uma vez só; a Niadra guarda apenas dois hashes dele. Exige o papel admin.',
  'control:res:post /v1/sources/{source_id}/keys:201': 'A chave e o segredo dela, mostrado só desta vez.',
  'control:op:post /v1/sources/{source_id}/keys/{key_id}/rotate':
    'Emite uma chave nova com os mesmos escopos. A chave antiga continua valendo por `grace_seconds` (24 horas, por padrão) e depois expira; com 0, é revogada na hora. Exige o papel admin.',
  'control:res:post /v1/sources/{source_id}/keys/{key_id}/rotate:201': 'A chave nova e o segredo dela, mostrado só desta vez.',
  'control:op:post /v1/sources/{source_id}/keys/{key_id}/revoke':
    'Corta uma chave na hora. A revogação entra no feed assinado que as células aplicam em segundos, e o SDK descarta o contexto guardado para aquela chave no próximo 401 ou 403. Exige o papel admin ou security.',
  'control:res:post /v1/sources/{source_id}/keys/{key_id}/revoke:200': 'A revogação, com a posição dela no feed de revogações.',
  'control:op:post /v1/sources/{source_id}/revoke':
    'Corta um fornecedor ou sistema de uma vez: toda chave ativa da fonte é revogada, e a fonte sai da configuração enviada às células. As células aplicam o corte em segundos. Exige o papel admin ou security.',
  'control:res:post /v1/sources/{source_id}/revoke:200': 'A revogação da fonte.',
  'control:op:get /v1/secret-refs':
    'Onde cada segredo de um espaço mora no cofre da célula, com a versão e quem fez a última rotação. Os valores nunca passam pelo plano de controle.',
  'control:res:get /v1/secret-refs:200': 'As referências de segredo.',
  'control:op:post /v1/secret-refs':
    'Registra a referência de um segredo que o Console gravou direto no cofre da célula, como o segredo de assinatura de um webhook. Só metadados passam por aqui. Exige o papel admin, integration ou security.',
  'control:res:post /v1/secret-refs:201': 'A referência.',

  // Configuration
  'control:op:get /v1/config/types':
    'Os tipos de configuração que um diff pode mudar (policies, schemas, mappings, views, trait-rules, trigger-rules, webhook-endpoints, export-schedules, review-sampling, experiments, settings, extraction-guidance, as palavras da empresa sobre o que a extração deve captar e deixar de fora, e ingest-masking, o que a ingestão retém antes de gravar: cartões, códigos de segurança, senhas e segredos, só o papel security), com os papéis que podem escrever e aprovar cada um, além do admin.',
  'control:res:get /v1/config/types:200': 'Os tipos de configuração.',
  'control:op:get /v1/config/{type}':
    'O documento em vigor de um tipo de configuração num espaço, com a versão. Versão 0 quer dizer que o tipo ainda está no padrão.',
  'control:res:get /v1/config/{type}:200': 'O documento e a versão dele.',
  'control:op:get /v1/config/diffs': 'Os diffs de um espaço, do mais novo para o mais antigo, com filtro opcional por estado.',
  'control:res:get /v1/config/diffs:200': 'Os diffs.',
  'control:op:post /v1/config/observed':
    'O que uma célula inferiu de um documento a partir do tráfego do espaço, registrado como diff aplicado por `niadra:observed`; não muda nenhuma declaração. Rota da célula, não de integração.',
  'control:op:post /v1/config/diffs':
    'A configuração nunca é escrita no lugar: políticas, views, mapeamentos, regras de padrão e de gatilho, endpoints de webhook, agendamentos de exportação e experimentos mudam por diffs versionados. O diff leva o documento novo inteiro e espera aprovação.',
  'control:res:post /v1/config/diffs:201': 'O diff, esperando aprovação.',
  'control:op:post /v1/config/diffs/{diff_id}/approve':
    'Aplica um diff pendente ou proposto e cria uma versão nova da configuração, que as células recebem pelo snapshot assinado. Quem escreveu o diff não pode aprová-lo, a não ser que o tenant tenha um admin só, e um diff escrito sobre um documento desatualizado é recusado.',
  'control:res:post /v1/config/diffs/{diff_id}/approve:200': 'O diff aplicado, com a versão que ele criou.',
  'control:op:post /v1/config/diffs/{diff_id}/reject':
    'Recusa um diff pendente ou proposto. Nada muda no espaço. Exige um papel que possa escrever aquele tipo.',
  'control:res:post /v1/config/diffs/{diff_id}/reject:200': 'O diff recusado.',
  'control:op:post /v1/config/diffs/{diff_id}/rollback':
    'Volta o documento ao que era antes de um diff aplicado. A volta é registrada como um diff novo, com `rollback_of`, e vale na hora, porque restaura um estado que já tinha sido aprovado. É recusada quando um diff posterior mudou o mesmo documento.',
  'control:res:post /v1/config/diffs/{diff_id}/rollback:200': 'O diff de volta, já aplicado.',
  'control:op:get /v1/config/history': 'Cada versão aplicada da configuração de um espaço, com filtro opcional por tipo.',
  'control:res:get /v1/config/history:200': 'As versões, da mais nova para a mais antiga.',
  'control:op:post /v1/config/proposals':
    'Onde uma célula entrega o diff proposto pelo assistente de configuração. O diff chega com o estado `proposed` e espera uma pessoa aprovar. Só configuração passa por aqui: amostras e valores de cliente ficam na célula. Usada pelas células da Niadra, não pelo seu código.',
  'control:res:post /v1/config/proposals:201': 'O diff proposto.',

  // Usage
  'control:op:get /v1/usage':
    'O uso por espaço, hora e métrica, como sessões cobradas e eventos recebidos, com as cotas em vigor, dos espaços que o papel de análise, segurança ou admin de quem chama cobre: todos, para um papel no tenant inteiro; um, para um papel num espaço só. A cada 15 minutos, cada célula manda o uso por hora dos espaços dela nas últimas 48 horas, somado sobre as fontes; uma hora mandada de novo substitui a contagem anterior, então a hora corrente pode estar incompleta.',
  'control:res:get /v1/usage:200': 'O uso por espaço, período e métrica, com as cotas do tenant.',

  // Platform operations
  'control:op:get /v1/cells': 'As células registradas, com região, endereço, capacidade e estado. Usada pelas células e pela operação da Niadra.',
  'control:res:get /v1/cells:200': 'As células.',
  'control:op:post /v1/cells': 'Registra uma célula nova numa região. Usada pela operação da Niadra.',
  'control:res:post /v1/cells:201': 'A célula.',
  'control:op:get /v1/spaces/{space_id}/placement':
    'Qual célula atende um espaço, e a mudança em andamento, quando houver. Durante a mudança, a célula antiga responde 421 e o SDK tenta de novo na hora, no mesmo endereço: o endereço do espaço nunca muda.',
  'control:res:get /v1/spaces/{space_id}/placement:200': 'A posição do espaço.',
  'control:op:post /v1/spaces/{space_id}/placement':
    'Começa a mudança de um espaço para outra célula: as células copiam os dados e gravam nos dois lados até a mudança terminar. Usada pela operação da Niadra.',
  'control:res:post /v1/spaces/{space_id}/placement:202': 'A mudança, copiando.',
  'control:op:post /v1/spaces/{space_id}/placement/complete':
    'Conclui uma mudança: o endereço do espaço passa a apontar para a célula nova. Usada pela operação da Niadra.',
  'control:res:post /v1/spaces/{space_id}/placement/complete:200': 'A mudança concluída.',
  'control:op:put /v1/quotas':
    'Define um limite de uso para um tenant, por métrica e período. Usada pela operação da Niadra. Uma cota chamada `llm_daily_micros:<finalidade>` com período `day` define o teto diário de gasto de IA do contrato para aquela finalidade (`extraction` ou `assistant`) em todos os espaços do tenant, em milionésimos de dólar: gravá-la republica as células do tenant, e cada espaço passa a usá-la como teto, que `GET /v1/usage/budget` mostra no espaço. As outras cotas ficam registradas e voltam junto com o uso; o teto de gasto de IA é a que as células aplicam.',
  'control:res:put /v1/quotas:200': 'A cota em vigor.',
  'control:res:get /v1/snapshot/{cell_id}:200': 'O snapshot assinado.',
  'control:op:get /v1/revocations':
    'O feed assinado de chaves e fontes revogadas de uma célula, a partir de um número de sequência. As células consultam o feed e cortam o acesso em segundos. Usada pelas células da Niadra.',
  'control:res:get /v1/revocations:200': 'O feed assinado.',
  'control:op:post /v1/usage': 'As células mandam os agregados de uso para a cobrança. Usada pelas células da Niadra.',
  'control:res:post /v1/usage:202': 'Quantos agregados foram aceitos.',
  'control:op:post /v1/spaces/{space_id}/deletion-receipt':
    'Onde uma célula informa o que apagou de um espaço apagado, com o SHA-256 do JSON canônico do comprovante. O mesmo comprovante enviado de novo é aceito; outro comprovante para o mesmo espaço responde 409, e um cujo hash não bate responde 422.',
  'control:res:post /v1/spaces/{space_id}/deletion-receipt:200': 'O comprovante guardado.',

  // Parameters
  'control:param:get /v1/sources:space_id': 'O espaço cujas fontes você quer.',
  'control:param:get /v1/secret-refs:space_id': 'O espaço cujas referências de segredo você quer.',
  'control:param:get /v1/config/{type}:type': 'Um tipo de configuração de `GET /v1/config/types`, como `policies` ou `trigger-rules`.',
  'control:param:get /v1/config/{type}:space_id': 'O espaço cuja configuração você quer.',
  'control:param:get /v1/config/diffs:space_id': 'O espaço cujos diffs você quer.',
  'control:param:get /v1/config/diffs:status': 'Só os diffs neste estado: `pending`, `proposed`, `applied` ou `rejected`.',
  'control:param:get /v1/config/history:space_id': 'O espaço cujo histórico você quer.',
  'control:param:get /v1/config/history:type': 'Só as versões deste tipo de configuração.',
  'control:param:get /v1/config/history:limit': 'Até 200 versões. O padrão é 50.',
  'control:param:get /v1/snapshot/{cell_id}:after':
    'A versão que a célula já tem. Com ela, a chamada espera até existir uma versão mais nova, ou responde 304 no prazo.',
  'control:param:get /v1/snapshot/{cell_id}:wait': 'Quanto tempo esperar por uma versão mais nova, até 30 segundos. O padrão é 25.',
  'control:param:get /v1/revocations:cell_id': 'A célula que pede o feed.',
  'control:param:get /v1/revocations:after': 'O último número de sequência que a célula aplicou. Passe o `seq` da resposta anterior.',
  'control:param:get /v1/usage:since': 'Início do período, incluído.',
  'control:param:get /v1/usage:until': 'Fim do período, excluído.',

  // Fields
  'control:LoginRequest.totp': 'O código atual do aplicativo autenticador. Obrigatório quando a pessoa cadastrou o segundo fator.',
  'control:LoginRequest.recovery_code':
    'Um dos códigos de recuperação da pessoa, no lugar de `totp`, quando o aplicativo autenticador se perdeu. Maiúsculas, espaços e hífens não importam. Cada código entra uma vez só.',
  'control:LoginRequest.space_id': 'O espaço que o token abre. Por padrão, o primeiro espaço a que a pessoa tem acesso.',
  'control:TokenRequest.space_id': 'Troca o token novo para este espaço. Por padrão, o espaço do token atual.',
  'control:TokenOut.expires_in': 'Segundos até o token vencer.',
  'control:TokenOut.recovery_codes':
    'Mostrados uma vez só: os dez códigos de recuperação, quando este login confirmou o autenticador. Nulo nos outros casos.',
  'control:TokenOut.recovery_codes_left': 'Quantos códigos de recuperação restam, quando este login usou um. Nulo nos outros casos.',
  'control:PasswordRequest.password': 'A senha da pessoa mais uma vez: só a sessão não muda o segundo fator.',
  'control:SecondFactorConfirmRequest.totp': 'O código atual do aplicativo autenticador novo.',
  'control:SecondFactorStatusOut.confirmed_at': 'Quando o autenticador em uso foi confirmado; nulo enquanto não há um.',
  'control:SecondFactorStatusOut.recovery_codes_left': 'Códigos de recuperação ainda não usados.',
  'control:RecoveryCodesOut.recovery_codes':
    'Dez códigos de uso único, `XXXXX-XXXXX`. Mostrados uma vez só; a Niadra guarda apenas um hash com chave (HMAC-SHA256) de cada um, e os códigos anteriores da pessoa param de valer.',
  'control:SecondFactorResetRequest.reason': 'O motivo, guardado junto da redefinição.',
  'control:SecondFactorResetOut.reset_id': 'O id desta redefinição.',
  'control:SecondFactorResetOut.user_id': 'A pessoa cujo segundo fator foi redefinido.',
  'control:SecondFactorResetOut.reset_by': 'O admin que redefiniu.',
  'control:SecondFactorResetOut.reason': 'O motivo, como o admin escreveu.',
  'control:SecondFactorResetOut.at': 'Quando a redefinição aconteceu.',
  'control:BindingIn.space_id': 'O papel vale só neste espaço. Vazio quer dizer todos os espaços do tenant.',
  'control:BindingIn.source_ids': 'No papel vendor: as fontes que esta pessoa pode ver.',
  'control:UserCreate.with_totp':
    'Só com `password`: cria o segredo do autenticador agora e o devolve em `totp_uri`. Sem senha, a pessoa configura o autenticador no primeiro login.',
  'control:UserCreate.password':
    'Opcional. Sem ela, a pessoa recebe um convite: um link de uso único para escolher a senha, e depois o autenticador no primeiro login. Uma pessoa cujo domínio de e-mail é do SSO do tenant não recebe convite e entra pelo provedor de identidade.',
  'control:SourceCreate.audience':
    'O que a fonte pode ler, pela classe de audiência: agente que fala com o cliente, agente interno, mesa humana e outras.',
  'control:SourceCreate.purposes': 'As finalidades pelas quais a política libera o acesso, como `customer_service` ou `billing`.',
  'control:SourceCreate.verification_ceiling':
    'O nível de verificação mais alto que esta fonte pode declarar, V2 por padrão. Uma fonte de agente de atendimento pode ir até V3, o nível do OTP que ela mesma roda; V4 só vale para `human` e `internal_agent`, e `no_customer` só para `internal_agent`. Um teto acima do que a audiência permite é recusado com 422 `verification_not_allowed`.',
  'control:SourceCreate.confirms_before_acting':
    'O agente confirma valores antes de agir, então a medição não conta essas confirmações como pergunta repetida.',
  'control:SourceCreate.navigation_enabled': 'Se a fonte pode usar as ferramentas de navegação do histórico.',
  'control:SourceCreate.trusted_action_ops':
    'Operações em que as ações declaradas por esta fonte podem fechar pendências antes de o sistema de registro confirmar.',
  'control:SourceCreate.critical': 'As sessões desta fonte ficam fora dos grupos de controle e sempre recebem a memória.',
  'control:KeyRotate.grace_seconds': 'Por quanto tempo a chave antiga continua valendo. O padrão é 24 horas; 0 revoga na hora.',
  'control:RevokeRequest.reason': 'O motivo, registrado na trilha de auditoria.',
  'control:DiffCreate.document': 'O documento novo inteiro do tipo, no formato que `GET /v1/config/{type}` devolve.',
  'control:DiffCreate.reason': 'O motivo, registrado junto do diff.',
  'control:DiffOut.base_version':
    'A versão da configuração sobre a qual o diff foi escrito. Um diff escrito sobre um documento desatualizado é recusado na aprovação.',
  'control:DiffOut.applied_version': 'A versão que o diff criou quando foi aplicado.',
  'control:DiffOut.author_label':
    'Quem propôs, para mostrar: o e-mail da pessoa, ou o rótulo de um autor que não é uma pessoa (`assistant@<célula>`, `niadra:observed`). Nulo só para uma pessoa que não existe mais.',
  'control:DiffOut.decided_by_label': 'O e-mail da pessoa que aprovou ou recusou.',
  'control:DiffOut.rollback_of': 'O diff que este desfez.',
  'control:SecretRefIn.ref': 'O caminho do segredo no cofre da célula, como `spaces/<space_id>/webhook/whsec-crm`.',
  'control:SecretRefIn.kind': '`webhook` para segredo de assinatura de webhook, `model` para credencial de modelo.',
  'control:CellCreate.capacity': 'Quantos espaços a célula pode atender.',
  'control:UsageItem.cost_micros': 'Custo em milionésimos de dólar, quando a métrica tem custo.',
  'control:QuotaIn.period': 'O período em que o limite é contado. O padrão é `month`.',
  'control:SpaceDeletionOut.deleted_at': 'Quando um admin apagou o espaço.',
  'control:SpaceDeletionOut.status': '`deleting` até a célula informar o que apagou; depois `deleted`, com o comprovante.',
  'control:SpaceDeletionOut.receipt': 'O que a célula apagou; nulo enquanto `status` é `deleting`.',
  'control:DeletionReceiptOut':
    'O que a célula apagou, como ela informou. As chaves de dados foram destruídas, então qualquer cópia dos dados do espaço que ainda exista (as cópias automáticas do próprio servidor de banco, até `instance_snapshot_expiry`) não pode mais ser lida.',
  'control:DeletionReceiptOut.cell_id': 'A célula que apagou o espaço.',
  'control:DeletionReceiptOut.db_name': 'O banco de dados do espaço, removido.',
  'control:DeletionReceiptOut.dropped_at': 'Quando o banco do espaço foi removido.',
  'control:DeletionReceiptOut.dek_versions_destroyed': 'As versões das chaves de dados do espaço que foram destruídas.',
  'control:DeletionReceiptOut.objects_deleted':
    'Objetos guardados apagados: conteúdo bruto, mídia, importações, exportações, arquivos de eventos e âncoras de auditoria.',
  'control:DeletionReceiptOut.backups_deleted': 'Cópias noturnas do banco do espaço apagadas.',
  'control:DeletionReceiptOut.secrets_deleted': 'Versões de segredo apagadas (credenciais de webhook e de exportação).',
  'control:DeletionReceiptOut.instance_snapshot_expiry':
    'Quando vence a última cópia automática do servidor de banco que ainda pode guardar os dados cifrados do espaço.',
  'control:DeletionReceiptOut.receipt_hash': 'SHA-256 do JSON canônico do comprovante, em hexadecimal.',
  'control:DeletionReceiptOut.received_at': 'Quando o controle guardou o comprovante.',
  'control:DeletionReceiptIn.cell_id': 'A célula que apagou o espaço.',
  'control:DeletionReceiptIn.receipt': 'O comprovante como a célula o redigiu; `receipt_hash` é o SHA-256 do JSON canônico dele.',
  'control:DeletionReceiptIn.receipt_hash':
    'SHA-256 do JSON canônico de `receipt` (chaves em ordem, sem espaços, UTF-8), em hexadecimal.',
}
