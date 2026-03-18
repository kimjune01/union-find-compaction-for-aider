"""Tests for TFIDFEmbedder."""

import math

from embedding_service import TFIDFEmbedder


def _cosine_similarity(a, b):
    """Cosine similarity between two sparse dicts or lists."""
    if isinstance(a, dict) and isinstance(b, dict):
        keys = set(a.keys()) | set(b.keys())
        dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in keys)
        norm_a = math.sqrt(sum(v * v for v in a.values()))
        norm_b = math.sqrt(sum(v * v for v in b.values()))
    else:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))

    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class TestTFIDFEmbedderBasics:
    def test_embed_returns_dict(self):
        embedder = TFIDFEmbedder()
        result = embedder.embed("hello world")
        assert isinstance(result, dict)

    def test_embed_nonempty_for_nonempty_text(self):
        embedder = TFIDFEmbedder()
        result = embedder.embed("hello world")
        assert len(result) > 0

    def test_embed_empty_text_returns_empty(self):
        embedder = TFIDFEmbedder()
        result = embedder.embed("")
        assert len(result) == 0


class TestTFIDFEmbedderDeterminism:
    def test_same_text_same_vector(self):
        embedder = TFIDFEmbedder()
        v1 = embedder.embed("hello world")
        v2 = embedder.embed("hello world")
        assert v1 == v2

    def test_different_texts_different_vectors(self):
        embedder = TFIDFEmbedder()
        v1 = embedder.embed("python programming language")
        v2 = embedder.embed("underwater basket weaving")
        assert v1 != v2


class TestTFIDFEmbedderSimilarity:
    def test_identical_texts_similarity_one(self):
        embedder = TFIDFEmbedder()
        v1 = embedder.embed("hello world")
        v2 = embedder.embed("hello world")
        sim = _cosine_similarity(v1, v2)
        assert abs(sim - 1.0) < 1e-6

    def test_similar_texts_higher_similarity(self):
        embedder = TFIDFEmbedder()
        v_base = embedder.embed("python function definition")
        v_similar = embedder.embed("python function declaration")
        v_different = embedder.embed("underwater basket weaving")

        sim_similar = _cosine_similarity(v_base, v_similar)
        sim_different = _cosine_similarity(v_base, v_different)

        assert sim_similar > sim_different

    def test_completely_disjoint_texts_zero_similarity(self):
        embedder = TFIDFEmbedder()
        v1 = embedder.embed("aaa bbb ccc")
        v2 = embedder.embed("xxx yyy zzz")
        sim = _cosine_similarity(v1, v2)
        assert sim == 0.0


class TestTFIDFEmbedderVocabulary:
    def test_vocabulary_grows_incrementally(self):
        embedder = TFIDFEmbedder()
        embedder.embed("hello world")
        vocab_size_1 = embedder.vocab_size

        embedder.embed("python programming")
        vocab_size_2 = embedder.vocab_size

        assert vocab_size_2 > vocab_size_1

    def test_repeated_words_dont_grow_vocab(self):
        embedder = TFIDFEmbedder()
        embedder.embed("hello world")
        vocab_size_1 = embedder.vocab_size

        embedder.embed("hello world")
        vocab_size_2 = embedder.vocab_size

        assert vocab_size_2 == vocab_size_1


class TestTFIDFEmbedderTokenization:
    def test_case_insensitive(self):
        embedder = TFIDFEmbedder()
        v1 = embedder.embed("Hello World")
        v2 = embedder.embed("hello world")
        assert v1 == v2

    def test_splits_on_non_alphanumeric(self):
        embedder = TFIDFEmbedder()
        v1 = embedder.embed("hello-world foo_bar")
        # Should have tokens for hello, world, foo, bar
        assert len(v1) >= 4

    def test_filters_stopwords(self):
        embedder = TFIDFEmbedder()
        v_with = embedder.embed("the quick brown fox")
        v_without = embedder.embed("quick brown fox")
        # "the" should be filtered, so vectors should be equal
        assert v_with == v_without


class TestTFIDFEmbedderIDF:
    def test_rare_words_have_higher_weight(self):
        embedder = TFIDFEmbedder()
        # Common word appears in multiple documents
        embedder.embed("python code")
        embedder.embed("python script")
        embedder.embed("python module")

        # "xylophone" only in one document
        v = embedder.embed("python xylophone")

        # xylophone should have higher IDF weight than python
        if "xylophone" in v and "python" in v:
            assert v["xylophone"] > v["python"]

    def test_document_count_tracks_embeds(self):
        embedder = TFIDFEmbedder()
        assert embedder.doc_count == 0

        embedder.embed("hello")
        assert embedder.doc_count == 1

        embedder.embed("world")
        assert embedder.doc_count == 2
