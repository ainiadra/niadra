/**
 * Every screen of a space, said for someone who runs the business and never saw the product: what it is for,
 * what the person is looking at, what they can do and what happens next. One source for the rail, the page
 * headers, the help of each screen and the tutorials, so the Console says the same thing everywhere.
 */
import type { Role } from '../api/types'
import type { Feature } from './features'
import type { GlossaryKey } from './glossary'
import { paths } from '../paths'
import type { IconName } from '../ui/Icon'

export interface ScreenInfo {
  /** The name in the rail and in the title of the page. */
  label: string
  /** One line under the name, in the rail and in the help: the job, said as a verb. */
  short: string
  /** What this screen is and why it matters to the business: the first paragraph of the help. */
  what: string
  /** One short line under the title of the page, only when the title does not say it all. */
  lead?: string
  /** How to read what is on the screen, one line per part, with the units and the periods. */
  read: string[]
  /** The one thing to do here, when there is one. */
  next?: string
  /** The words of this screen, defined in the glossary. */
  terms: GlossaryKey[]
  to: (space: string) => string
  icon: IconName
  roles?: Role[]
  feature?: Feature
}

const REGISTRY = {
  home: {
    label: 'Início',
    short: 'Por onde começar e como o espaço está',
    what: 'Os tutoriais, o que a memória já está fazendo neste espaço e os trabalhos do Console. Cada resposta vem do estado real do espaço.',
    read: [
      'Tutoriais: os passo a passo guiados, com quantos você já fez.',
      'Agentes conectados, eventos chegando e contexto entregue: cada quadro diz Sim, Ainda não ou Não conferido pelo que o espaço registra e abre a tela de onde a resposta vem.',
      'Os trabalhos do Console: os grupos do menu, com o que cada um serve.',
    ],
    next: 'Se Agentes conectados diz Ainda não, crie a chave do primeiro agente.',
    terms: ['fonte', 'chave', 'evento', 'contexto', 'comprovante'],
    to: paths.home,
    icon: 'home',
  },
  tutorials: {
    label: 'Tutoriais',
    short: 'Passo a passo guiado, um por tela',
    what: 'Um passo a passo para cada tela e cada trabalho do Console, escrito para quem nunca viu o produto: do que a memória sabe de um cliente a quem acessou o quê, passando por identidade, padrões, avisos, sistemas e configuração. Cada passo diz onde clicar e leva você até lá.',
    read: [
      'Cada cartão é um tutorial, com o que você saberá fazer no fim, os minutos e os passos. Concluído aparece quando você chega ao último passo.',
      'Começar abre a tela do primeiro passo com o guia ao lado; Me leve lá, num passo, abre a tela daquele passo.',
      'O guia acompanha você de tela em tela e destaca a parte da tela de que o passo fala.',
    ],
    next: 'Comece por Conheça o Console em 5 minutos.',
    terms: ['memoria', 'contexto', 'comprovante', 'aproveitamento'],
    to: paths.tutorials,
    icon: 'book',
  },
  customers: {
    label: 'Clientes',
    short: 'Ver o que os agentes sabem de um cliente',
    what: 'O que a Niadra sabe de cada cliente: o que ele contou em cada canal, o que os sistemas registraram, o que cada agente fez e o que cada agente recebe antes de atender. Abra um cliente pela lista ou busque por um dado exato dele.',
    lead: 'Cada cliente que a memória conhece, pelo pseudônimo. Abra um para ver o que os agentes sabem dele.',
    read: [
      'A busca encontra um cliente por um dado exato: telefone com o código do país, e-mail, identificador do WhatsApp ou o pseudônimo p_… que aparece nos comprovantes. Nome e texto livre não encontram ninguém, de propósito: a memória guarda os identificadores como hash.',
      'A lista mostra cada cliente que a memória conhece: o pseudônimo, o tipo (pessoa, conta ou parceiro), até três identificadores e a última atividade em qualquer canal ou sistema. Escolha a ordem e o tipo acima dela.',
      'Na lista, os identificadores vêm mascarados para todos os papéis: +55*******4321 é um telefone que termina em 4321. O dado inteiro, quando o seu papel pode vê-lo, fica na tela do cliente. Um cliente apagado não aparece.',
      'Abrir memória leva à tela do cliente: fatos, pendências, linha do tempo, padrões, objetos e o contexto que cada agente recebe.',
    ],
    next: 'Abra um cliente da lista e veja, na tela dele, o contexto que o agente de voz recebe antes de atender.',
    terms: ['memoria', 'contexto', 'comprovante', 'pseudonimo', 'identificador'],
    to: paths.customers,
    icon: 'search',
  },
  profile: {
    label: 'Memória do cliente',
    short: 'Tudo o que a memória guarda sobre uma pessoa, conta ou parceiro',
    what: 'A memória de um cliente, item por item, com a origem de cada um: quem disse, em qual canal e quando. Ao lado, o contexto exato que um agente recebe antes de agir, com o que a política retém e por quê.',
    read: [
      'Fatos: o que a memória sabe, com a confiança e a última confirmação. Um valor mascarado existe, mas o seu papel não o vê.',
      'Pendências e promessas: o que ficou em aberto, com prazo; vencida é a que passou do prazo sem uma ação fechar.',
      'Linha do tempo: cada conversa encerrada e cada tarefa de agente interno, resumidas, com o desfecho.',
      'Padrões: o que se repete, com a regra, as evidências e o prazo de validade.',
      'Objetos e ações: pedidos, faturas e tickets ligados ao cliente, e o que cada agente fez neles.',
      'Relações: o desenho de quem está ligado a este cliente: identificadores, empresa e parceiro, objetos, e os agentes e sistemas que o atenderam ou agiram por ele. Mostrar mais um nível traz as relações das relações.',
      'O contexto que o agente recebe: escolha o agente, a visão e o nível de verificação e veja o texto que ele recebe e o que fica retido, com o motivo.',
    ],
    next: 'Escolha um agente no painel da direita e clique em Ver o que o agente recebe.',
    terms: ['fato', 'pendencia', 'episodio', 'padrao', 'objeto', 'contexto', 'retido', 'mascarado', 'nivelVerificacao', 'visao'],
    to: paths.customers,
    icon: 'search',
  },
  relations: {
    label: 'Mapa de relações',
    short: 'Quem está ligado a quem, no espaço inteiro',
    what: 'O desenho das relações que a memória conhece: cada cliente ligado aos seus identificadores, à empresa e ao parceiro, aos pedidos, faturas e chamados, e aos agentes e sistemas que o atenderam ou agiram por ele. O mapa abre pelos itens mais ligados do espaço e cresce a partir de qualquer um deles.',
    lead: 'Quem está ligado a quem, e por onde cada agente e sistema entrou.',
    read: [
      'Cada forma é um item: círculo escuro para pessoa, quadrado para empresa, losango para parceiro, círculo pequeno para identificador, retângulo para objeto, hexágono para agente e retângulo de contorno para sistema. A legenda diz quantos de cada há no mapa, e esconde ou mostra cada tipo.',
      'Cada linha é uma relação. Passe o ponteiro ou o foco por uma linha para ler a frase: quem identifica quem, quem faz parte de qual empresa, quem atendeu, quem agiu sobre o quê. A linha tracejada é uma sugestão de identidade que ninguém confirmou.',
      'Passar o ponteiro ou o foco por um item abre o cartão dele, com o tipo, os dados e o botão Abrir, que leva à tela do item. Clicar num item (ou Enter no teclado) traz as relações dele para o mapa; os novos saem de dentro dele.',
      'Ver como lista mostra os mesmos itens e as mesmas relações numa tabela, para quem prefere ler ou usa o celular.',
      'Os avisos acima do mapa dizem quando ele mostra só os itens mais próximos e quantos itens o seu papel não vê.',
    ],
    next: 'Clique num cliente do mapa para trazer as relações dele, e depois em Abrir para ver a memória completa.',
    terms: ['perfil', 'identificador', 'conta', 'parceiro', 'objeto', 'fonte', 'assercao'],
    to: paths.relations,
    icon: 'graph',
  },
  identity: {
    label: 'Identidade',
    short: 'Um cliente só, em todos os canais',
    what: 'Como a Niadra reconhece o mesmo cliente no WhatsApp, na voz, no app e no CRM, e por quê. Cada ligação entre dois identificadores é uma asserção registrada, com método e evidência, e pode ser desfeita. Aqui o seu time confere as ligações, aceita ou dispensa sugestões, une ou separa perfis e liga pessoas a contas e parceiros.',
    lead: 'Como a Niadra reconhece o mesmo cliente em todos os canais, e quem confere cada ligação.',
    read: [
      'Asserções: cada ligação entre dois identificadores, com o método (uma identificação explícita, um login, um sistema) e o estado (aplicada, pendente, recusada).',
      'Sugestões: pares que a Niadra acha que são a mesma pessoa, com a pontuação, esperando uma pessoa aceitar ou dispensar. Nada se une sozinho por nome parecido.',
      'Unir e separar: dois perfis viram um, ou um perfil se divide; os fatos e conversas presos a cada identificador vão junto.',
      'Bloquear identificador: o telefone da loja ou o e-mail genérico da empresa deixam de ligar pessoas entre si.',
      'Vínculos e Matriz e subsidiárias: pessoas ligadas a contas e parceiros por papel, e as organizações sob a matriz delas.',
    ],
    terms: ['identificador', 'assercao', 'perfil', 'conta', 'parceiro'],
    to: paths.identity,
    icon: 'link',
  },
  patterns: {
    label: 'Padrões',
    short: 'O que se repete e vira sinal',
    what: 'Os sinais que a memória deriva do que se repete na vida de um cliente, como a terceira reclamação sobre entrega em 90 dias. Cada padrão nasce de uma regra escrita, leva as evidências e vence no prazo da regra. Aqui o seu time monta e testa as regras antes de ligar, e retira um padrão errado.',
    lead: 'Os sinais que se repetem na vida de um cliente, cada um por uma regra escrita, com evidências e prazo.',
    read: [
      'Regras em uso: as regras ligadas neste espaço, com os parâmetros de cada uma. Mudar uma regra é uma proposta que outra pessoa aprova.',
      'Criar e testar: a regra roda contra os últimos 90 dias e diz quantos clientes teriam o padrão, com exemplos, sem gravar nada.',
      'Padrões de um cliente: retire um padrão errado ou tire o cliente dos padrões quando ele se opuser.',
    ],
    next: 'Teste uma regra do catálogo contra os últimos 90 dias e veja quantos clientes ela marcaria.',
    terms: ['padrao', 'diff', 'quatroOlhos'],
    to: paths.patterns,
    icon: 'layers',
  },
  agentMemory: {
    label: 'Memória dos agentes',
    short: 'O que os agentes aprenderam sobre o trabalho',
    what: 'O que cada agente aprendeu sobre o próprio trabalho: procedimentos que funcionaram, como as ferramentas e os processos da empresa se comportam e o que dá errado. Nada aqui é sobre clientes: uma nota com nome, telefone, e-mail ou documento é recusada.',
    lead: 'O que cada agente aprendeu sobre o próprio trabalho. Nada aqui é sobre clientes.',
    read: [
      'Notas: entram no prompt de cada agente, antes do contexto do cliente. Uma edição gera versão nova; a anterior fica aposentada e guardada.',
      'Propostas: notas que um agente ou uma destilação propôs e esperam uma pessoa aprovar, editar ou recusar.',
      'Ligar e limites: se a memória dos agentes está ligada, quem grava e quantas notas cada agente pode ter.',
    ],
    terms: ['memoriaAgentes', 'agente'],
    to: paths.agentMemory,
    icon: 'sliders',
  },
  receipts: {
    label: 'Comprovantes',
    short: 'Quem leu o quê, e quando',
    what: 'Cada vez que um agente lê a memória, ou alguém abre a memória de um cliente aqui, fica um comprovante: quem leu, sobre quem, para quê, em que nível, o que foi entregue e o que ficou retido. Os comprovantes são encadeados por hash, e a raiz de cada dia fica gravada fora do banco: é assim que a empresa responde, meses depois, por que um agente disse o que disse.',
    lead: 'Cada leitura da memória deixa um comprovante: quem leu, sobre quem, o que recebeu e o que ficou retido.',
    read: [
      'A lista mostra os comprovantes do mais recente para o mais antigo, 50 por página; os filtros limitam por agente, tipo, cliente, conversa e período.',
      'Perfil: o cliente sobre quem foi a leitura, pelo pseudônimo p_…, o mesmo da lista de Clientes. Um comprovante sem cliente é de uma ação que não foi sobre uma pessoa.',
      'Tipo: Leitura é um contexto entregue a um agente; Consulta ao histórico, uma busca; Ação no Console, algo que uma pessoa fez aqui; Leitura negada, uma tentativa que a política recusou.',
      'Itens e Retidos: quantos itens o agente recebeu e quantos existiam e não foram entregues. Abra o comprovante para ver o motivo de cada um.',
      'Conferir a cadeia de um dia: refaz a corrente de hashes do dia e compara com a raiz gravada no banco e com a cópia fora dele. O dia da cadeia é contado em UTC: ele termina às 21:00 no horário de Brasília.',
      'Ação no Console: a pessoa, o que ela fez e sobre o quê, em palavras. Uma chave emitida ou revogada é uma ação do plano de controle, sem pessoa.',
    ],
    next: 'Abra o comprovante de uma leitura e veja a origem de cada item que o agente recebeu.',
    terms: ['comprovante', 'pseudonimo', 'retido', 'cadeia', 'hash', 'nivelVerificacao'],
    to: paths.receipts,
    icon: 'receipt',
  },
  receipt: {
    label: 'Comprovante',
    short: 'Por que o agente recebeu cada item',
    what: 'Um comprovante aberto: a leitura, o manifesto do que entrou, cada item entregue com o evento de onde ele veio, o que ficou retido e por quê, e a posição dele na cadeia do dia.',
    read: [
      'Comprovante: quem leu, a audiência, a finalidade, o nível de verificação e a versão da política em vigor na hora.',
      'Manifesto: o hash do que entrou e o hash do contexto entregue; é o que prova o que o agente recebeu.',
      'Itens entregues e a origem de cada um: para cada item, o evento, o canal e a hora de onde ele veio.',
      'O que ficou retido e por quê: política, nível de verificação, não coube ou não usado por este agente.',
    ],
    terms: ['comprovante', 'manifesto', 'linhagem', 'retido', 'cadeia'],
    to: paths.receipts,
    icon: 'receipt',
  },
  turns: {
    label: 'Turnos dos agentes',
    short: 'Por que o agente respondeu aquilo',
    what: 'O registro de cada rodada de um agente: o que ele leu da memória, que ferramentas e modelos chamou, o que mostrou à pessoa, o que afirmou e o que decidiu, com a versão em que rodou. É aqui que o seu time entende por que o agente respondeu o que respondeu, e reproduz uma conversa real depois de mudar um prompt ou um modelo.',
    lead: 'O registro de cada rodada de um agente: o que leu, chamou, mostrou, afirmou e decidiu.',
    read: [
      'Todo turno fica 7 dias e abre pelo ID. Os marcados na captura, os sorteados para a amostra e os promovidos ficam guardados por mais tempo e aparecem na lista.',
      'Marcas: o que chamou a atenção no turno, como um erro numa chamada, uma afirmação sem evidência ou um retorno negativo da pessoa.',
      'Abrir um turno mostra, em ordem: o que leu, o que chamou, o que mostrou, o que afirmou e o que decidiu.',
    ],
    terms: ['turno', 'afirmacao', 'replay'],
    to: paths.turns,
    icon: 'steps',
    feature: 'turns',
  },
  turn: {
    label: 'Turno',
    short: 'Uma rodada do agente, passo a passo',
    what: 'Uma rodada de um agente, do que entrou até a resposta: o que leu da memória, o que chamou, o que mostrou, o que afirmou e o que decidiu, com a versão fixada para um replay reproduzir.',
    read: [
      'O build fixado: o que precisa ser igual para reproduzir este turno: prompts, modelo e esquemas das ferramentas.',
      'O que leu: cada leitura da Niadra, com o comprovante dela.',
      'O que chamou: as chamadas de ferramenta e de modelo, na ordem, com o tempo de cada uma.',
      'O que afirmou: cada afirmação do agente, com o veredito e a evidência no próprio turno.',
    ],
    terms: ['turno', 'afirmacao', 'replay', 'comprovante'],
    to: paths.turns,
    icon: 'steps',
    feature: 'turns',
  },
  coordination: {
    label: 'Coordenação',
    short: 'Quem está com o cliente agora',
    what: 'Entre vários agentes, pessoas e sistemas, quem está com cada cliente agora, o que já saiu uma vez só (para a mesma mensagem não sair duas vezes) e os orçamentos de contato. Só contagens: nenhum cliente aparece nesta tela. A Niadra sabe quem está com o cliente; quem envia e quem barra é o sistema da empresa.',
    lead: 'Quem está com cada cliente agora, o que já saiu uma vez só e os orçamentos de contato. Só contagens.',
    read: [
      'Posse agora: quantas afirmações de posse valem neste momento, por tipo e origem.',
      'Efeitos: o que saiu nos últimos dias pela chave que garante uma vez só, por estado.',
      'Conflitos resolvidos e Orçamentos de contato: sobreposições que a coordenação resolveu, e quantos clientes chegaram ao limite de contatos de cada finalidade.',
    ],
    terms: ['posse', 'finalidade'],
    to: paths.coordination,
    icon: 'swap',
    feature: 'coordination',
  },
  usage: {
    label: 'Uso e aproveitamento',
    short: 'Quanto cada agente usa e aproveita',
    what: 'Dois lados da mesma conta. O uso diz o que o espaço consumiu: conversas e tarefas, que são a unidade de cobrança, leituras de contexto e eventos recebidos. O aproveitamento diz o que cada agente fez com o contexto que recebeu: se usou, se perguntou de novo o que já sabia e se contradisse a memória. Por agente e por fornecedor, nunca por cliente.',
    lead: 'O que o espaço consumiu e o que cada agente fez com o contexto que recebeu. Por agente, nunca por cliente.',
    read: [
      'O período fica no alto da tela (7, 30 ou 90 dias até hoje), e vale para todas as tabelas. Os dias são contados em UTC, como o servidor conta: o dia de hoje termina às 21:00 no horário de Brasília, e uma conversa medida depois disso já conta no dia seguinte.',
      'Uso por agente: conversas e tarefas cobradas, leituras de contexto, consultas ao histórico e eventos recebidos, por agente.',
      'Cota e custo do espaço: o total por métrica, a cota da empresa e o custo para a Niadra; o extrato do mês sai em CSV.',
      'Teto diário de gasto com IA: o que o espaço gastou hoje com o provedor de IA contra o teto do dia, em dólares.',
      'Tempo de leitura do contexto: nas últimas 24 horas e nos últimos 7 dias, a mediana e o p95 do tempo do servidor, sem a rede, a parte das leituras abaixo de 100 ms, se o objetivo de 99% foi cumprido, quantas leituras houve e quantas saíram degradadas. Os sete dias são o que a região guarda.',
      'Aproveitamento do contexto: por agente, as taxas de aproveitamento, repetição, contradição e transferência sem leitura, cada uma com o intervalo de 95% e o número de casos.',
      'Uma conversa, entrega por entrega: a medição de uma conversa só, para ver cada entrega de contexto e o que o agente fez com ela.',
    ],
    next: 'Compare o aproveitamento dos agentes por fornecedor: é o número que mostra quem usa o que recebe.',
    terms: ['aproveitamento', 'repeticao', 'contradicao', 'transferenciaSemLeitura', 'intervalo', 'tokens', 'extracao'],
    to: paths.usage,
    icon: 'gauge',
  },
  measure: {
    label: 'Desfechos e experimentos',
    short: 'Quanto o agente vendeu e o que mudou',
    what: 'O que o trabalho dos agentes levou a acontecer: vendas e outros desfechos atribuídos a cada agente, com o método que sustenta cada número, e a diferença contra o arquivo do BI da empresa. Também os experimentos, com um grupo com a memória contra um grupo sem ela, e o custo por turno. Contagens por agente, nunca por cliente.',
    lead: 'Vendas e outros desfechos atribuídos a cada agente, com o método que sustenta cada número, e os experimentos.',
    read: [
      'O período fica no alto da tela (7, 30 ou 90 dias até hoje), em dias UTC, como o servidor conta: o dia de hoje termina às 21:00 no horário de Brasília.',
      'Desfechos atribuídos: cada método numa faixa. Determinístico é o pedido que trouxe o código que o agente mostrou; provável é a pessoa que viu o item e comprou depois. Os dois nunca se somam.',
      'Diferença contra o arquivo do BI: suba o CSV do BI e veja, linha a linha, o desvio contra o que a Niadra atribuiu.',
      'Experimentos: cada braço contra o controle, com o intervalo e se a diferença já pode ser lida.',
      'Custo por turno: o que cada agente gastou por turno, ao lado do custo que a empresa mediu com a memória desligada.',
    ],
    terms: ['desfecho', 'experimento', 'intervalo', 'turno', 'afirmacao', 'restricao'],
    to: paths.measure,
    icon: 'layers',
    feature: 'measurement',
    roles: ['analysis'],
  },
  review: {
    label: 'Revisão',
    short: 'Seu time confere uma amostra',
    what: 'Uma amostra das conversas passa pelo seu time: pessoas conferem o que a memória extraiu e o que a medição disse. A concordância diz quanto dá para confiar nos números das outras telas. Cada veredito vira um caso de teste do espaço. Você só vê o que o agente daquela finalidade veria.',
    lead: 'Uma amostra das conversas passa pelo seu time; a concordância diz quanto dá para confiar nos números.',
    read: [
      'Amostras para revisar: cada amostra traz os fatos, as pendências, os padrões e os sinais da medição de uma conversa, para marcar certo, errado ou faltou.',
      'Concordância: por tipo de item e por sinal, quanto o seu time concorda com o sistema e quanto os revisores concordam entre si, no período escolhido (30, 90 ou 365 dias), com o intervalo e o número de julgados.',
      'Casos de teste: cada veredito roda contra a memória e a medição de hoje; um caso que falha mostra o que mudou.',
    ],
    next: 'Julgue uma amostra: marque cada fato e cada sinal e grave os vereditos.',
    terms: ['amostra', 'concordancia', 'intervalo', 'aproveitamento', 'finalidade'],
    to: paths.review,
    icon: 'eye',
    roles: ['review'],
  },
  gaps: {
    label: 'Lacunas e respostas prontas',
    short: 'O que falta na memória',
    what: 'O que os clientes perguntam e nenhum sistema manda para a memória, o que os agentes precisam perguntar a clientes que a memória já conhece, e as perguntas que a memória responde sempre pela mesma regra. É a lista do que ligar primeiro. Contagens por assunto, nunca um cliente, um valor ou um texto.',
    lead: 'O que os clientes perguntam e nenhum sistema manda para a memória. A lista do que ligar primeiro.',
    read: [
      'O período fica no alto da tela (7, 30 ou 90 dias até hoje).',
      'Lacunas de conhecimento: cada linha junta um assunto e a categoria da conversa, com quantos clientes e quantas vezes. Nenhuma fonte grava é uma lacuna estrutural; com fonte, o sistema manda o tipo, mas não para esses clientes.',
      'Perguntas recorrentes: as perguntas que o espaço mais recebe e quanto a memória respondeu, em porcentagem.',
      'Respostas prontas propostas: linhas escritas por regra para o seu time aprovar; nada entra no contexto sem essa aprovação.',
    ],
    terms: ['lacuna', 'fonte', 'contexto'],
    to: paths.gaps,
    icon: 'search',
    roles: ['analysis'],
  },
  data: {
    label: 'Dados',
    short: 'Apagar, exportar e responder ao titular',
    what: 'Os direitos do titular e a saída dos dados: apagar um cliente por inteiro, pela linhagem de tudo o que veio dele; entregar o arquivo com os dados dele; atender a oposição a uma finalidade; e acompanhar a exportação contínua para o bucket da empresa.',
    lead: 'Apagar um cliente por inteiro, entregar o arquivo dele e acompanhar a exportação contínua.',
    read: [
      'Apagamento: escolha o que apagar (um perfil, um identificador ou uma conversa) e acompanhe cada pedido até Concluído, com o que foi apagado por tabela e o comprovante.',
      'Arquivo do titular: tudo o que a memória guarda sobre uma pessoa, num zip com o hash de cada parte. O arquivo vale 24 horas.',
      'Exportação contínua: cada agendamento escreve uma cópia incremental no bucket da empresa; as execuções mostram o que saiu e quando.',
    ],
    next: 'Para atender um pedido de apagamento, abra a aba Apagamento, escolha Um perfil inteiro e cole o ID do perfil.',
    terms: ['titular', 'linhagem', 'arquivoTitular', 'exportacaoContinua', 'finalidade', 'comprovante'],
    to: paths.data,
    icon: 'lock',
  },
  agents: {
    label: 'Agentes e chaves',
    short: 'Quem pode ler e gravar na memória',
    what: 'Cada agente, fornecedor e sistema que fala com a memória é uma fonte, com a própria chave, as finalidades, a audiência e o teto de verificação. É aqui que um agente novo nasce: crie a fonte, emita a chave dela e, quando precisar, gire, revogue ou corte o acesso na hora.',
    lead: 'Cada agente, fornecedor e sistema é uma fonte, com a própria chave e o que pode ler.',
    read: [
      'Cada linha é uma fonte: o fornecedor, o canal, as finalidades que ela pode ler, a audiência e o teto de verificação.',
      'Ativa é uma fonte com acesso; Acesso cortado é uma fonte cujas chaves pararam de valer na região.',
      'Abra a fonte para ver as chaves, emitir uma nova, girar ou revogar, e para cortar o acesso.',
    ],
    next: 'Clique em Nova fonte, dê um nome ao agente, escolha a audiência e emita a primeira chave.',
    terms: ['fonte', 'chave', 'escopo', 'audiencia', 'finalidade', 'tetoVerificacao'],
    to: paths.agents,
    icon: 'key',
  },
  agent: {
    label: 'Fonte',
    short: 'Uma fonte, as chaves dela e o corte de acesso',
    what: 'Uma fonte: o que ela pode ler e fazer, as chaves que ela usa e o corte de acesso, que para todas as chaves dela em segundos.',
    read: [
      'O que esta fonte pode ler e fazer: as regras fixadas na criação; para mudar uma, crie uma fonte nova e corte esta.',
      'Chaves: cada chave com os escopos, o estado e a validade. O segredo aparece uma vez, na emissão.',
      'Cortar o acesso agora: todas as chaves param na região em segundos; o corte vira permanente na próxima configuração assinada.',
    ],
    terms: ['fonte', 'chave', 'escopo', 'tetoVerificacao'],
    to: paths.agents,
    icon: 'key',
  },
  systems: {
    label: 'Sistemas',
    short: 'Eventos que entram, avisos que saem',
    what: 'Por onde cada agente e sistema fala com a memória: o endereço de entrada por webhook e o mapeamento que traduz o evento, os endpoints que recebem os avisos, quanto do que cada fonte enviou chegou, e o fluxo de comprovantes para o SIEM da empresa.',
    lead: 'Por onde os eventos entram e os avisos saem: webhooks, mapeamentos, endpoints e cobertura.',
    read: [
      'Entrada por webhook: o endereço que o sistema chama para mandar eventos, e o mapeamento que os traduz. Sem mapeamento, o evento fica guardado cru, sem virar memória.',
      'Endpoints que recebem avisos: os endereços do seu sistema que recebem os avisos, com os tipos de evento e se estão ligados.',
      'Cobertura nos últimos 7 dias: quantos eventos a fonte disse ter enviado e quantos chegaram; o que não chegou ou foi recusado aparece ao lado.',
      'Comprovantes no SIEM: o fluxo que leva cada comprovante ao sistema de segurança da empresa em segundos.',
    ],
    next: 'Copie o endereço de entrada da fonte e cole no sistema que vai mandar os eventos.',
    terms: ['webhook', 'mapeamento', 'endpoint', 'cobertura', 'siem', 'aviso'],
    to: paths.systems,
    icon: 'plug',
  },
  types: {
    label: 'Tipos',
    short: 'Os objetos que os sistemas mantêm',
    what: 'Os tipos de objeto que os sistemas da empresa mantêm e a memória acompanha: pedidos, propostas, processos, itens do catálogo. A Niadra lê os tipos do que os sistemas enviam; cada tipo diz de onde vem e quanto dos campos os objetos trazem. Declarar é opcional.',
    lead: 'Os tipos de objeto que os sistemas mantêm e a memória acompanha: pedidos, propostas, chamados.',
    read: [
      'Tipos declarados e derivados: cada tipo com a cobertura de cada campo, medida nos objetos que mudaram por último.',
      'Campos novos no tráfego: o sistema passou a enviar um campo novo; a Niadra serve o valor e deixa uma nota.',
      'Tipos novos que a Niadra passou a acompanhar, sem ninguém declarar.',
      'Campos retidos até alguém declarar (parecem dado pessoal ou são texto livre longo): guardados, fora dos agentes.',
    ],
    terms: ['tipo', 'objeto'],
    to: paths.objectTypes,
    icon: 'layers',
    feature: 'state',
    roles: ['integration'],
  },
  alerts: {
    label: 'Avisos',
    short: 'A Niadra avisa; o seu sistema age',
    what: 'A Niadra avisa por webhook quando uma regra acontece na memória, como uma promessa vencida, a terceira reclamação ou uma fonte que parou de mandar eventos. Quem age é o seu agente ou o seu sistema. Aqui ficam os disparos, as entregas, as regras em uso e o editor para montar e testar uma regra nova.',
    lead: 'A Niadra avisa o seu sistema quando uma regra acontece na memória. Quem age é o seu sistema.',
    read: [
      'Disparos: cada vez que uma regra ligada aconteceu, do mais recente para o mais antigo, com o cliente (pelo pseudônimo) ou a fonte.',
      'Entregas de webhook: cada aviso enviado ao seu endpoint, com o estado: na fila, entregue, falhando ou esgotou as tentativas. Uma entrega que esgotou pode ser enviada de novo à mão.',
      'Regras em uso: as regras ligadas e desligadas, com os parâmetros. Toda mudança é uma proposta que outra pessoa aprova.',
      'Criar e testar: monte a regra a partir do catálogo e teste; o teste diz quantos clientes dos últimos 30 dias disparariam agora, sem enviar nada.',
    ],
    next: 'Monte uma regra de promessa vencida em Criar e testar e veja quantos clientes disparariam hoje.',
    terms: ['aviso', 'webhook', 'endpoint', 'padrao', 'diff'],
    to: paths.alerts,
    icon: 'bell',
  },
  config: {
    label: 'Configuração',
    short: 'Toda mudança, com autor, motivo e aprovação',
    what: 'Os documentos que governam o espaço: a política de leitura, os mapeamentos, as visões de tarefa, as regras de padrão e de aviso, os endpoints, a exportação, a amostragem e os experimentos. Toda mudança é um diff com autor e motivo, e só vale depois que outra pessoa aprova. É a tela técnica do Console: as outras telas propõem as mudanças mais comuns por formulário.',
    lead: 'Os documentos que governam o espaço. Toda mudança é um diff que outra pessoa aprova.',
    read: [
      'Documentos: cada documento em vigor, com a versão e quem pode escrever nele.',
      'Diffs: as propostas de mudança, aguardando aprovação, aplicadas ou recusadas. Aprovar aqui é o que faz uma mudança valer.',
      'Histórico: cada versão de cada documento, para voltar atrás.',
      'Segredos: só qual segredo existe, a versão e quem girou; o valor fica no cofre da região.',
      'Assistente: pede uma proposta de documento a partir de amostras de eventos, que vira um diff para aprovar.',
    ],
    next: 'Abra a aba Diffs para ver o que espera aprovação.',
    terms: ['diff', 'quatroOlhos', 'politica', 'mapeamento', 'segredo'],
    to: paths.config,
    icon: 'sliders',
  },
  people: {
    label: 'Pessoas e papéis',
    short: 'Quem entra no Console e com que papel',
    what: 'Quem entra no Console da sua empresa, com que papéis e como. Vale para todos os projetos, não só este espaço. Cada papel vale na empresa inteira ou num espaço, e o servidor confere cada ação.',
    read: [
      'Cada linha é uma pessoa, com os papéis, o último acesso e o estado da conta.',
      'Adicionar pessoa gera um convite de uso único; a pessoa escolhe a senha e configura o aplicativo autenticador no primeiro acesso.',
      'O histórico de cada pessoa guarda cada mudança de acesso, com quem fez e por quê.',
    ],
    terms: ['papel', 'espaco'],
    to: paths.people,
    icon: 'people',
    roles: ['admin'],
  },
  sso: {
    label: 'Login único (SSO)',
    short: 'Entrar com o login da empresa',
    what: 'O seu time entra no Console com o login da empresa, por OIDC ou SAML 2.0. Os grupos do provedor de identidade viram papéis, e quem ainda não existe no Console é criado no primeiro login.',
    lead: 'O seu time entra com o login da empresa, por OIDC ou SAML 2.0; os grupos viram papéis.',
    read: [
      'Conexão com o provedor de identidade: o que colar no provedor e o que trazer dele.',
      'Quem entra e com que papel: cada grupo mapeado dá um papel na empresa inteira.',
      'Testar o login: entre com uma conta do provedor numa aba nova e veja os papéis que o login daria, sem criar ninguém.',
    ],
    terms: ['papel'],
    to: paths.sso,
    icon: 'door',
    roles: ['admin'],
  },
} as const satisfies Record<string, ScreenInfo>

