# Design Decisions

Organized by regression risk. Each decision either prevents a regression or was inherited from gemini-cli with an explicit rationale for why it transfers.

---

## Regression Prevention (PR 1)

These decisions minimize the risk of the new code making anything worse.

### 1. Default unchanged — opt-in only

`--chat-history-summarizer` defaults to `recursive`. Users who don't pass the flag get exactly the current behavior. No code path changes for the default case.

**Regression surface:** Minimal. The flag selects a different constructor at one site in `main.py`. The existing `ChatSummary` class is untouched.

### 2. Mandatory fallback to recursive

After union-find renders its output, two checks:
1. Result tokens > `max_tokens` → fall back to `super().summarize()`
2. Result tokens >= input tokens → fall back to `super().summarize()`

Union-find never produces a result larger than what recursive would have produced. If it somehow does, it hands off to recursive. The fallback is the existing system.

**Why both checks:** Check 1 enforces the budget. Check 2 prevents the pathological case where union-find "compresses" to more tokens than the input (possible if cluster summaries are verbose).

### 3. Output format matches exactly

`summarize()` returns `[summary_msg, "Ok.", *hot_messages]` where `summary_msg` is a user message starting with `prompts.summary_prefix`. This is the same structure recursive produces.

**Why this matters:** `summarize_end()` assigns the return value directly to `self.done_messages`. The coder later includes `done_messages` verbatim in the prompt. The format must be a valid alternating user/assistant message list. Separate user messages per cluster was rejected for this reason.

### 4. `summarize_all()` delegates to parent

`summarize_all()` is called during edit format transitions (e.g., architect → code mode). It expects a single compact blob, not structured clusters. `ChatSummaryUF.summarize_all()` calls `super().summarize_all()` — recursive handles it.

**Regression surface:** Minimal — the code path is identical to current behavior.

### 5. Stale-safety preserved

`summarize_end()` compares `self.summarizing_messages == self.done_messages` by value equality. If messages changed during summarization, the result is discarded. Union-find must tolerate this.

**How:** `ChatSummaryUF` snapshots `_fed_count` state. If the result is discarded and `done_messages` grew, the next `summarize()` call feeds only the delta (`messages[_fed_count:]`). If `done_messages` shrank (previous result applied, or `/drop-topic` rewrote it), `_fed_count > len(messages)` triggers a full forest rebuild.

**Equal-length replacement:** In theory, `done_messages` could be replaced with a same-length list that `_fed_count` doesn't detect. In practice, this doesn't happen — aider modifies `done_messages` by appending (grows), applying summary (shrinks), `/clear` (empties), or `/drop-topic` (shrinks). All of these trigger rebuild via `_fed_count`. The `summarize_end()` stale check provides an additional safety net by discarding results when `done_messages` changed during summarization.

### 6. Threading contract unchanged

No changes to `summarize_start()`, `summarize_worker()`, or `summarize_end()`. `ChatSummaryUF.summarize()` is called inside `summarize_worker()` on the background thread, same as `ChatSummary.summarize()`. The thread lifecycle, join behavior, and stale check are all existing code.

### 7. Token counting uses same method

`ChatSummaryUF` inherits `self.token_count = self.models[0].token_count` from `ChatSummary.__init__()`. All budget calculations use the same tokenizer as recursive.

### 8. Model cascade preserved

`ClusterSummarizer` receives the same `[weak_model, main_model]` list. Tries weak_model first, falls back to main_model. Same error handling (`ValueError` if all fail). Same `simple_send_with_retries()` call.

### 9. Existing commands unaffected

`/clear`, `/drop`, `/tokens`, `/reset` are unchanged. `/topics` and `/drop-topic` are new methods added to `commands.py`. Aider's command dispatch uses `getattr()` — new methods don't interfere with existing ones.

### 10. No new dependencies

TF-IDF embedder is pure Python. No `numpy`, no `scipy`, no external packages. The import graph adds only internal modules (`aider.context_window`, `aider.embedding_service`, `aider.cluster_summarizer`, `aider.chat_summary_uf`).

### 11. Stable root ordering

`roots()` returns roots in insertion order via `_root_order` list, not `list(set(...))`.

**Why this matters for PR 1:** Deterministic rendering depends on deterministic root ordering. `render()` iterates `roots()` to build the summary blob. Nondeterministic ordering would produce different `done_messages` on each call, even with identical forest state. This is a backend correctness issue independent of `/topics`.

**What "stable" means:** Same order on repeated calls. After a merge, the surviving root keeps its position; the absorbed root is removed. After a `remove_cluster()`, remaining roots shift down but maintain relative order.

### 12. Weighted centroid averaging

