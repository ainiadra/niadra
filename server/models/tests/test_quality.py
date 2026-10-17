"""The served embedding model against the hashing embedder, on what a customer-service agent searches for.

It needs the model files, which CI does not download, so without them every test here is skipped. To run
it with the model the image bakes in:

    uv run python src/niadra_models/onnx/pinned.py paraphrase-multilingual-minilm-l12-v2-r1 ~/.cache/niadra
    NIADRA_MODELS_ONNX_EMBED_DIR=~/.cache/niadra/paraphrase-multilingual-minilm-l12-v2-r1 \
        uv run --extra onnx pytest tests/test_quality.py -s

`-s` prints the scores. A model that replaces this one passes these floors too, or brings the evidence
for new ones.
"""

import os
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from niadra_models.capabilities import Embedder
from niadra_models.registry import build
from niadra_models.rules.embeddings import HashingEmbedder
from niadra_models.settings import Settings

MODEL_DIR = Path(os.environ.get("NIADRA_MODELS_ONNX_EMBED_DIR") or "no-model")
pytestmark = pytest.mark.skipif(
    not MODEL_DIR.is_dir(), reason="NIADRA_MODELS_ONNX_EMBED_DIR names no model directory (module docstring)"
)

# (text, paraphrase, distractor). Most distractors share words with the text and mean something else,
# which is where a lexical embedder fails and a search by meaning has to hold.
TRIPLETS = [
    ("meu pedido não chegou", "a entrega atrasou", "quero trocar a senha"),
    (
        "quero cancelar minha assinatura",
        "não quero mais continuar com o plano",
        "quero assinar o plano anual",
    ),
    (
        "fui cobrado duas vezes",
        "apareceram duas cobranças iguais no meu cartão",
        "posso pagar em duas vezes?",
    ),
    (
        "a tampa do produto veio quebrada",
        "o item chegou danificado",
        "quero comprar outra tampa para o produto",
    ),
    ("não consigo entrar na minha conta", "o login não está funcionando", "quero abrir uma conta nova"),
    (
        "quero falar com um atendente",
        "preciso ser atendido por uma pessoa de verdade",
        "o atendente foi muito educado",
    ),
    ("o boleto venceu e não paguei", "minha fatura está em atraso", "recebi o boleto por email"),
    ("quero o dinheiro de volta", "gostaria de ser reembolsado", "quero voltar a comprar com vocês"),
    ("minha internet está lenta", "a conexão está muito devagar", "quero contratar internet para outra casa"),
    ("o cartão foi bloqueado", "não consigo usar meu cartão, está travado", "quero um cartão adicional"),
    (
        "quero mudar meu endereço de entrega",
        "vou morar em outro lugar e preciso atualizar onde recebo",
        "a entrega no meu endereço foi rápida",
    ),
    ("o técnico não apareceu", "a visita técnica agendada não aconteceu", "o técnico foi muito atencioso"),
    ("posso parcelar?", "dá para dividir o pagamento em várias vezes?", "já paguei a parcela deste mês"),
    (
        "quero falar com o gerente",
        "preciso de alguém com mais autoridade para resolver",
        "o gerente da loja me ligou ontem",
    ),
    ("sinal fraco no celular", "o telefone quase não pega dentro de casa", "quero comprar um celular novo"),
    ("a fatura veio com valor errado", "cobraram mais do que deviam na conta", "a fatura chegou no prazo"),
    ("my package never arrived", "the delivery is late", "I want to change my password"),
    ("I was charged twice", "there are two identical charges on my card", "can I pay in two installments?"),
    (
        "cancel my subscription",
        "I don't want to keep the plan anymore",
        "how do I subscribe to the annual plan?",
    ),
    ("the app keeps crashing", "the application closes by itself all the time", "I love the new app design"),
    (
        "my card was declined",
        "the payment with my card did not go through",
        "I want a new card with a different design",
    ),
    ("I need a copy of my invoice", "please send me the bill again", "my invoice was paid yesterday"),
    ("mi pedido no ha llegado", "el envío está retrasado", "quiero cambiar mi contraseña"),
    ("me cobraron dos veces", "hay dos cargos iguales en mi tarjeta", "quiero pagar en dos cuotas"),
    (
        "quiero hablar con una persona",
        "necesito que me atienda un agente humano",
        "la persona que me atendió fue amable",
    ),
    ("la aplicación no abre", "no consigo entrar en la app", "me gusta mucho la aplicación"),
    ("quiero devolver el producto", "deseo regresar lo que compré", "quiero comprar el producto otra vez"),
    ("el técnico no vino", "la visita técnica no se realizó", "el técnico fue muy amable"),
    ("meu pedido não chegou", "my order has not arrived", "meu pedido chegou ontem"),
    ("the product arrived broken", "o produto chegou quebrado", "the product arrived yesterday"),
]

