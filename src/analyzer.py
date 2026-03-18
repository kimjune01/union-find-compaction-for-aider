"""Statistical analysis: McNemar's test, latency percentiles, futility rules."""

from __future__ import annotations

from dataclasses import dataclass

from scipy.stats import chi2

from schemas import Judgment


@dataclass
class McNemarResult:
    chi2: float
    p_value: float
    b: int  # UF correct, recursive wrong
    c: int  # recursive correct, UF wrong


@dataclass
class FutilityResult:
    should_stop: bool
    reason: str


def compute_mcnemar(b: int, c: int) -> McNemarResult:
    """McNemar's test with continuity correction."""
    if b + c == 0:
        return McNemarResult(chi2=0.0, p_value=1.0, b=b, c=c)
    chi2_stat = (abs(b - c) - 1) ** 2 / (b + c)
    p_value = 1.0 - chi2.cdf(chi2_stat, df=1)
    return McNemarResult(chi2=chi2_stat, p_value=p_value, b=b, c=c)


def compute_latency_percentiles(values: list[float]) -> dict[str, float]:
    """Compute p50, p90, p95, max per prereg H2b."""
    if not values:
        return {"p50": 0.0, "p90": 0.0, "p95": 0.0, "max": 0.0}
    import numpy as np
    arr = np.array(values)
    return {
        "p50": float(np.percentile(arr, 50)),
        "p90": float(np.percentile(arr, 90)),
        "p95": float(np.percentile(arr, 95)),
        "max": float(np.max(arr)),
    }


def compute_sign_test(uf_scores: list[float], rec_scores: list[float]) -> dict[str, float]:
    """Conversation-level sign test (prereg H1 sensitivity).

    Each entry is a per-conversation proportion (e.g. 3/8 correct).
    Counts how many conversations UF wins vs Rec wins, ignores ties.
    Uses binomial test under null p=0.5.
    """
    from scipy.stats import binomtest
    wins = 0
    losses = 0
    for uf, rec in zip(uf_scores, rec_scores):
        if uf > rec:
            wins += 1
        elif rec > uf:
            losses += 1
    n = wins + losses
    if n == 0:
        return {"wins": 0, "losses": 0, "ties": len(uf_scores), "p_value": 1.0}
    p_value = float(binomtest(wins, n, 0.5).pvalue)
    return {
        "wins": wins,
        "losses": losses,
        "ties": len(uf_scores) - n,
        "p_value": p_value,
    }


def cost_ratio(cost_a: float, cost_b: float) -> float:
    if cost_b == 0:
        return float("inf")
    return cost_a / cost_b


class ExperimentAnalyzer:
    def analyze_judgments(self, judgments: list[Judgment]) -> McNemarResult:
        """Extract discordant pairs and run McNemar's test."""
        by_question: dict[str, dict[str, bool]] = {}
        for j in judgments:
            if j.question_id not in by_question:
                by_question[j.question_id] = {}
            by_question[j.question_id][j.system] = j.verdict == "CORRECT"

        b = 0  # UF correct, recursive wrong
        c = 0  # recursive correct, UF wrong
        for systems in by_question.values():
            uf = systems.get("union_find", False)
            rec = systems.get("recursive", False)
            if uf and not rec:
                b += 1
            elif rec and not uf:
                c += 1

        return compute_mcnemar(b, c)

    def check_futility(
        self,
        mcnemar: McNemarResult,
        n_conversations: int,
        cost_ratio_val: float,
    ) -> FutilityResult:
        """Check if experiment should stop early (prereg futility rules)."""
        if n_conversations < 12:
            return FutilityResult(should_stop=False, reason="too_early")

        if cost_ratio_val > 3.0:
            return FutilityResult(
                should_stop=True,
                reason=f"Cost ratio {cost_ratio_val:.1f}x exceeds 3x threshold",
            )

        total_discordant = mcnemar.b + mcnemar.c
        if total_discordant > 0:
            effect_pp = abs(mcnemar.b - mcnemar.c) / total_discordant * 100
        else:
            effect_pp = 0.0

        if effect_pp < 2.0 and mcnemar.p_value > 0.50:
            return FutilityResult(
                should_stop=True,
                reason=f"Effect {effect_pp:.1f}pp < 2pp with p={mcnemar.p_value:.3f} > 0.50",
            )

        return FutilityResult(should_stop=False, reason="continue")
