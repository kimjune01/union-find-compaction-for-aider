"""Tests for statistical analysis: McNemar's test, futility, latency stats."""

from analyzer import (
    ExperimentAnalyzer, McNemarResult,
    compute_latency_percentiles, compute_mcnemar, compute_sign_test, cost_ratio,
)
from schemas import Judgment


class TestMcNemar:
    def test_known_effect(self):
        result = compute_mcnemar(b=30, c=10)
        assert result.p_value < 0.005

    def test_perfect_agreement(self):
        result = compute_mcnemar(b=0, c=0)
        assert result.p_value == 1.0

    def test_symmetric_discordance(self):
        result = compute_mcnemar(b=20, c=20)
        assert result.p_value > 0.5

    def test_single_discordant(self):
        result = compute_mcnemar(b=1, c=0)
        assert result.p_value > 0.05

    def test_continuity_correction(self):
        result = compute_mcnemar(b=10, c=5)
        expected = (abs(10 - 5) - 1) ** 2 / (10 + 5)
        assert abs(result.chi2 - expected) < 1e-10

    def test_returns_b_c(self):
        result = compute_mcnemar(b=30, c=10)
        assert result.b == 30 and result.c == 10


class TestLatencyPercentiles:
    def test_basic(self):
        p = compute_latency_percentiles(list(range(1, 101)))
        assert p["p50"] == 50.5
        assert p["max"] == 100.0

    def test_has_p90(self):
        p = compute_latency_percentiles(list(range(1, 101)))
        assert "p90" in p

    def test_empty(self):
        p = compute_latency_percentiles([])
        assert p["p50"] == 0.0 and p["max"] == 0.0

    def test_single(self):
        assert compute_latency_percentiles([42.0])["p50"] == 42.0


class TestSignTest:
    def test_uf_dominant(self):
        result = compute_sign_test([0.75, 0.5, 0.625, 0.875], [0.25, 0.25, 0.25, 0.25])
        assert result["wins"] == 4 and result["losses"] == 0

    def test_tied(self):
        result = compute_sign_test([0.5, 0.5], [0.5, 0.5])
        assert result["ties"] == 2 and result["p_value"] == 1.0

    def test_mixed(self):
        result = compute_sign_test([0.75, 0.25, 0.5], [0.25, 0.75, 0.5])
        assert result["wins"] == 1 and result["losses"] == 1 and result["ties"] == 1


class TestCostRatio:
    def test_basic(self):
        assert cost_ratio(100, 200) == 0.5

    def test_zero_denominator(self):
        assert cost_ratio(100, 0) == float("inf")


class TestExperimentAnalyzer:
    def _make_judgments(self, uf_correct, rec_correct):
        judgments = []
        for i, (uf, rec) in enumerate(zip(uf_correct, rec_correct)):
            qid = f"q{i}"
            cid = f"conv-{i // 8}"
            judgments.append(Judgment(cid, qid, "union_find", "CORRECT" if uf else "INCORRECT", ""))
            judgments.append(Judgment(cid, qid, "recursive", "CORRECT" if rec else "INCORRECT", ""))
        return judgments

    def test_analyze_judgments(self):
        uf = [True] * 30 + [False] * 10 + [True] * 60
        rec = [False] * 30 + [True] * 10 + [True] * 60
        result = ExperimentAnalyzer().analyze_judgments(self._make_judgments(uf, rec))
        assert result.p_value < 0.005 and result.b == 30

    def test_futility_too_early(self):
        result = ExperimentAnalyzer().check_futility(
            McNemarResult(0.1, 0.8, 5, 4), n_conversations=6, cost_ratio_val=1.1)
        assert not result.should_stop

    def test_futility_no_effect(self):
        result = ExperimentAnalyzer().check_futility(
            McNemarResult(0.0, 0.99, 50, 50), n_conversations=12, cost_ratio_val=1.1)
        assert result.should_stop

    def test_futility_high_cost(self):
        result = ExperimentAnalyzer().check_futility(
            McNemarResult(5.0, 0.02, 20, 10), n_conversations=12, cost_ratio_val=3.5)
        assert result.should_stop

    def test_futility_continues(self):
        result = ExperimentAnalyzer().check_futility(
            McNemarResult(10.0, 0.001, 30, 10), n_conversations=12, cost_ratio_val=1.5)
        assert not result.should_stop