# One customer's history as the navigation renders it (facts, conversations, open items, actions,
# patterns), and the questions an agent asks about it, with the rows that answer each one.
HISTORY = {
    "d01": "Conversa 12/08/2026 · whatsapp · O pedido 4471 chegou com a tampa quebrada; cliente pediu troca com entrega até sexta · resolvido",
    "d02": "entrega: prefere receber no endereço do trabalho, Rua Augusta 1500",
    "d03": "Conversa 02/09/2026 · voz · Cliente reclamou de cobrança duplicada no cartão de crédito referente à fatura de agosto · escalado",
    "d04": "pagamento: prefere pagar por boleto bancário",
    "d05": "Em aberto · estorno de R$ 189,90 prometido pela empresa · prazo 30/09/2026",
    "d06": "Conversa 15/09/2026 · email · Cliente pediu o cancelamento do plano anual por causa do preço · pendente",
    "d07": "contato: não atende ligações antes das 10h; prefere mensagens",
    "d08": "Feito por outro agente · crédito de R$ 189,90 lançado na fatura 2026-08",
    "d09": "Conversa 20/07/2026 · chat · Não conseguia acessar a conta, a senha foi redefinida pelo agente · resolvido",
    "d10": "produto: comprou a cafeteira Modelo X em junho",
    "d11": "Conversa 05/06/2026 · whatsapp · Dúvida sobre a garantia da cafeteira; agente explicou que cobre um ano · resolvido",
    "d12": "Padrões · reclamação recorrente: atraso na entrega, 3 vezes em 90 dias",
    "d13": "Conversa 10/09/2026 · voz · Cliente quis saber o status da portabilidade do número · pendente",
    "d14": "endereço: mudou-se para Belo Horizonte em agosto",
    "d15": "Conversa 01/09/2026 · whatsapp · The customer asked for an invoice in English for the company's accounting · resolved",
    "d16": "Conversación 03/09/2026 · chat · El cliente pidió cambiar la dirección de envío a Madrid · resuelto",
    "d17": "Conversa 18/09/2026 · voz · Cliente elogiou o atendimento da loja física · resolvido",
    "d18": "Em aberto · segunda via do boleto de setembro a cargo do cliente",
    "d19": "Conversa 22/09/2026 · whatsapp · Internet caiu três vezes na semana; técnico agendado para 25/09 · pendente",
    "d20": "plano: assinante do plano família com 4 linhas",
    "d21": "Conversa 14/08/2026 · chat · Pediu desconto para continuar cliente; ofertado 20% por 6 meses · resolvido",
    "d22": "Feito por outro agente · visita técnica agendada para 25/09/2026, período da manhã",
    "d23": "Conversa 30/08/2026 · email · Recebeu produto errado (cor azul em vez de preta) e quer devolver · pendente",
    "d24": "Conversa 11/07/2026 · voz · Cartão bloqueado após três tentativas de senha erradas; desbloqueio feito · resolvido",
    "d25": "Conversa 21/08/2026 · chat · Pediu aumento do limite do cartão para viagem · não resolvido",
    "d26": "Padrões · pediu atendimento humano",
    "d27": "Conversation 09/09/2026 · email · Customer complained that the refund for order 5520 has not shown up yet · pending",
    "d28": "Conversation 16/09/2026 · voice · Asked to upgrade the internet plan to 500 Mbps · resolved",
    "d29": "Conversación 19/09/2026 · whatsapp · La clienta dice que la factura llegó con un valor más alto de lo habitual · escalado",
    "d30": "Conversación 07/09/2026 · chat · Quería saber cómo pagar con Pix desde el extranjero · resuelto",
    "d31": "cadastro: atualizou o e-mail para o endereço corporativo",
    "d32": "Conversa 25/08/2026 · whatsapp · Perguntou se o frete é grátis para compras acima de R$ 200 · resolvido",
    "d33": "Em aberto · devolução do produto errado aguardando coleta pela transportadora",
    "d34": "Conversa 13/09/2026 · voz · Cliente irritado com a demora no atendimento telefônico · não resolvido",
    "d35": "Padrões · faturas em atraso",
    "d36": "Conversa 28/07/2026 · chat · Solicitou nota fiscal da compra da cafeteira · resolvido",
    "d37": "Feito por outro agente · segunda via da fatura enviada por e-mail",
    "d38": "Conversa 03/08/2026 · whatsapp · Quis saber se pode parcelar a compra em 10 vezes sem juros · resolvido",
    "d39": "preferência: gosta de ser chamada pelo primeiro nome, Marina",
    "d40": "Conversa 17/09/2026 · voz · Sinal de celular fraco dentro de casa; orientado a ativar chamadas por Wi-Fi · resolvido",
}
QUESTIONS = [
    ("embalagem danificada", {"d01"}),
    ("o cliente já teve problema com cobrança?", {"d03", "d29"}),
    ("ele quer cancelar?", {"d06", "d21"}),
    ("melhor horário para ligar", {"d07"}),
    ("onde entregar o pedido", {"d02", "d14"}),
    ("reembolso prometido", {"d05", "d08", "d27"}),
    ("problemas de login", {"d09"}),
    ("atrasos de entrega", {"d12"}),
    ("forma de pagamento preferida", {"d04"}),
    ("garantia do produto", {"d11"}),
    ("visita do técnico", {"d22", "d19"}),
    ("conexão instável", {"d19"}),
    ("did the customer ever request a refund?", {"d05", "d08", "d27"}),
    ("¿el cliente cambió de dirección?", {"d14", "d16"}),
    ("quantas linhas tem o plano", {"d20"}),
    ("mandamos o item errado", {"d23", "d33"}),
    ("cliente satisfeito com o atendimento", {"d17"}),
    ("migrar o número para outra operadora", {"d13"}),
    ("nota fiscal em inglês", {"d15"}),
    ("oferta para ele não sair", {"d21"}),
    ("cartão travado", {"d24"}),
    ("quer mais crédito no cartão", {"d25"}),
    ("pediu para falar com uma pessoa", {"d26"}),
    ("customer upset about waiting on the phone", {"d34"}),
    ("velocidade maior de internet", {"d28"}),
    ("fatura mais cara que o normal", {"d29"}),
    ("pagar do exterior", {"d30"}),
    ("mudou o email", {"d31"}),
    ("entrega gratuita", {"d32"}),
    ("cliente com contas atrasadas", {"d35"}),
    ("comprovante fiscal da compra", {"d36"}),
    ("reenviar o boleto", {"d18", "d37"}),
    ("pagamento em prestações", {"d38"}),
    ("como a cliente gosta de ser chamada", {"d39"}),
    ("celular sem sinal em casa", {"d40"}),
    ("the package lid was broken", {"d01"}),
    ("¿tuvo problemas con la entrega?", {"d12", "d01"}),
    ("troca de produto", {"d01", "d23"}),
    ("status da devolução", {"d33"}),
    ("dinheiro de volta do pedido 5520", {"d27"}),
]


