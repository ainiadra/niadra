/**
 * The guided walkthroughs of the Console, one per screen and job, written for someone who runs the business and
 * never saw the product. Each step says where to click and names the part of the screen it points at (a
 * `data-tour` anchor), so the player can take the person there and highlight it. Progress is kept per person on
 * the control plane, with a copy in the browser (lib/tutorialProgress.ts).
 */
import type { Role } from '../api/types'
import type { Feature } from './features'
import type { ScreenId } from './screens'

export interface TutorialStep {
  /** What to do, in one or two sentences, with the name of the control to click. */
  text: string
  /** The screen the step happens on; the player opens it. */
  screen: ScreenId
  /** The `data-tour` anchor the player highlights, when the step points at one part of the screen. */
  anchor?: string
  /** `?aba=` of the screen, for a step inside one tab. */
  tab?: string
}

export interface Tutorial {
  id: string
  title: string
  /** What the person will be able to do at the end, in one line. */
  outcome: string
  minutes: number
  steps: TutorialStep[]
  /** The role the job needs; the hub says so instead of hiding the tutorial. */
  roles?: Role[]
  /** The agent feature the job needs; the hub says so when the space left it off. */
  feature?: Feature
  /** The screen the walkthrough is about, when it is not the one most of its steps are on. */
  screen?: ScreenId
}

