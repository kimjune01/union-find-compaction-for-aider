# Experiment Results

**Date:** 2026-03-18 10:22
**Model:** gemini/gemini-3.1-flash-lite-preview
**Classification:** Exploratory benchmark validation
**Conversations:** 17
**Paired observations:** 136

---

## H1: Recall

| System | Correct | Total | Rate |
|--------|---------|-------|------|
| Union-Find | 54 | 136 | 39.7% |
| Recursive | 56 | 136 | 41.2% |

**Effect:** -1.5pp
**McNemar's chi2:** 1.3333
**p-value:** 0.2482
**Discordant pairs:** b=3 (UF correct, Rec wrong), c=0 (Rec correct, UF wrong)
**Result:** MISS

### Sensitivity: Conversation-level sign test

| UF wins | Rec wins | Ties | p-value |
|---------|----------|------|---------|
| 5 | 7 | 5 | 0.7744 |

## H2: Latency

### H2a: Append + Render (target: p95 < 100ms)

| Operation | p50 | p90 | p95 | max |
|-----------|-----|-----|-----|-----|
| Append | 0.03ms | 0.06ms | 0.08ms | 0.19ms |
| Render | 0.01ms | 0.02ms | 0.02ms | 0.03ms |

**Result:** PASS

### H2b: ResolveDirty (informational, no pass/fail target)

| p50 | p90 | p95 | max |
|-----|-----|-----|-----|
| 1523.61ms | 2475.78ms | 4151.18ms | 8571.12ms |

## H3: Cost (target: UF <= 2x recursive)

| System | Total Tokens |
|--------|-------------|
| Union-Find | 382348 |
| Recursive | 335232 |

**Ratio:** 1.14x
**Result:** PASS

---

## Decision

| Hypothesis | Target | Result | Outcome |
|------------|--------|--------|---------|
| H1 Recall | >= +5pp, p<0.05 | -1.5pp, p=0.2482 | MISS |
| H2a Latency | p95 < 100ms | append=0.08ms, render=0.02ms | PASS |
| H3 Cost | <= 2x | 1.14x | PASS |