`union()` weights centroid by cluster size: `(emb_a * size_a + emb_b * size_b) / (size_a + size_b)`.

**Why:** Equal-weight averaging lets a 1-message cluster pull a 50-message cluster's centroid halfway. Over repeated merges, this distorts cluster identity and degrades merge quality. Weighted averaging preserves the larger cluster's semantic position.

**Why PR 1:** This is a clustering correctness fix, not a UX feature. Wrong centroids mean wrong merges, which means worse summaries — regardless of whether `/topics` exists.

---

## `/topics` and `/drop-topic` Safety (PR 2)

These decisions prevent the new commands from introducing bugs.

### 13. Source of truth: `done_messages`, not the forest

`done_messages` is what aider sends to the model. The forest is a shadow structure providing topic visibility and selective drop. Both are updated in lockstep.

**Why this matters:** If `/drop-topic` only mutates the forest, the dropped topic stays in `done_messages` and the model still sees it. Worse: if the drop shrinks history enough that `too_big()` returns false, re-summarization never runs, and the drop never materializes.

**How:** `/drop-topic` removes the cluster from the forest, then immediately re-renders and assigns the result to `self.coder.done_messages`. Same pattern as `/clear` (which sets `done_messages = []`).

**Forest rebuild is automatic:** After `/drop-topic` rewrites `done_messages`, `_fed_count > len(messages)` on the next `summarize()` call. This triggers a full forest rebuild from the new `done_messages` — which no longer contains the dropped topic. This is the same mechanism that fires after every successful summarization.

### 14. Threading guard on both commands

Both `cmd_topics` and `cmd_drop_topic` check `self.coder.summarizer_thread is not None`. If a thread is running, refuse with "try again in a moment."

**Why both, not just `/drop-topic`:** The forest isn't designed for concurrent access. `cmd_topics` reads `roots()`, `compact()`, hot zone state. If the background summarizer is mutating these structures, reads can race or return inconsistent data.

**Why not join the thread:** Joining blocks the main thread. The user would freeze until summarization finishes. Refusing is instant.

**Why not use a lock:** A lock would need to wrap every forest read in `summarize_worker` too. Overengineered for a situation that lasts seconds and happens infrequently.

### 15. `remove_cluster` cleans all data structures

`remove_cluster(root_id)` removes from: `_parent`, `_content`, `_embedding`, `_summary`, `_dirty`, `_dirty_inputs`, `_children`, `_root_order`. Returns removed node IDs.

**Why exhaustive:** A partial removal leaves orphaned entries. If `_dirty` still references a removed root, `resolve_dirty()` would try to summarize a nonexistent cluster. If `_root_order` still lists it, `roots()` would include a ghost.

---

## Inherited from gemini-cli (PR 1, with rationale)

These values come from gemini-cli v2, where they were validated experimentally. They transfer because the underlying mechanics (TF-IDF similarity, union-find merging, hot/cold zones) are the same.

### 16. `graduate_at = 26`, `evict_at = 30`

Overlap window of 4 messages (~2 turns). Messages exist in both hot and cold for this window, giving `resolve_dirty()` time to run before eviction.

**Heuristic, not guarantee.** In gemini-cli, dirty resolution ran during the main LLM call (5-30s). In aider, it runs inside `summarize_worker` on a background thread — timing depends on user think time, which is less predictable. The overlap window is generous enough for the common case but is a tuning parameter, not an invariant. If summaries are stale at eviction, the fallback is showing raw content (safe, just verbose).

### 17. `max_cold_clusters = 10`

Cap on cluster count. Validated in 5+ experimental trials in gemini-cli.

**Heuristic, not guarantee.** 10 clusters × ~200 tokens ≈ 2,000 tokens of cold context. This fits comfortably at the high end of aider's `max_chat_history_tokens` (1K-8K) but is tight at the low end (1K). The mandatory fallback to recursive (Decision 2) catches the case where 10 clusters exceed the budget. The cap also bounds the O(n²) force-merge loop to 45 comparisons — negligible.

### 18. `merge_threshold = 0.15` cosine

TF-IDF cosine similarity floor for merging. Tuned in gemini-cli from 0.3 → 0.15 (reduced singletons from 66 to 10).

**Transfers because:** Same embedder (TF-IDF), same similarity metric. The threshold is a property of the embedding space, not the host application.

### 19. Render all clusters (no query retrieval)

Unlike gemini-cli's `render(query, k=3, min_sim=0.05)`, aider renders all clusters. No query-based retrieval.

**Why different:** Aider's summarizer doesn't have access to the user's current query at `summarize()` time. The `summarize_worker` receives a snapshot of `done_messages`, not `cur_messages`. Rendering all 10 clusters (~2K tokens) is cheap enough that retrieval isn't needed.