@pytest.fixture(scope="module")
def model() -> Embedder:
    pytest.importorskip("onnxruntime", reason="the onnx extra is not installed (uv run --extra onnx)")
    return build(Settings.from_env()).embedder


def paraphrase_accuracy(embedder: Embedder) -> float:
    """Share of triplets where the paraphrase is closer to the text than the distractor is."""
    right = 0
    for text, paraphrase, distractor in TRIPLETS:
        t, p, d = embedder.embed([text, paraphrase, distractor])
        right += float(t @ p) > float(t @ d)
    return right / len(TRIPLETS)


def mean_reciprocal_rank(embedder: Embedder) -> float:
    """Of the first row that answers each question, ranking the whole history by cosine."""
    ids = list(HISTORY)
    rows = embedder.embed([HISTORY[i] for i in ids])
    reciprocal = []
    for question, answers in QUESTIONS:
        scores = rows @ embedder.embed([question])[0]
        ranked = [ids[i] for i in np.argsort(-scores, kind="stable")]
        reciprocal.append(1 / (1 + min(ranked.index(a) for a in answers)))
    return float(np.mean(reciprocal))


def compare(name: str, metric: Callable[[Embedder], float], model: Embedder) -> tuple[float, float]:
    served, hashing = metric(model), metric(HashingEmbedder())
    print(f"\n{name}: {model.version} {served:.3f}, hashing {hashing:.3f}")
    return served, hashing


def test_paraphrases_rank_above_distractors(model: Embedder) -> None:
    served, hashing = compare("paraphrase accuracy", paraphrase_accuracy, model)
    assert served >= 0.7
    assert served >= hashing + 0.4


def test_history_questions_find_their_rows_by_meaning(model: Embedder) -> None:
    served, hashing = compare("history mean reciprocal rank", mean_reciprocal_rank, model)
    assert served >= 0.7
    assert served >= hashing + 0.2


def test_vectors_are_normalized_and_barely_depend_on_the_batch(model: Embedder) -> None:
    texts = [t for triplet in TRIPLETS[:8] for t in triplet]
    together = model.embed(texts)
    alone = np.vstack([model.embed([t]) for t in texts])
    assert together.shape == (len(texts), 384)
    np.testing.assert_allclose(np.linalg.norm(together, axis=1), 1.0, rtol=1e-4)
    # Dynamic int8 quantization scales each run by its own range, so batch mates move a vector slightly.
    assert float(np.min(np.sum(together * alone, axis=1))) > 0.99
