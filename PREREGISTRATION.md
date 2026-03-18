# Preregistration: Union-Find Context Compaction for Aider

**Date:** 2026-03-18
**Author:** June Kim (kimjune01)
**Witness:** Claude Opus 4.6 (noreply@anthropic.com)
**Status:** Preregistered (before implementation)
**Classification:** Exploratory benchmark validation
**Lineage:** Architecture ported from gemini-cli v2 (validated there: 0.79x cost, 0.3ms latency, +8.3pp recall trend). Parameters carried, not independently derived.

---

## Why Exploratory

This is classified exploratory from the start because:
- The architecture was designed and validated on gemini-cli before being ported
- Parameters (`graduate_at`, `evict_at`, `merge_threshold`) are carried from gemini-cli, not derived from aider data
- Aider's baseline is stronger than gemini-cli's (hierarchical recursive vs flat single-snapshot) — the gap may be smaller or nonexistent

Lesson from gemini-cli: v1 was registered as confirmatory, failed, required reclassification. Starting exploratory avoids that. Lesson from metacognition research: pre-registration keeps you honest even when results surprise you.

---

## Research Question

**Does union-find context compaction improve aider's chat history summarization compared to hierarchical recursive summarization?**

Three exploratory hypotheses test recall, latency, and cost. Results are benchmark observations, not confirmatory findings.

---

## Experimental Setup

**Implementation:** TDD in `src/`, tested with pytest before experiment
**Evaluation model:** To be frozen before first trial (record exact model ID)
**Evaluation data:** 24 conversations from GitHub issues in Python-heavy repos (django, flask, fastapi, pytorch + repos used in gemini-cli experiment). 200 messages per conversation, 8 recall questions each = 192 paired observations.
**Power rationale:** Gemini-cli had 96 pairs, underpowered at p=0.136 for a +8.3pp effect. 192 pairs provides ~80% power for a 5pp effect at alpha=0.05 (McNemar's).
**Baseline:** Recursive summarization rerun contemporaneously in same environment (not reusing any prior numbers).
**Evaluation freeze:** All hypotheses evaluated on a single tagged git commit.

**Conversation selection protocol:**
1. Select 24 GitHub issues with ≥15 comments from target repos
2. Selection blind to summarization performance (select before running either system)
3. Expand each into a 200-message simulated conversation
4. Generate 8 factual recall questions per conversation (questions frozen before running systems)

**Judge protocol:**
- Blinded LLM-as-judge (judge sees compressed history + question, not which system produced it)
- Binary scoring: correct/incorrect
- Dual-model judging if feasible (two models, majority vote) — learned from metacognition Round 3

---

## Hypotheses

All hypotheses are **exploratory**. Results reported as benchmark observations.

### H1: Recall

Union-find recall on 24 coding conversations.

**Method:** Both systems compress the same 200-message conversations. 8 recall questions per conversation scored by blinded judge.
**Test:** McNemar's on 192 paired binary outcomes (p<0.05)
**Benchmark target:** Union-find recall ≥ recursive + 5pp
**If target missed:** Report effect size, p-value, and CI regardless. A positive trend without significance is still informative.
**Sensitivity:** Conversation-level sign test (24 paired proportions) as secondary check.

### H2: Latency

**H2a: Append + render p95 < 100ms.** Both are synchronous (no LLM calls). Validates the non-blocking architecture claim.

**H2b: `resolveDirty()` latency reported (no pass/fail target).** This is where LLM work happens. Report p50, p90, p95, max. In aider's model, `resolveDirty()` runs inside the worker thread — the user doesn't block unless `summarize_end()` joins before completion.

**Honest latency reporting:** H2b prevents hiding latency by deferring it. The user-facing latency depends on whether `resolveDirty()` finishes before the user's next message, which depends on typing speed — not something we can benchmark. We report both numbers and let readers judge.

### H3: Cost

Union-find total token cost ≤ 2x recursive over same conversations.

**Method:** Count ALL tokens from ALL LLM calls across full conversation lifecycle.
**Benchmark target:** Union-find cost ≤ 2x recursive
**Note:** Union-find makes many small calls vs recursive's few large calls. Total tokens may be comparable. The 2x threshold allows for overhead while still being economically viable.

---

## Tuning Policy

If a hypothesis misses its target on first measurement, **max 2 parameter changes** allowed. Architectural changes require a new preregistration.

| Hypothesis | Change 1 | Change 2 | Then |
|---|---|---|---|
| H1 (Recall) | Merge threshold (0.15 → {0.10, 0.20}) | Max clusters (10 → {8, 15}) | Accept result |
| H2a (Latency) | graduate_at/evict_at | Max cluster count | Accept result |
| H3 (Cost) | Cluster limit | Summary prompt (shorter) | Accept result |

**Claim strength ladder:**
- 0 changes: "Benchmark-supported"
- 1-2 changes: "Benchmark-supported after tuning"
- Architectural change: Requires new preregistration
- Still failing: "Not supported"

---

## Futility Rules

Learned from metacognition Round 3 (oscillated 0.90-0.94 for 17 batches wasting compute).

- **H1:** If after 12 conversations (96 pairs, 50% of data), McNemar p > 0.50 and effect size < 2pp, stop and report as "no detectable difference."
- **H3:** If after 12 conversations, cost ratio > 3x, stop and report.
- **General:** If any hypothesis requires architectural change to recover, stop that hypothesis and accept the result.

---

## Decision Rules

| Outcome | Action |
|---|---|
| H1 ✅ H2a ✅ H3 ✅ | Open PR on aider with evidence |
| H1 ❌ H2a ✅ H3 ✅ | Document; note aider's stronger baseline may close the gap |
| H1 ✅ H2a ❌ | Investigate — likely implementation bug (append/render shouldn't block) |
| H1 ✅ H3 ❌ | Document as premium feature (better recall at higher cost) |
| Multiple ❌ | Recommend staying with recursive; document lessons |

**Stop if:** H1 AND H2a both miss after tuning, OR cost > 3x after tuning.

---

## Data Storage

```
experiment/
├── conversations/           # 24 source conversations (frozen)
│   └── questions/           # 8 questions each (frozen)
├── quality/                 # H1
│   ├── recursive-results.json
│   ├── union-find-results.json
│   └── analysis.md
├── performance/             # H2a + H2b
│   ├── append-latencies.csv
│   ├── render-latencies.csv
│   ├── resolve-dirty-latencies.csv
│   ├── environment.md
│   └── analysis.md
├── cost/                    # H3
│   ├── recursive-tokens.json
│   ├── union-find-tokens.json
│   └── analysis.md
└── RESULTS.md               # Summary with all hypothesis outcomes
```

---

## Commitment

1. **Report all outcomes** — success and failure
2. **Follow preregistered criteria** — no post-hoc changes to hypotheses
3. **Acknowledge exploratory status** — benchmark validation, not confirmation
4. **No HARKing** — hypotheses frozen before implementation
5. **Contemporaneous baselines** — rerun recursive in same environment
6. **Honest latency reporting** — H2b prevents hiding deferred work
7. **Futility rules** — stop early on hopeless hypotheses, don't waste compute
8. **Effect size matters** — report effect sizes and CIs, not just p-values

---

**Do not modify hypotheses after implementation begins.**