**Regression note:** This is more conservative than gemini-cli. Rendering all clusters means nothing is accidentally omitted by a bad similarity match.

### 20. TF-IDF (not dense embeddings)

Pure Python, incremental vocabulary, no API calls.

**Inherited rationale:** Deterministic, no cost, no latency. The experiment showed TF-IDF produces quality-equivalent clustering to what a dense embedder would need to beat.

**Transfers because:** The experiment was run on aider's summarization pipeline. If TF-IDF were insufficient, we'd have seen it in the recall results.

### 21. Single-pass cluster summarization (no verification)

One LLM call per dirty cluster. No verification pass.

**Inherited rationale:** Clusters are small (~5-20 messages each). Single-pass is reliable for small inputs. Verification would double cost.

**Transfers because:** Same cluster sizes, same summarization model (weak_model first).

---

## Decisions Made Fresh for Aider (PR 1 + PR 2)

### 22. Incremental feeding with `_fed_count` (within-cycle only)

Forest persists across `summarize()` calls within a growing cycle. Each call feeds `messages[_fed_count:]`. When `_fed_count > len(messages)`, the forest rebuilds from scratch.

**Why:** Aider's `summarize_worker` receives the full `done_messages` list each time. Without incremental feeding, every call would re-embed and re-insert all messages. `_fed_count` tracks what's already in the forest.

**Critical nuance:** The forest is rebuilt after every successful summarization. When `summarize_end()` swaps in the result, `done_messages` shrinks → `_fed_count > len(messages)` → rebuild. Incremental feeding only works within a growing cycle (messages accumulate but the summary hasn't been applied yet). The forest is a session-scoped cache, not a persistent store.

**Lifecycle rule:** Any operation that resets or replaces `done_messages` automatically triggers a rebuild via the `_fed_count` mechanism. This includes `/clear`, `/drop-topic`, history restore, and normal summarization. No explicit invalidation needed — the mechanism is self-healing.

### 23. First-person user voice in cluster summaries

Cluster summarization prompt uses "I asked you..." voice, matching `prompts.summarize`. Gemini-cli used third-person factual voice.

**Why different:** Aider's LLM prompt expects history written as user addressing assistant. The summary becomes part of `done_messages`, which the LLM reads as conversation history. Wrong voice would confuse the model.

### 24. `/topics` shows guidance for recursive users

When the summarizer is `ChatSummary` (recursive), `/topics` prints "Topic view requires --chat-history-summarizer union-find." It does not show a degraded view.

**Why not degrade gracefully:** A degraded view (just total token count) would duplicate `/tokens` and confuse the mental model. The commands exist because of structured history; showing them without structure is misleading.

### 25. Topics are session-scoped

The forest lives in memory for the current session. On restart, `done_messages` is loaded from `.aider.chat.history.md` — an opaque summary blob. The forest rebuilds from it, but the rebuilt topics are derived from summary text, not original messages. They won't match the topics from the previous session.

**Stated plainly:** `/topics` after restart shows whatever clusters form from the loaded history. These are new topics, not the old ones. There is no fake reconstruction of previous topic boundaries.

**Future:** Cross-session persistence (#4079) requires serializing the forest itself, not rebuilding from `done_messages`. That's a separate PR after the maintainer is comfortable with the data structure.

---

## Defaults Table

| Parameter | Value | Source | Regression risk |
|-----------|-------|--------|----------------|
| Default summarizer | `recursive` | Unchanged | Minimal — flag check only |
| Summary voice | First-person user | Matches `prompts.summarize` | Minimal — same convention |
| Model cascade | `[weak_model, main_model]` | Same as `ChatSummary` | Minimal — same code path |
| `summarize_all()` | Delegates to parent | Same behavior | Minimal — delegates to parent |
| `graduate_at` | 26 | gemini-cli v2 | Opt-in only |
| `evict_at` | 30 | gemini-cli v2 | Opt-in only |
| `max_cold_clusters` | 10 | gemini-cli v2 | Opt-in only |
| `merge_threshold` | 0.15 | gemini-cli v2 | Opt-in only |
| Embeddings | Pure Python TF-IDF | No deps | Opt-in only |
| Cluster prompt | Adapted from `prompts.summarize` | Fragments, not full conversation | Opt-in only |
| Verification | None | Cluster inputs are small | Opt-in only |
| Retrieval | Render all (no query) | More conservative than gemini-cli | Opt-in only |
| CLI flag | `--chat-history-summarizer` | Opt-in | Minimal — one arg addition |
| Persistence | None (rebuild per session) | Deferred | Opt-in only |
