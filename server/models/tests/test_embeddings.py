import numpy as np

from niadra_models.rules.embeddings import HashingEmbedder


def cos(embedder: HashingEmbedder, a: str, b: str) -> float:
    va, vb = embedder.embed([a, b])
    return float(va @ vb)


def test_vectors_are_normalized_with_the_configured_dimension() -> None:
    vectors = HashingEmbedder().embed(["meu pedido atrasou", "outra coisa", ""])
    assert vectors.shape == (3, 384)
    assert vectors.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(vectors[:2], axis=1), 1.0, rtol=1e-5)
    assert not vectors[2].any()


def test_similar_texts_are_closer_than_unrelated_ones() -> None:
    e = HashingEmbedder()
    anchor = "Meu pedido não chegou e quero reembolso"
    close = cos(e, anchor, "o pedido nao chegou, quero o reembolso")
    typo = cos(e, anchor, "meu pedidoo nao chegou quero reembolço")
    far = cos(e, anchor, "Qual o horário de funcionamento da loja?")
    assert close > 0.7
    assert typo > 0.5
    assert far < 0.25
    assert min(close, typo) > far


def test_vectors_are_stable_across_instances_and_calls() -> None:
    a = HashingEmbedder().embed(["mesmo texto"])
    b = HashingEmbedder().embed(["mesmo texto"])
    np.testing.assert_array_equal(a, b)


def test_accents_and_case_do_not_change_the_vector() -> None:
    e = HashingEmbedder()
    assert cos(e, "Ação São João", "acao sao joao") > 0.999