export type ScreenId = keyof typeof REGISTRY
export const SCREENS: Record<ScreenId, ScreenInfo> = REGISTRY

export interface NavGroup {
  id: string
  label: string
  /** One line under the group's name: the job the group serves. */
  short: string
  screens: ScreenId[]
  /** Collapsed by default: the technical screens a person opens now and then. */
  advanced?: boolean
  /** Only for the Administration role. */
  admin?: boolean
}

/** The rail, grouped by the job to be done, not by the module that serves it. */
export const NAV_GROUPS: NavGroup[] = [
  { id: 'start', label: 'Começar', short: 'Por onde começar', screens: ['home', 'tutorials'] },
  { id: 'memory', label: 'Clientes e memória', short: 'O que a Niadra sabe de cada cliente', screens: ['customers', 'relations', 'identity', 'patterns'] },
  { id: 'agents', label: 'O que os agentes fizeram', short: 'Leituras, respostas e resultados, por agente', screens: ['receipts', 'turns', 'usage', 'measure', 'review', 'gaps'] },
  { id: 'privacy', label: 'Privacidade e direitos', short: 'Apagar, exportar e responder ao titular', screens: ['data'] },
  { id: 'connections', label: 'Conexões', short: 'Por onde os agentes e sistemas entram', screens: ['agents', 'systems', 'alerts'] },
  { id: 'advanced', label: 'Avançado', short: 'Ajustes finos e os recursos do núcleo de agentes', screens: ['agentMemory', 'coordination', 'types', 'config'], advanced: true },
  { id: 'admin', label: 'Administração', short: 'Quem entra no Console', screens: ['people', 'sso'], admin: true },
]

