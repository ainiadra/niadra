---
id: restricoes-por-pessoa
lang: pt
slug: nao-fale-de-valores-com-a-minha-esposa-restricoes-por-pessoa
title: '"Não fale de valores com a minha esposa": restrições por pessoa em agentes de IA'
description: Quando o cliente pede que um contato não receba certa informação, a regra precisa valer em todo agente e em todo canal, e só para aquela pessoa. Como guardar uma restrição por pessoa, como ela aparece para o agente e por que preferência de contato não é restrição.
order: 4
kind: guide
date: 2026-10-08T09:00:00-03:00
category: Segurança
icon: lock
cover: A regra vale para quem ela nomeia
keywords: restrição de informação por contato agente de IA, não falar de valores com familiar, privacidade por pessoa em atendimento com IA, agente de IA respeitar pedido do cliente, contas com vários contatos, preferência de contato agente de IA, instrução do cliente para agentes
---

Uma restrição por pessoa funciona quando fica guardada na conta do cliente, diz exatamente a quem se aplica e chega a todo agente que atende qualquer contato daquela conta. "Não falar de valores com a Clara" precisa impedir que qualquer agente, em qualquer canal, diga um valor à Clara, e precisa deixar o titular da conta receber os valores dele normalmente.

As duas metades são igualmente importantes. Se a regra vale para menos gente do que devia, a informação vaza. Se vale para mais gente, o agente recusa ao próprio titular o que é dele.

## Onde esse pedido aparece?

Em qualquer conta com mais de um contato:

- O titular de um plano pede que a esposa, também cadastrada, não receba valores de cobrança.
- O sócio de uma empresa pede que o financeiro receba boletos, mas não detalhes do contrato.
- O cliente de um escritório pede que um parente que às vezes liga não seja informado sobre o andamento.

O pedido costuma ser feito a uma pessoa da equipe, uma vez, numa conversa sobre outra coisa. Depois, a conta é atendida por um agente de WhatsApp, um de voz e um humano, cada um de um fornecedor.

## O que precisa ficar guardado?

Três coisas:

1. **A regra, nas palavras de quem pediu.** "Não falar de valores dos processos."
2. **A quem ela se aplica.** A pessoa nomeada, guardada à parte do texto. Quando a regra nomeia alguém, só vale para esse alguém.
3. **Sobre o que ela vale.** A conta inteira ("dos processos", no plural) ou um objeto só ("neste processo"). Uma regra sobre a conta não pode ficar presa ao único processo que estava aberto na tela quando o pedido foi feito.

A regra também precisa sobreviver à forma como foi dita. Quando o pedido é feito ao assistente da equipe e ele responde "não consigo registrar isso aqui", a regra continua valendo: quem decide é a pessoa que pediu, não a resposta do assistente.

## Como a regra aparece para o agente?

A redação muda o resultado. Testamos várias formas com o mesmo modelo, na mesma conversa:

- "Não falar de valores com a Clara (com este cliente)" fez o agente recusar os valores ao titular, que lia "este cliente" como ele mesmo.
- "Não falar de valores com a Clara (vale para quem ela nomeia)" ainda confundia o modelo menor.
- "Exigido se quem fala for Clara: não falar de valores. Com qualquer outro contato, não se aplica" funcionou para os dois lados: a Clara não recebeu valores, e o titular recebeu.

A forma condicional, que diz quando a regra vale e quando não vale, foi a única que o modelo menor leu certo.

## Preferência de contato é a mesma coisa?

Não, e misturar as duas causa o mesmo erro de recusar ao titular. "Contatar o Marcos só pelo WhatsApp, depois das 10h" diz como a equipe deve procurar o Marcos. Ela não diz o que se pode responder a ele. Quando a preferência aparecia junto com as restrições, como "exigido", o agente recusava responder ao próprio Marcos. Separada, como preferência de contato, ela é respeitada quando a equipe vai procurá-lo, e não atrapalha o atendimento.

## Como a Niadra resolve

Na Niadra, uma instrução sobre a conta fica guardada com a pessoa a quem se aplica, e o [contexto](/produtos/contexto) de cada agente a mostra de forma condicional: o que vale quando quem fala é aquela pessoa, e que não se aplica a qualquer outro contato. A restrição vale para toda a conta e em todos os canais, e preferências de contato aparecem à parte, como guia para quando a equipe procurar alguém. A [identidade](/produtos/identidade) liga cada telefone e e-mail à conta certa, para que a regra chegue mesmo quando a pessoa fala de um número novo. O post sobre [LGPD e segurança](/blog/lgpd-e-seguranca-na-memoria-de-clientes-para-ia) explica o resto das proteções, e a documentação descreve [privacidade](https://docs.niadra.com/concepts/privacy) e [identidade](https://docs.niadra.com/concepts/identity).

## Perguntas frequentes

### E se a pessoa restrita ligar de um número que a memória não conhece?

A regra depende de reconhecer quem fala. Um número novo, ainda não ligado à conta, não recebe nada da conta. Quando o número é ligado à pessoa, a restrição passa a valer para ele.

### A restrição pode sumir do contexto em algum caso?

Ela não pode sumir em silêncio. Se a memória não conseguir ler as instruções de algum contato, o contexto diz que elas não foram lidas, em vez de entregar um bloco sem a regra.

### A restrição vale para agentes de outros fornecedores?

Vale. A memória é neutra: o agente de WhatsApp de um fornecedor e o de voz de outro leem a mesma instrução da conta, como mostra o post sobre [agentes de fornecedores diferentes](/blog/como-agentes-de-fornecedores-diferentes-compartilham-o-historico).
