# Add opt-in union-find chat history summarizer

## Summary

- Adds `--chat-history-summarizer union-find` flag — opt-in alternative to the default recursive summarizer
- Groups messages into topic clusters by TF-IDF similarity, summarizes each cluster independently
- Produces the same output format, uses the same model cascade, falls back to recursive if output exceeds budget
- Default behavior completely unchanged — users who don't pass the flag get exactly the current system
- Benchmarked as quality-equivalent (136 paired observations, McNemar p=0.248, 1.14× cost)
- No new dependencies (pure Python TF-IDF embedder)

## Test plan

- [x] 49 new tests passing (`tests/basic/test_chat_summary_uf.py`)
- [x] 523 existing tests passing, zero regressions

## Why

The current recursive summarizer compresses chat history into a single opaque text blob. It works well for staying within budget, but it's a one-way door: original messages are discarded, there's no provenance, and no way to selectively remove one stale topic without re-summarizing everything. Users notice this:

- #3607 — selective history control ("I want to drop the debugging context but keep the refactor decisions")
- #2219 — see/edit context ("I want to see what the model's context actually contains")
- #948 — token breakdown with actions ("show me what's consuming tokens and let me act on it")

These issues share a root cause: the summarizer destroys structure. You can't inspect, search, or selectively drop topics that don't exist as addressable units.

Union-find compaction groups messages into topic-coherent clusters and summarizes each one independently. Every summary traces back to its source messages. This makes structured operations possible — inspecting topics, dropping one, branching context — without changing how the rest of aider works.

This PR doesn't add those operations. It adds the backend that makes them possible. User-facing commands (`/topics`, `/drop-topic`) come in a follow-up PR once the foundation is reviewed.

## Background

The algorithm was [prototyped against gemini-cli](https://june.kim/union-find-compaction), where it showed a +8–18pp recall advantage over flat summarization across 7 trials (1 significant at p=0.039, rest directional). It was then ported to aider for validation against aider's stronger recursive baseline. Full experiment methodology, preregistration, and data are in the [research repo](https://github.com/kimjune01/union-find-compaction-for-aider).

## What changes

**New files** (549 lines of production code, 689 lines of tests):

| File | Lines | What it does |
|------|------:|--------------|
| `aider/context_window.py` | 328 | `Forest` (union-find cluster store with stable ordering and weighted centroids) + `ContextWindow` (hot/cold zones with graduation and eviction) |
| `aider/embedding_service.py` | 80 | `TFIDFEmbedder` — pure Python, incremental vocabulary, no external dependencies |
| `aider/cluster_summarizer.py` | 56 | Per-cluster summarization via existing model cascade (`simple_send_with_retries`) |
| `aider/chat_summary_uf.py` | 85 | `ChatSummaryUF(ChatSummary)` — drop-in subclass with incremental feeding and mandatory fallback |
| `tests/basic/test_chat_summary_uf.py` | 689 | 49 tests covering 12 areas (see table below) |

**Modified files** (15 lines changed):

| File | Change |
|------|--------|
| `aider/args.py` | `--chat-history-summarizer` argument (default `recursive`, choices `[recursive, union-find]`) |
| `aider/main.py` | Conditional at summarizer construction — `ChatSummaryUF` when `union-find`, `ChatSummary` otherwise |

## What doesn't change

- Default summarization (recursive, unchanged)
- Threading model (`summarize_start`/`summarize_worker`/`summarize_end`)
- Output format (`[summary_msg, "Ok.", *hot_messages]`)
- `summarize_all()` behavior (delegates to parent)
- Existing commands (`/clear`, `/drop`, `/tokens`, `/reset`)
- Existing tests (no modifications, all 523 still passing)

## How it works

Messages flow through a hot zone and a cold forest:

1. Each user/assistant message is TF-IDF-embedded and pushed to the hot zone
2. When the hot zone exceeds `graduate_at` (26 messages), the oldest graduates to the forest
3. Graduated messages merge with the nearest existing cluster if cosine similarity ≥ 0.15, or form a new singleton
4. If cluster count exceeds `max_cold_clusters` (10), the closest pair is force-merged
5. Dirty clusters (merged but not yet summarized) are summarized via the model cascade
6. `render()` returns cold summaries + hot contents, formatted as `[summary_msg, "Ok.", *hot_messages]`

The overlap window (graduate_at=26, evict_at=30) gives `resolve_dirty()` time to summarize before eviction.

## Safety

1. **Mandatory fallback.** If the union-find result exceeds `max_tokens` or is ≥ input tokens, falls back to `super().summarize()` (recursive). The union-find path can never produce a worse result than the existing system.
2. **Stale-safety preserved.** `summarize_end()` stale check works identically. The `_fed_count` mechanism triggers a full forest rebuild when `done_messages` changes (shrinks, is cleared, or is replaced by a previous summarization result).
3. **Same tokenizer.** Inherits `self.token_count = self.models[0].token_count` from `ChatSummary.__init__()`.
4. **Stable root ordering.** `roots()` returns clusters in insertion order via a tracked `_root_order` list, ensuring deterministic `render()` output across calls.
5. **Weighted centroid averaging.** `union()` weights centroids by cluster size (`emb * size / total`), preventing small clusters from distorting large ones over repeated merges.
6. **No new dependencies.** TF-IDF embedder is pure Python. No numpy, no scipy, no API calls.

## Test coverage

49 tests in `tests/basic/test_chat_summary_uf.py`, organized by area:

| Area | Tests | What's verified |
|------|------:|-----------------|
| Forest mechanics | 12 | Insert, union, roots, compact, nearest_root, dirty tracking, path compression, dirty input collection |
| Stable root ordering | 4 | Insertion order preserved, order after merge, deterministic across calls, multiple merges |
| Weighted centroid | 3 | Equal-size midpoint, unequal-size weights toward larger, sparse dict weighting |
| Cluster summarization | 4 | First model success, fallback to second model, all fail raises ValueError, single model (not list) |
| Flag selection | 3 | Subclass relationship, ChatSummaryUF constructs correctly, default is ChatSummary (not UF) |
| Output format | 2 | Not-too-big returns unchanged, summary + "Ok." + hot_messages format |
| Fallback to recursive | 2 | Result exceeds max_tokens, not enough messages for graduation |
| `summarize_all()` parity | 1 | Delegates to parent, same output format |
| Stale discard + rebuild | 2 | `_fed_count` shrink triggers `_init_context_window()`, incremental feeding tracks correctly |
| Incremental feeding | 2 | Skips non-user/assistant roles, empty content not fed |
| Low token budget | 1 | Graceful fallback without crash |
| TF-IDF embedder | 7 | Sparse dict output, empty string, stopwords filtered, vocabulary growth, doc count, cosine similarity (high for similar, low for different) |
| ContextWindow integration | 6 | Append/render, hot count tracking, graduation to forest, force merge, cold+hot render, dirty resolution |

## Benchmark

Tested on 17 real aider conversations (136 paired observation points where both backends triggered summarization):

| Metric | Value |
|--------|-------|
| McNemar's test | p = 0.248 (no significant difference in recall) |
| Cost ratio | 1.14× (union-find uses slightly more tokens due to per-cluster prompts) |
| Latency overhead | Sub-millisecond (TF-IDF embedding + union-find operations; LLM calls dominate) |

The union-find backend produces quality-equivalent summaries. The value proposition is not "better summaries" — it's "structured context that enables visibility and selective control."

## Follow-up (not in this PR)

- `/topics` — read-only command showing topic clusters with token counts and previews
- `/drop-topic N` — selective topic removal with `done_messages` sync and threading guard
- Cross-session topic persistence (#4079)