export const TUTORIALS: Tutorial[] = [
  {
    id: 'tour',
    title: 'Conheça o Console em 5 minutos',
    screen: 'home',
    outcome: 'Saber o que cada grupo do menu faz e onde ver o que a memória sabe, quem leu o quê e quanto cada agente aproveita.',
    minutes: 5,
    steps: [
      { text: 'Este é o Início: o que a memória já está fazendo neste espaço, lido do que ele registra, e os trabalhos do Console. Não há nada a configurar para a memória funcionar.', screen: 'home', anchor: 'home.status' },
      { text: 'Em Clientes, a lista mostra cada cliente que a memória conhece, pelo pseudônimo, do que teve atividade mais recente para o mais antigo. Não precisa saber nenhum dado do cliente para abrir a memória dele.', screen: 'customers', anchor: 'customers.list' },
      { text: 'A memória de um cliente. No alto, o contexto exato que um agente recebe antes de atender, e o que a política retém. Abaixo, nas abas, tudo o que a memória guarda: fatos, pendências, linha do tempo, padrões, objetos.', screen: 'profile', anchor: 'profile.context' },
      { text: 'Identidade é como a Niadra reconhece o mesmo cliente no WhatsApp, na voz, no app e no CRM. Cada ligação entre dois identificadores tem método e evidência, e pode ser desfeita.', screen: 'identity', anchor: 'identity.tabs' },
      { text: 'Comprovantes é a prova: cada leitura da memória, quem leu, o que recebeu e o que ficou retido. Meses depois, é aqui que a empresa responde por que um agente disse o que disse.', screen: 'receipts', anchor: 'receipts.list' },
      { text: 'Uso e aproveitamento: quanto o espaço consumiu e, por agente e fornecedor, se o agente usou o que recebeu, o que perguntou de novo e o que contradisse.', screen: 'usage', anchor: 'usage.context' },
      { text: 'Avisos: a Niadra avisa o seu sistema quando uma regra acontece na memória, como uma promessa vencida. Quem age é o seu agente ou o seu sistema. Os disparos ficam aqui.', screen: 'alerts', anchor: 'alerts.tabs' },
      { text: 'Em Agentes e chaves nasce cada agente: a fonte, a chave e o que ela pode ler. É o primeiro passo da lista do Início. O botão de ajuda, no menu, reabre o tutorial da tela em que você estiver.', screen: 'agents', anchor: 'agents.new' },
    ],
  },
  {
    id: 'customer-memory',
    title: 'Ver o que um agente sabe de um cliente',
    outcome: 'Abrir a memória de um cliente e ver o texto exato que um agente recebe antes de atender, com o que fica retido e por quê.',
    minutes: 3,
    steps: [
      { text: 'Em Clientes, role até Clientes deste espaço. Cada linha é um cliente, com o pseudônimo, os identificadores mascarados e a última atividade. Se souber o telefone ou o e-mail, a busca acima encontra o cliente por esse dado exato.', screen: 'customers', anchor: 'customers.list' },
      { text: 'Clique em Abrir memória numa linha. A tela do cliente abre no pseudônimo dele, o código p_… que substitui nome e telefone nas telas.', screen: 'customers', anchor: 'customers.list' },
      { text: 'No alto, em O contexto que o agente recebe, escolha o agente, a visão (voz, chat, tarefa) e o nível de verificação, e clique em Ver o que o agente recebe.', screen: 'profile', anchor: 'profile.context' },
      { text: 'O texto que aparece é o contexto exato. Ao lado, Retidos lista o que existe na memória e não foi entregue, com o motivo: política, nível de verificação ou não coube. Nada é entregue a ninguém nesta simulação, e ela deixa um comprovante em seu nome.', screen: 'profile', anchor: 'profile.context' },
      { text: 'As abas abaixo mostram o que a memória guarda: Fatos, Pendências e promessas, Linha do tempo, Padrões, Objetos e ações. Em cada item, a linha em letra miúda diz quem disse, em qual canal e quando.', screen: 'profile', anchor: 'profile.tabs' },
    ],
  },
  {
    id: 'customer-relations',
    title: 'Ver quem está ligado a um cliente',
    outcome: 'Ler, num desenho, os identificadores, a empresa, os pedidos e chamados de um cliente, e os agentes e sistemas que o atenderam ou agiram por ele.',
    minutes: 3,
    steps: [
      { text: 'Em Clientes, abra a memória de um cliente pela lista, com Abrir memória. O mapa de relações fica dentro da tela dele.', screen: 'customers', anchor: 'customers.list' },
      { text: 'Clique na aba Relações. O cliente aparece no meio, com um anel lima, e cada item ligado a ele em volta: círculo pequeno é um identificador, quadrado lima é a empresa, retângulo é um pedido ou chamado, hexágono azul é um agente, retângulo azul de contorno é um sistema.', screen: 'profile', anchor: 'profile.tabs' },
      { text: 'Clique num item para acendê-lo: ele se ilumina, um sinal percorre as linhas dele até cada vizinho e o resto do mapa recua. O cartão abre com o tipo, os dados em palavras e as relações dele como frases. Passe o ponteiro por uma linha e leia a frase da relação: quem identifica quem, quem atendeu, quem registrou. A linha tracejada é uma sugestão de identidade que ninguém confirmou ainda.', screen: 'profile', anchor: 'profile.relations' },
      { text: 'Clique em Mostrar mais um nível para trazer as relações das relações: as outras pessoas da mesma empresa, a matriz dela, os outros pedidos que o mesmo sistema registrou. Os itens novos saem de dentro do cliente.', screen: 'profile', anchor: 'profile.relations' },
      { text: 'Na legenda, clique num tipo para escondê-lo e enxergar o resto. Ver como lista mostra os mesmos itens numa tabela, com as mesmas relações, e Abrir leva à tela de cada um.', screen: 'profile', anchor: 'profile.relations' },
    ],
  },
  {
    id: 'relation-map',
    title: 'Explorar o mapa de relações do espaço',
    outcome: 'Partir dos itens mais ligados do espaço, trazer as relações de um cliente para o mapa e chegar à memória dele.',
    minutes: 3,
    steps: [
      { text: 'Em Mapa de relações, o espaço abre pelos itens mais ligados: os clientes com mais identificadores e objetos, as empresas, os agentes e sistemas por onde mais eventos entraram. A legenda diz quantos itens de cada tipo há no mapa.', screen: 'relations', anchor: 'relations.map' },
      { text: 'As empresas são os quadrados lima, cada uma com as pessoas dela em volta, numa região clara do mapa; os agentes e sistemas, quando aparecem, são azuis. Clique num cliente (círculo claro) ou numa empresa para acendê-lo: as relações dele se iluminam e o cartão abre. Clique duas vezes, ou use Trazer as relações dele no cartão, para trazer o que está ligado a ele, saindo de dentro dele. Clique em outro item para seguir o fio.', screen: 'relations', anchor: 'relations.map' },
      { text: 'Use Procurar um item pelo nome para achar um pseudônimo, um pedido ou um agente já no mapa: o item ganha um anel e um clique no resultado leva até ele. O mapa só tem o que já foi trazido.', screen: 'relations', anchor: 'relations.map' },
      { text: 'Centralizar devolve o mapa inteiro à tela; mais e menos aproximam e afastam; Refazer o desenho arruma os itens de novo depois de você arrastá-los. No teclado, as setas andam entre os itens e Enter traz as relações de um cliente.', screen: 'relations', anchor: 'relations.map' },
      { text: 'No cartão, Abrir leva à tela do item: a memória de um cliente, a fonte de um agente ou sistema, os objetos de um cliente. O aviso acima do mapa diz quando ele mostra só os itens mais próximos e quantos o seu papel não vê.', screen: 'relations', anchor: 'relations.map' },
    ],
  },
  {
    id: 'identity',
    title: 'Conferir que é o mesmo cliente em todos os canais',
    outcome: 'Ler as ligações entre identificadores, aceitar ou dispensar uma sugestão, e saber quando unir, separar ou bloquear.',
    minutes: 4,
    steps: [
      { text: 'Em Identidade, a aba Asserções lista cada ligação entre dois identificadores do mesmo cliente: o telefone e o e-mail, o ID do app e o ID no CRM. Cada uma diz o método (uma identificação explícita, um login, um sistema) e o estado.', screen: 'identity', anchor: 'identity.assertions', tab: 'assertions' },
      { text: 'Em Sugestões ficam os pares que a Niadra acha que são a mesma pessoa, com a pontuação. Nada se une sozinho: uma pessoa aceita ou dispensa. Leia os sinais em que o par concorda antes de aceitar.', screen: 'identity', anchor: 'identity.suggestions', tab: 'suggestions' },
      { text: 'Unir e separar: dois perfis viram um, ou um perfil se divide. Os fatos e as conversas presos a cada identificador vão junto. Use quando um cliente aparece duas vezes, ou quando duas pessoas foram unidas por engano.', screen: 'identity', anchor: 'identity.merge', tab: 'merge' },
      { text: 'Bloquear identificador: o telefone da loja ou o e-mail genérico da empresa deixam de ligar pessoas entre si. É o jeito de impedir que um número compartilhado misture clientes.', screen: 'identity', anchor: 'identity.block', tab: 'block' },
      { text: 'Vínculos liga pessoas a contas e parceiros por papel: o comprador de uma empresa cliente, o motorista de uma transportadora. Matriz e subsidiárias registra as organizações sob a matriz delas.', screen: 'identity', anchor: 'identity.links', tab: 'links' },
    ],
  },
  {
    id: 'patterns',
    title: 'Criar uma regra de padrão e testar antes de ligar',
    outcome: 'Ler as regras em uso, testar uma regra nova contra os últimos 90 dias e mandar para aprovação.',
    minutes: 4,
    roles: ['integration', 'security'],
    steps: [
      { text: 'Em Padrões, Regras em uso lista as regras ligadas neste espaço, com os parâmetros de cada uma. Um padrão é um sinal que se repete, como a terceira reclamação sobre entrega em 90 dias. Ele nunca é uma nota nem um rótulo.', screen: 'patterns', anchor: 'patterns.rules' },
      { text: 'Clique em Nova regra. O editor abre com o catálogo: escolha o padrão, como Reclamação recorrente, e ajuste os parâmetros, como quantas vezes e em quantos dias.', screen: 'patterns', anchor: 'patterns.editor' },
      { text: 'Clique em Testar. A regra roda contra os últimos 90 dias e diz quantos clientes teriam o padrão, com exemplos. Nada é gravado no teste.', screen: 'patterns', anchor: 'patterns.editor' },
      { text: 'Escreva o motivo e envie para aprovação. A regra vira um diff que outra pessoa aprova em Configuração; só depois ela passa a derivar padrões.', screen: 'patterns', anchor: 'patterns.editor' },
      { text: 'Padrões de um cliente: aqui o seu time retira um padrão errado ou tira o cliente dos padrões quando ele se opuser. A retirada deixa uma lápide, e o padrão só volta com evidência nova.', screen: 'patterns', anchor: 'patterns.customer' },
    ],
  },
  {
    id: 'agent-memory',
    title: 'Ver o que os agentes aprenderam sobre o trabalho',
    outcome: 'Ler as notas que entram no prompt de cada agente, aprovar ou recusar uma proposta e saber quem pode gravar.',
    minutes: 3,
    steps: [
      { text: 'Em Memória dos agentes, a aba Notas lista o que cada agente aprendeu sobre o próprio trabalho: procedimentos que funcionaram, como as ferramentas se comportam, o que dá errado. Nada aqui é sobre clientes: uma nota com nome, telefone ou documento é recusada.', screen: 'agentMemory', anchor: 'agentMemory.notes', tab: 'notes' },
      { text: 'Cada nota entra no prompt do agente dela, antes do contexto do cliente. Editar gera uma versão nova; a anterior fica aposentada e guardada. Versões mostra o histórico de cada nota.', screen: 'agentMemory', anchor: 'agentMemory.notes', tab: 'notes' },
      { text: 'Em Propostas ficam as notas que um agente ou uma destilação propôs e esperam uma pessoa. Leia, edite se precisar, e aprove ou recuse. Nada entra no prompt sem essa decisão quando o espaço só aceita notas aprovadas.', screen: 'agentMemory', anchor: 'agentMemory.proposals', tab: 'proposals' },
      { text: 'Ligar e limites diz se a memória dos agentes está ligada, quem grava (o agente, uma pessoa ou os dois) e quantas notas cada agente pode ter. Mudar é uma proposta que outra pessoa aprova.', screen: 'agentMemory', anchor: 'agentMemory.settings', tab: 'settings' },
    ],
  },
  {
    id: 'why-answer',
    title: 'Entender por que o agente respondeu aquilo',
    outcome: 'Partir de uma resposta de um agente e chegar ao comprovante da leitura, aos itens que ele recebeu e à origem de cada um.',
    minutes: 4,
    steps: [
      { text: 'Em Comprovantes, use os filtros: o agente, o período e, se tiver, o ID da conversa. O ID da conversa vai no corpo do pedido, nunca no endereço da página.', screen: 'receipts', anchor: 'receipts.filters' },
      { text: 'Na lista, a coluna Retidos diz quantos itens existiam e não foram entregues. Clique na linha da leitura que precede a resposta do agente.', screen: 'receipts', anchor: 'receipts.list' },
      { text: 'O comprovante aberto mostra quem leu, a finalidade, o nível de verificação e a política em vigor na hora. Em Itens entregues, cada item leva ao evento de onde veio: o canal, a hora, quem falou.', screen: 'receipt', anchor: 'receipt.items' },
      { text: 'Em O que ficou retido e por quê, cada item retido traz o motivo: a política não libera para esta finalidade, o nível de verificação era baixo, ou o item não coube no contexto.', screen: 'receipt', anchor: 'receipt.withheld' },
      { text: 'Com os registros de turno ligados, Turnos dos agentes mostra a rodada inteira: o que o agente leu, chamou, mostrou e afirmou antes de responder.', screen: 'turns', anchor: 'turns.list' },
    ],
  },
  {
    id: 'turns',
    title: 'Reproduzir uma rodada de um agente, passo a passo',
    outcome: 'Abrir um turno e ler, na ordem, o que o agente leu, chamou e afirmou, com a versão em que rodou.',
    minutes: 4,
    feature: 'turns',
    steps: [
      { text: 'Em Turnos dos agentes, a lista traz os turnos guardados: os marcados na captura, os sorteados para a amostra e os promovidos. Todo turno fica 7 dias e abre pelo ID, no campo acima da lista.', screen: 'turns', anchor: 'turns.list' },
      { text: 'Filtre por agente, por marca ou por período. Uma marca é o que chamou a atenção no turno: um erro numa chamada, uma afirmação sem evidência, um retorno negativo da pessoa.', screen: 'turns', anchor: 'turns.filters' },
      { text: 'Clique num turno. A tela abre no resumo e no build fixado: o que precisa ser igual (prompts, modelo, esquemas das ferramentas) para um replay reproduzir esta rodada.', screen: 'turn', anchor: 'turn.read' },
      { text: 'O que chamou lista as chamadas de ferramenta e de modelo, na ordem, com o tempo de cada uma. É aqui que se vê o que o agente consultou antes de responder.', screen: 'turn', anchor: 'turn.calls' },
      { text: 'O que afirmou mostra cada afirmação do agente com o veredito e a evidência no próprio turno: um preço que confere com a ferramenta, uma data sem evidência. Partindo daqui, o seu time corrige o prompt ou o modelo.', screen: 'turn', anchor: 'turn.claims' },
    ],
  },
  {
    id: 'coordination',
    title: 'Saber quem está com o cliente agora',
    outcome: 'Ler a posse de cada cliente, os conflitos que a coordenação resolveu e os orçamentos de contato.',
    minutes: 3,
    feature: 'coordination',
    steps: [
      { text: 'Em Coordenação, Conflitos resolvidos conta o que a Niadra decidiu entre agentes, pessoas e sistemas: clientes disputados, compromissos superados, transbordos sem desfecho. Só contagens: nenhum cliente aparece aqui.', screen: 'coordination', anchor: 'coordination.conflicts' },
      { text: 'Posse agora diz quantas afirmações de posse valem neste momento, por tipo, nível e origem. O mais restritivo vence: um atendente humano com o cliente barra o agente de vendas.', screen: 'coordination', anchor: 'coordination.ownership' },
      { text: 'Efeitos é o que saiu uma vez só pela chave que garante uma vez só: a mesma mensagem nunca sai duas vezes. Um efeito esperando decisão nunca é reenviado sozinho.', screen: 'coordination', anchor: 'coordination.effects' },
      { text: 'Orçamentos de contato: quantos clientes chegaram ao limite de contatos de cada finalidade, somando todos os agentes e fornecedores. A Niadra sabe quem está com o cliente; quem envia e quem barra é o sistema da empresa.', screen: 'coordination', anchor: 'coordination.budgets' },
    ],
  },
  {
    id: 'usage',
    title: 'Ler o uso e o aproveitamento por agente',
    outcome: 'Saber o que o espaço consumiu, quanto custa, e se cada agente usa o contexto que recebe.',
    minutes: 4,
    steps: [
      { text: 'Em Uso e aproveitamento, escolha o período no alto (7, 30 ou 90 dias). Os dias são contados em UTC, como o servidor conta: o dia de hoje termina às 21:00 no horário de Brasília.', screen: 'usage', anchor: 'usage.sources' },
      { text: 'Uso por agente: conversas e tarefas cobradas, que são a unidade de cobrança, leituras de contexto, consultas ao histórico e eventos recebidos, por agente.', screen: 'usage', anchor: 'usage.sources' },
      { text: 'Cota e custo do espaço: o total por métrica contra a cota da empresa. O extrato do mês sai em CSV. Logo abaixo, o teto diário de gasto com IA e o que o espaço gastou hoje.', screen: 'usage', anchor: 'usage.quota' },
      { text: 'Aproveitamento do contexto: por agente e fornecedor, se o agente usou o que recebeu, o que perguntou de novo e o que contradisse, cada taxa com o intervalo de 95% e o número de casos. É o número que mostra quem usa o que recebe.', screen: 'usage', anchor: 'usage.context' },
      { text: 'Uma conversa, entrega por entrega: cole o ID de uma conversa e veja cada entrega de contexto e o que o agente fez com ela.', screen: 'usage', anchor: 'usage.conversation' },
    ],
  },
  {
    id: 'sales',
    title: 'Ver quanto o agente vendeu',
    outcome: 'Ler os desfechos atribuídos a cada agente, saber o que sustenta cada número e comparar com o BI da empresa.',
    minutes: 3,
    roles: ['analysis'],
    feature: 'measurement',
    steps: [
      { text: 'Em Desfechos e experimentos, escolha o período no alto. Desfechos atribuídos mostra as vendas e outros resultados por agente, com o valor.', screen: 'measure', anchor: 'measure.attribution' },
      { text: 'Cada linha traz o método: Por pedido e Por linha são determinísticos, o pedido trouxe o código que o agente mostrou; Por identidade é provável, a pessoa viu o item e comprou depois. As faixas nunca se somam.', screen: 'measure', anchor: 'measure.attribution' },
      { text: 'Em Diferença contra o arquivo do BI, suba o CSV do seu BI (pedido, linha, valor, moeda). A Niadra compara linha a linha e diz o desvio e as piores diferenças.', screen: 'measure', anchor: 'measure.bi' },
      { text: 'Experimentos: cada braço contra o grupo de controle, com o intervalo e se a diferença já pode ser lida. Custo por turno mostra o que cada agente gastou ao lado do custo com a memória desligada.', screen: 'measure', anchor: 'measure.experiments' },
      { text: 'Em Uso e aproveitamento, a tabela Aproveitamento do contexto diz se cada agente usou o que recebeu. Vender mais com a memória começa por aproveitar o contexto.', screen: 'usage', anchor: 'usage.context' },
    ],
  },
  {
    id: 'review',
    title: 'Julgar uma amostra e ler a concordância',
    outcome: 'Marcar certo, errado ou faltou numa amostra, gravar os vereditos e saber quanto dá para confiar nos números.',
    minutes: 4,
    roles: ['review'],
    steps: [
      { text: 'Em Revisão, Amostras para revisar traz as conversas sorteadas para o seu time. Cada amostra diz por que foi sorteada: a taxa base, uma fonte nova, uma categoria sensível. Você só vê o que o agente daquela finalidade veria.', screen: 'review', anchor: 'review.queue' },
      { text: 'Abra uma amostra. Para cada fato, pendência, padrão e sinal da medição, marque certo, errado ou faltou. Grave os vereditos ao fim: cada um vira um caso de teste do espaço.', screen: 'review', anchor: 'review.queue' },
      { text: 'Concordância: por tipo de item e por sinal, quanto o seu time concorda com o sistema e quanto os revisores concordam entre si, com o intervalo e o número de julgados. É o número que diz quanto confiar nas outras telas.', screen: 'review', anchor: 'review.agreement' },
      { text: 'Casos de teste: cada veredito roda contra a memória e a medição de hoje. Um caso que falha mostra o que mudou desde o julgamento.', screen: 'review', anchor: 'review.cases' },
    ],
  },
  {
    id: 'gaps',
    title: 'Descobrir o que falta na memória',
    outcome: 'Ler as lacunas de conhecimento, saber que sistema poderia gravar cada uma e aprovar uma resposta pronta.',
    minutes: 3,
    roles: ['analysis'],
    steps: [
      { text: 'Em Lacunas e respostas prontas, escolha o período no alto. Lacunas de conhecimento junta, por assunto e categoria, o que os clientes perguntam e nenhum sistema manda para a memória, com quantos clientes e quantas vezes.', screen: 'gaps', anchor: 'gaps.table' },
      { text: 'Cada linha diz quem poderia gravar: o ERP pelo mapeamento, a extração de uma fonte. Nenhuma fonte grava é uma lacuna estrutural, e é a lista do que ligar primeiro em Sistemas.', screen: 'gaps', anchor: 'gaps.table' },
      { text: 'Perguntas recorrentes: as perguntas que o espaço mais recebe e quanto a memória respondeu, em porcentagem. Acima de 90%, a resposta sai pela regra quase sempre.', screen: 'gaps', anchor: 'gaps.recurring' },
      { text: 'Respostas prontas propostas: linhas escritas por regra a partir de uma lacuna, para o seu time aprovar. Nada entra no contexto dos agentes sem essa aprovação. Aceite ou dispense cada uma.', screen: 'gaps', anchor: 'gaps.proposals' },
    ],
  },
  {
    id: 'connect',
    title: 'Ligar um novo canal ou sistema',
    outcome: 'Criar a fonte de um agente ou sistema, emitir a chave dela e conferir que os eventos chegam.',
    minutes: 4,
    roles: ['admin'],
    steps: [
      { text: 'Em Agentes e chaves, clique em Nova fonte. Uma fonte é como um agente, fornecedor ou sistema aparece para a memória.', screen: 'agents', anchor: 'agents.new' },
      { text: 'Dê o nome que o seu time usa, escolha a audiência (quem fala com o cliente, quem trabalha por dentro), o canal e as finalidades. O teto de verificação é o nível mais alto que a fonte pode declarar.', screen: 'agents', anchor: 'agents.new' },
      { text: 'Deixe marcado Emitir a primeira chave agora. O segredo aparece uma vez: guarde no cofre do servidor do agente. Com ele, o SDK já pede contexto e envia eventos.', screen: 'agents', anchor: 'agents.new' },
      { text: 'Em Sistemas, cada fonte tem o endereço de entrada por webhook, para um sistema (CRM, ERP) mandar eventos. Copie e cole no sistema. Sem mapeamento, o evento fica guardado cru: o mapeamento se propõe em Configuração ou pelo assistente.', screen: 'systems', anchor: 'systems.inbound' },
      { text: 'A cobertura nos últimos 7 dias diz quantos eventos a fonte enviou e quantos chegaram. Quando o primeiro evento aparecer aqui, a conexão está feita.', screen: 'systems', anchor: 'systems.coverage' },
    ],
  },
  {
    id: 'keys',
    title: 'Girar, revogar ou cortar o acesso de um agente',
    outcome: 'Abrir uma fonte, emitir ou girar a chave dela, revogar uma chave vazada e cortar o acesso em segundos.',
    minutes: 3,
    roles: ['admin', 'security'],
    steps: [
      { text: 'Em Agentes e chaves, cada linha é uma fonte: o fornecedor, o canal, as finalidades que ela pode ler, a audiência e o teto de verificação. Ativa é uma fonte com acesso; Acesso cortado, uma cujas chaves pararam de valer. Clique na fonte.', screen: 'agents', anchor: 'agents.list' },
      { text: 'Em Chaves, cada chave aparece com os escopos, o estado e a validade. O segredo apareceu uma vez, na emissão: a Niadra guarda só dois hashes dele. Emitir uma chave nova abre o diálogo com os escopos.', screen: 'agent', anchor: 'agent.keys' },
      { text: 'Girar emite uma chave nova e deixa a antiga valer pelo período de troca, para o agente trocar sem parar. Revogar faz uma chave parar de funcionar na hora; as outras chaves da fonte continuam valendo.', screen: 'agent', anchor: 'agent.keys' },
      { text: 'Cortar o acesso agora para todas as chaves da fonte em segundos, em todas as instâncias da região. Use quando uma chave vazou ou um fornecedor saiu. O corte vira permanente na próxima configuração assinada.', screen: 'agent', anchor: 'agent.cut' },
    ],
  },
  {
    id: 'systems',
    title: 'Receber os avisos no seu sistema e no SIEM',
    outcome: 'Criar o endpoint que recebe os avisos, acompanhar as entregas e ligar o fluxo de comprovantes para o SIEM.',
    minutes: 4,
    roles: ['integration', 'security'],
    steps: [
      { text: 'Em Sistemas, cada fonte mostra o endereço de entrada por webhook e os endpoints dela: os endereços do seu sistema que recebem os avisos. Clique em Novo endpoint na fonte que vai receber.', screen: 'systems', anchor: 'systems.inbound' },
      { text: 'Cole a URL do seu sistema e marque os tipos de evento que ele quer receber, como uma regra de aviso que disparou ou uma pendência fechada. A assinatura do endpoint vai para o cofre da região e nunca volta para a tela.', screen: 'systems', anchor: 'systems.inbound' },
      { text: 'Em Avisos, aba Entregas de webhook, cada aviso enviado aparece com o estado: na fila, entregue, falhando ou esgotou as tentativas. Uma entrega que esgotou pode ser enviada de novo à mão.', screen: 'alerts', anchor: 'alerts.deliveries', tab: 'deliveries' },
      { text: 'De volta a Sistemas, Comprovantes no SIEM leva cada comprovante ao sistema de segurança da empresa em segundos, no formato OCSF 1.3 ou HTTP Event Collector, com ids e hashes, nunca o que foi lido.', screen: 'systems', anchor: 'systems.siem' },
    ],
  },
  {
    id: 'alerts',
    title: 'Montar um aviso de promessa vencida',
    outcome: 'Criar uma regra de aviso a partir do catálogo, testar quantos clientes disparariam hoje e mandar para aprovação.',
    minutes: 4,
    roles: ['integration'],
    steps: [
      { text: 'Em Avisos, a aba Disparos mostra cada vez que uma regra ligada aconteceu, do mais recente para o mais antigo, com o cliente pelo pseudônimo ou a fonte. A Niadra avisa; quem age é o seu agente ou o seu sistema.', screen: 'alerts', anchor: 'alerts.firings', tab: 'firings' },
      { text: 'Regras em uso lista as regras ligadas e desligadas, com os parâmetros. Toda mudança é uma proposta que outra pessoa aprova. Clique em Nova regra.', screen: 'alerts', anchor: 'alerts.rules', tab: 'rules' },
      { text: 'Em Criar e testar, escolha a condição Promessa vencida no catálogo, diga quantos dias de atraso e de quem é a promessa, e o endpoint que recebe o aviso.', screen: 'alerts', anchor: 'alerts.editor', tab: 'editor' },
      { text: 'Clique em Testar: o teste diz quantos clientes dos últimos 30 dias disparariam agora, sem enviar nada. Escreva o motivo e envie para aprovação.', screen: 'alerts', anchor: 'alerts.editor', tab: 'editor' },
      { text: 'Entregas de webhook mostra cada aviso enviado ao seu endpoint e o estado dele. Uma entrega que esgotou as tentativas pode ser enviada de novo à mão.', screen: 'alerts', anchor: 'alerts.deliveries', tab: 'deliveries' },
    ],
  },
  {
    id: 'types',
    title: 'Acompanhar os objetos que os sistemas mantêm',
    outcome: 'Ler os tipos de objeto do espaço, a cobertura de cada campo, a deriva em aberto e os tipos que ninguém declarou.',
    minutes: 3,
    roles: ['integration'],
    feature: 'state',
    steps: [
      { text: 'Em Tipos, o Resumo conta os tipos de objeto que os sistemas da empresa mantêm e a memória acompanha: pedidos, propostas, chamados. A Niadra lê os tipos do que os sistemas enviam; declarar é opcional.', screen: 'types', anchor: 'types.summary' },
      { text: 'Campos novos no tráfego: o sistema passou a enviar um campo ou um estado que a declaração não tem. Um campo novo chega aos agentes sem esperar ninguém; um estado novo de um tipo que lista os estados fica guardado. A nota fica aqui.', screen: 'types', anchor: 'types.drift' },
      { text: 'Tipos declarados e derivados: cada tipo com a cobertura de cada campo, medida nos objetos que mudaram por último. Um campo com cobertura baixa é um campo que o sistema quase nunca manda.', screen: 'types', anchor: 'types.list' },
      { text: 'Tipos novos que a Niadra passou a acompanhar: um sistema mostrou objetos de um tipo que ninguém declarou, e a Niadra criou o tipo a partir do que viu. Logo abaixo, os campos retidos por parecerem dado pessoal ou por serem texto livre longo, os únicos que esperam uma declaração.', screen: 'types', anchor: 'types.undeclared' },
    ],
  },
  {
    id: 'approve',
    title: 'Aprovar uma mudança de configuração',
    outcome: 'Ler uma proposta de mudança, entender o motivo e aprová-la ou recusá-la.',
    minutes: 3,
    roles: ['admin', 'security', 'integration'],
    steps: [
      { text: 'Toda mudança no espaço é uma proposta com autor e motivo, chamada diff, e só vale depois que outra pessoa aprova. Em Configuração, abra a aba Diffs.', screen: 'config', anchor: 'config.tabs', tab: 'diffs' },
      { text: 'Deixe o filtro em Aguardando aprovação. Cada proposta diz o documento que muda, quem propôs, quando e o motivo. Leia o motivo antes de tudo: é o que o autor quer que você entenda.', screen: 'config', anchor: 'config.diffs', tab: 'diffs' },
      { text: 'Clique em Aprovar ou Recusar. Ao aprovar, o plano de controle confere a mudança de novo e ela passa a valer na próxima configuração assinada.', screen: 'config', anchor: 'config.diffs', tab: 'diffs' },
      { text: 'A aba Histórico guarda cada versão de cada documento. Se uma mudança aprovada deu errado, Desfazer volta à versão anterior, com motivo, e também passa por aprovação.', screen: 'config', anchor: 'config.history', tab: 'history' },
    ],
  },
  {
    id: 'config',
    title: 'Entender os documentos que governam o espaço',
    outcome: 'Saber o que cada documento de configuração faz, onde ficam os segredos e como pedir uma proposta ao assistente.',
    minutes: 4,
    roles: ['admin', 'security', 'integration'],
    steps: [
      { text: 'Em Configuração, a aba Documentos lista cada documento em vigor, com a versão e quem pode escrever nele: a política de leitura, os mapeamentos, as visões de tarefa, as regras, os endpoints, a exportação, a amostragem. É a tela técnica do Console: as outras telas propõem as mudanças mais comuns por formulário.', screen: 'config', anchor: 'config.documents', tab: 'documents' },
      { text: 'Escolha um documento para ler o conteúdo em vigor. Uma edição aqui vira um diff com motivo, que outra pessoa aprova na aba Diffs.', screen: 'config', anchor: 'config.documents', tab: 'documents' },
      { text: 'Segredos mostra só qual segredo existe, a versão e quem girou: a assinatura dos webhooks, a credencial do modelo de IA. O valor fica no cofre da região e nunca volta para a tela.', screen: 'config', anchor: 'config.secrets', tab: 'secrets' },
      { text: 'Assistente pede uma proposta de documento a partir de amostras de eventos deste espaço, como um mapeamento para um sistema novo. A proposta vira um diff para aprovar; a amostra vai mascarada ao provedor de IA.', screen: 'config', anchor: 'config.assist', tab: 'assist' },
    ],
  },
  {
    id: 'erasure',
    title: 'Atender um pedido de apagamento',
    screen: 'data',
    outcome: 'Encontrar o cliente que pediu para ser apagado, pedir o apagamento e guardar o comprovante.',
    minutes: 3,
    roles: ['security'],
    steps: [
      { text: 'Em Clientes, busque o cliente pelo dado que ele informou no pedido: telefone com o código do país ou e-mail. A busca encontra só por dado exato.', screen: 'customers', anchor: 'customers.search' },
      { text: 'Abra a memória do cliente e copie o ID do perfil, que aparece no endereço da página e na aba Identificadores e vínculos. É esse ID que o apagamento usa.', screen: 'profile', anchor: 'profile.tabs' },
      { text: 'Em Dados, aba Apagamento, escolha Um perfil inteiro, cole o ID, marque que entendeu que o apagamento não se desfaz e clique em Pedir o apagamento. O apagamento segue tudo o que veio desse cliente: eventos, fatos, conversas, objetos e o que já saiu na exportação.', screen: 'data', anchor: 'data.forget', tab: 'forget' },
      { text: 'Em Pedidos de apagamento, acompanhe o pedido até Concluído. A linha diz o que foi apagado por tabela e o comprovante do apagamento, que é o que você guarda para responder ao titular.', screen: 'data', anchor: 'data.requests', tab: 'forget' },
    ],
  },
  {
    id: 'data-export',
    title: 'Entregar o arquivo do titular e a exportação contínua',
    outcome: 'Gerar o arquivo com os dados de um cliente para um pedido de acesso, e acompanhar a cópia contínua da memória no bucket da empresa.',
    minutes: 3,
    roles: ['security'],
    steps: [
      { text: 'Em Dados, aba Arquivo do titular, cole o ID do perfil do cliente que pediu os dados dele. O arquivo traz tudo o que a memória guarda sobre a pessoa, em JSON por tipo de registro, num zip com o hash de cada parte.', screen: 'data', anchor: 'data.package', tab: 'export' },
      { text: 'Clique em Gerar o arquivo. O link vale 24 horas; a geração fica num comprovante em seu nome. É o que você entrega ao titular para atender o direito de acesso.', screen: 'data', anchor: 'data.package', tab: 'export' },
      { text: 'Exportação contínua: cada agendamento escreve uma cópia incremental da memória, em Parquet, no bucket da empresa. A credencial vai direto para o cofre da região.', screen: 'data', anchor: 'data.schedules', tab: 'continuous' },
      { text: 'As execuções mostram o que saiu e quando, e o que falhou. Um agendamento novo é um diff que outra pessoa aprova em Configuração.', screen: 'data', anchor: 'data.schedules', tab: 'continuous' },
    ],
  },
  {
    id: 'who-read',
    title: 'Ver quem acessou o quê',
    outcome: 'Listar as leituras de um agente ou de uma pessoa num período, conferir que a cadeia de comprovantes está íntegra e ver o histórico de acesso de uma pessoa.',
    minutes: 4,
    steps: [
      { text: 'Em Comprovantes, filtre por Agente ou fonte e pelo período. Para ver só o que pessoas fizeram aqui no Console, escolha o tipo Ação no Console.', screen: 'receipts', anchor: 'receipts.filters' },
      { text: 'Cada linha diz quem leu, sobre qual cliente (pelo pseudônimo), o que foi lido e quantos itens ficaram retidos. Leitura negada é uma tentativa que a política recusou: fica registrada também.', screen: 'receipts', anchor: 'receipts.list' },
      { text: 'Em Conferir a cadeia de um dia, escolha o dia e clique em Conferir. A Niadra refaz a corrente de hashes e compara com a raiz gravada no banco e com a cópia fora dele. Cadeia íntegra quer dizer que ninguém alterou um comprovante daquele dia.', screen: 'receipts', anchor: 'receipts.chain' },
      { text: 'Para o acesso das pessoas, Pessoas e papéis mostra quem entra no Console, com que papéis e quando entrou pela última vez; o histórico de cada pessoa guarda cada mudança de acesso, com quem fez e por quê.', screen: 'people', anchor: 'people.list' },
    ],
  },
  {
    id: 'people',
    title: 'Adicionar uma pessoa e dar um papel',
    outcome: 'Convidar alguém para o Console, escolher o papel e o alcance dele, e ler o histórico de acesso.',
    minutes: 3,
    roles: ['admin'],
    steps: [
      { text: 'Em Pessoas e papéis, cada linha é uma pessoa da empresa, com os papéis, o último acesso e o estado da conta. Vale para todos os projetos, não só este espaço.', screen: 'people', anchor: 'people.list' },
      { text: 'Clique em Adicionar pessoa. Informe o e-mail corporativo e escolha o papel: Administração, Segurança e privacidade, Integração, Revisão, Análise ou Fornecedor. Cada papel vale na empresa inteira ou só num espaço.', screen: 'people', anchor: 'people.add' },
      { text: 'A pessoa recebe um convite de uso único: escolhe a senha e configura o aplicativo autenticador no primeiro acesso. Se o e-mail for de um domínio do SSO da empresa, ela entra pelo provedor.', screen: 'people', anchor: 'people.add' },
      { text: 'Na linha de cada pessoa, Papéis muda o alcance, Histórico guarda cada mudança de acesso com quem fez e por quê, e Desativar corta o acesso na hora. O servidor confere cada ação.', screen: 'people', anchor: 'people.list' },
    ],
  },
  {
    id: 'sso',
    title: 'Ligar o login da empresa (SSO)',
    outcome: 'Conectar o provedor de identidade da empresa, mapear os grupos para papéis e testar o login antes de ligar.',
    minutes: 4,
    roles: ['admin'],
    steps: [
      { text: 'Em Login único (SSO), escolha o protocolo que o seu provedor oferece: OpenID Connect ou SAML 2.0. Em No provedor, cole estão os dados que o provedor pede; em Do provedor, traga, o que ele dá a você.', screen: 'sso', anchor: 'sso.connection' },
      { text: 'Quem entra e com que papel: cada grupo do provedor vira um papel na empresa inteira. Quem ainda não existe no Console é criado no primeiro login, e os papéis são conferidos a cada login.', screen: 'sso', anchor: 'sso.roles' },
      { text: 'Regras do login: se o SSO está ligado, os domínios de e-mail que veem a opção, e se o Console pede o aplicativo autenticador depois do provedor. Salve desligado primeiro.', screen: 'sso', anchor: 'sso.rules' },
      { text: 'Testar o login abre o provedor numa aba nova e diz os papéis que o login daria, sem criar ninguém. Com o teste certo, ligue o SSO e salve com motivo.', screen: 'sso', anchor: 'sso.test' },
    ],
  },
]

export const tutorial = (id: string) => TUTORIALS.find((t) => t.id === id) ?? null

/** The tutorials that pass through a screen, for the help of that screen. */
export const tutorialsFor = (screen: ScreenId) => TUTORIALS.filter((t) => t.steps.some((s) => s.screen === screen))
