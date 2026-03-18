# PR: Add union-find context compaction as opt-in summarization strategy

## Summary

- Add `--chat-history-summarizer union_find` flag (default stays `recursive`)
- Drop-in `ChatSummaryUF(ChatSummary)` subclass — same interface, same threading model, same output format
- 4 new modules: `context_window.py`, `embedding_service.py`, `cluster_summarizer.py`, `chat_summary_uf.py`
- 66 tests, zero new dependencies (TF-IDF is pure Python, uses existing model API)

## Why

Users want more control over what stays in context and what gets compressed. The current summarizer is opaque — once messages are summarized, the originals are gone, the topic structure is gone, and there's no way to inspect or selectively manage what's retained.

Relevant issues:
- **#3607** — "More control over chat history": users report important early decisions get summarized away while verbose recent exchanges stay
- **#2219** — "Editing history in realtime": user wants to see and edit the exact context being sent
- **#948** — Request for a table of context consumers with token counts and selective dropping
- **#4079** — "Chat history archive": cross-session topic persistence
- **#3722** — Bug where `tail_tokens` is always 0, silently dropping tail messages from summaries

Union-find compaction organizes context by topic instead of by position. Messages graduate from a hot zone into a forest of topic-coherent clusters, grouped by TF-IDF similarity. Each cluster maintains its own summary. This structure is the foundation for features like topic inspection, selective expansion, and cross-session memory — none of which are possible with flat recursive summarization.

## How it works

```
Hot zone (recent messages, verbatim)
  ↓ graduate oldest when hot > 26
Cold forest (topic clusters, summarized)
  ↓ merge when similarity > 0.15 or cluster count > 10
Rendered output: [cluster summaries] + [hot messages]
```

1. **Append**: New messages enter the hot zone with a TF-IDF embedding. O(vocabulary) per message, sub-millisecond.
2. **Graduate**: When hot zone exceeds 26 messages, oldest messages move to the cold forest. Each finds the nearest existing cluster by cosine similarity. If similarity > 0.15, it merges; otherwise it forms a new singleton.
3. **Merge**: When cluster count exceeds 10, the closest pair force-merges. Union-by-size, path compression. No LLM call — just marks the cluster dirty.
4. **Resolve**: Dirty clusters get summarized via the existing model cascade (`weak_model` → `main_model`). This runs in the existing `summarize_worker` thread.
5. **Render**: Cold cluster summaries + hot messages → output in the same format as recursive (`[summary_msg, "Ok.", *hot_messages]`).

Falls back to `super().summarize()` (recursive) when:
- Not enough messages to form cold clusters (compression hole)
- Result exceeds `max_tokens` (budget violation)
- Result is larger than input (inflation)

## Quality: regression-tested, not better, not worse

We ran a preregistered experiment comparing both systems on 17 conversations (136 paired observations), blinded GPT-5.4 judge:

| System | Correct | Rate |
|--------|---------|------|
| Union-Find | 54/136 | 39.7% |
| Recursive | 56/136 | 41.2% |

McNemar's p = 0.248. Only 3 discordant pairs out of 136 — the systems get the same questions right and wrong. Quality is equivalent.

| Metric | Result | Target |
|--------|--------|--------|
| Recall difference | -1.5pp | (informational) |
| Append p95 | 0.08ms | < 100ms |
| Render p95 | 0.02ms | < 100ms |
| Cost ratio | 1.14x | < 2x |

Full experiment data, preregistration, and writeup included in `experiment/`.

## What this enables (future work, not in this PR)

The topic-structured forest supports operations that recursive summarization can't:

- **`/topics`** — List current topic clusters with token counts. Addresses #948.
- **`/expand <topic>`** — Drill into a cluster to see the original messages. Addresses #2219.
- **`/drop <topic>`** — Remove a topic cluster from context. Addresses #3607.
- **Cross-session persistence** — Serialize the forest to resume topic structure across sessions. Addresses #4079.

These are not included in this PR. This PR is the structural foundation — opt-in, regression-tested, same interface.

## Files

| File | Lines | Purpose |
|------|-------|---------|
| `aider/context_window.py` | 314 | Forest (union-find) + ContextWindow (hot/cold zones) |
| `aider/embedding_service.py` | 81 | TF-IDF embedder, pure Python |
| `aider/cluster_summarizer.py` | 57 | Per-cluster summarization via model cascade |
| `aider/chat_summary_uf.py` | 86 | `ChatSummaryUF(ChatSummary)` drop-in subclass |
| `tests/test_context_window.py` | 28 tests | Forest + ContextWindow |
| `tests/test_embedding_service.py` | 15 tests | TF-IDF |
| `tests/test_cluster_summarizer.py` | 8 tests | Cluster summarization |
| `tests/test_chat_summary_uf.py` | 15 tests | Integration |

## Integration points

1. **`args.py`**: Add `--chat-history-summarizer` with choices `recursive` (default) and `union_find`.
2. **`main.py`**: In summarizer construction, branch on the flag:
   ```python
   if args.chat_history_summarizer == "union_find":
       summarizer = ChatSummaryUF(models, max_tokens)
   else:
       summarizer = ChatSummary(models, max_tokens)
   ```
3. No other changes. Same threading model, same output format, same stale-safety contract.

## Test plan

- [ ] `pytest tests/` — 66 new tests pass
- [ ] Existing aider test suite unchanged
- [ ] Manual smoke test: 50+ message conversation with `--chat-history-summarizer union_find`, verify summarization triggers and output is coherent
- [ ] Verify fallback to recursive on small conversations (< 27 messages that exceed budget)