/** The screen a path under a space belongs to, for the help button and the tutorials. */
export function screenOf(pathname: string): ScreenId | null {
  const m = /^\/console\/e\/[^/]+\/([^/?#]+)(\/[^/?#]+)?/.exec(pathname)
  if (!m) return null
  const [, first, second] = m
  const detail = Boolean(second)
  switch (first) {
    case 'inicio':
      return 'home'
    case 'tutoriais':
      return 'tutorials'
    case 'clientes':
      return detail ? 'profile' : 'customers'
    case 'relacoes':
      return 'relations'
    case 'identidade':
      return 'identity'
    case 'padroes':
      return 'patterns'
    case 'memoria-dos-agentes':
      return 'agentMemory'
    case 'recibos':
      return detail ? 'receipt' : 'receipts'
    case 'turnos':
      return detail ? 'turn' : 'turns'
    case 'coordenacao':
      return 'coordination'
    case 'uso':
      return 'usage'
    case 'medicao':
      return 'measure'
    case 'revisao':
      return 'review'
    case 'lacunas':
      return 'gaps'
    case 'dados':
      return 'data'
    case 'agentes':
      return detail ? 'agent' : 'agents'
    case 'sistemas':
      return 'systems'
    case 'tipos':
      return 'types'
    case 'avisos':
      return 'alerts'
    case 'configuracao':
      return 'config'
    case 'pessoas':
      return 'people'
    case 'sso':
      return 'sso'
    default:
      return null
  }
}
