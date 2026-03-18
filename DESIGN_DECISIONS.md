# Design Decisions

15 aider-specific decisions, ordered least to most uncertain. Each documents the decision, rationale, and what would trigger a change.

---

### 1. Summary voice: first-person user

**Decision:** Cluster summaries use first-person user voice ("I asked you...", "We discussed...") matching aider's existing `prompts.summarize` convention.

**Rationale:** The LLM expects `done_messages` to read as user-assistant conversation. Introducing third-person factual style for cluster summaries would create a voice mismatch. The current prompt already instructs "Write *as* the user."

**Change trigger:** Never — this is an aider convention, not ours to change.

---

### 2. Model cascade: reuse existing `[weak_model, main_model]`

**Decision:** `ClusterSummarizer` receives the same model list as `ChatSummary`. Tries `weak_model` first, falls back to `main_model`.

**Rationale:** No reason to introduce new model routing. Cluster summarization is the same task (summarize conversation fragments) at a smaller scale. If `weak_model` is good enough for 100-message summaries, it's good enough for 5-message cluster summaries.

**Change trigger:** If cluster summaries are consistently low quality with `weak_model` but fine with `main_model`, consider always using `main_model` for clusters (small input = low cost anyway).

---

### 3. `summarize_all()` delegates to parent

**Decision:** `ChatSummaryUF.summarize_all()` calls `super().summarize_all()` unchanged.

**Rationale:** `summarize_all()` is called during edit format transitions in `Coder.create()`. It needs to collapse ALL history into one summary for the new format. The per-cluster structure of union-find is irrelevant here — the entire history must become one blob. The parent's implementation (concatenate + LLM call) is correct.

**Change trigger:** Never for the spike. If forest persistence is added later, `summarize_all()` could use the forest's cluster summaries as input instead of raw messages.

---

### 4. Rebuild forest each `summarize()` call (stateless)

**Decision:** Each call to `summarize()` constructs a fresh `ContextWindow` and feeds all messages from scratch.

**Rationale:** The stale-safety model means `summarize_end()` may discard results. If the forest persists across calls but the result is discarded, the forest's state diverges from `done_messages`. Rebuilding avoids this entirely. Cost: ~200ms for 200 messages (append is <1ms each). This runs in the background thread, so 200ms is invisible.

**Trade-off:** No cross-call learning. Clusters from previous compressions are lost. Each call re-discovers topic structure from scratch.

**Change trigger:** If cross-call persistence proves valuable AND a safe reconciliation strategy exists for the stale-discard case.

---

### 5. Overlap window: `graduate_at=26`, `evict_at=30`

**Decision:** Messages graduate to the forest at position 26, evict from hot at position 30. Overlap window of ~4 messages.

**Rationale:** Same parameters validated in gemini-cli v2 experiment (0.3ms append+render). The overlap gives `resolveDirty()` time to run before messages leave the hot zone. With aider's background threading, `resolveDirty()` runs inside `summarize_worker()` which executes during user think time.

**Note:** With stateless-per-call design (decision #4), the overlap window matters less — all messages are fed fresh, and `resolveDirty()` runs immediately after. The overlap is still structurally correct and would matter if persistence is added later.

**Change trigger:** If hot zone is too large (wastes tokens) or too small (clusters not resolved in time). Tune by ±5.

---

### 6. Max cold clusters: 10

**Decision:** Hard cap at 10 clusters in the forest. When exceeded, closest pair merges.

**Rationale:** Validated in gemini-cli v2. 10 clusters × ~200 token summaries = ~2,000 tokens for the cold zone. Well within typical `max_chat_history_tokens`.

**Change trigger:** If conversations have more than 10 distinct topics that all remain relevant simultaneously. Increase to 15-20 if token budget allows.

---

### 7. Merge threshold: 0.15 cosine similarity

**Decision:** Messages merge with nearest cluster if cosine similarity ≥ 0.15. Below this, they stay as singletons.

**Rationale:** TF-IDF similarity of 0.15 means meaningful term overlap. Lower threshold = more merging (fewer clusters, more filler). Higher threshold = more singletons (more clusters, hit cap faster, forced merges).

**Change trigger:** If too many forced merges (threshold too high, cap hit constantly) or too much filler pollution (threshold too low, unrelated messages merged). Adjust by ±0.05.

---

### 8. TF-IDF embeddings (no external dependencies)

**Decision:** Pure Python TF-IDF. No numpy, no sentence-transformers, no API calls.

**Rationale:** Aider has minimal dependencies. Adding numpy or a dense embedding model would increase install complexity. TF-IDF is:
- Fast (<1ms per embed)
- Deterministic (same input → same output)
- Pure Python (no native extensions)
- Sufficient for topic clustering (gemini-cli validated)

**Trade-off:** TF-IDF captures lexical similarity, not semantic similarity. "authentication" and "login" are different terms. Dense embeddings would catch this.

**Change trigger:** If recall experiments show TF-IDF misses obvious clusters (semantically related messages with different vocabulary). Would require adding a dependency.

---

### 9. Cluster summarization prompt

**Decision:** Custom prompt for cluster summaries, adapted from `prompts.summarize` but shorter and cluster-focused.

**Rationale:** The existing `summarize` prompt is designed for full conversation summarization ("Briefly summarize this partial conversation"). Cluster summaries need: "Summarize these related conversation fragments, preserving technical details."

**Key differences from existing prompt:**
- Acknowledges fragments (not full conversation)
- First item may be a previous summary (integrate, don't re-summarize)
- Same user-voice convention
- Same technical detail preservation

**Change trigger:** If cluster summaries are too verbose or miss key details. Tune prompt wording.

---

### 10. Output format: summary + ok + hot messages

**Decision:** `summarize()` returns `[summary_msg, ok_msg, *hot_messages]` where `summary_msg` contains all cluster summaries joined with double newlines, prefixed with `prompts.summary_prefix`.

**Rationale:** Matches the current output structure: compressed history as user message, acknowledgment, then recent messages. The LLM sees the same pattern it's used to. Hot messages preserve original dict format (role, content, any other keys).

**Alternative considered:** Separate user message per cluster. Rejected because `done_messages` must be valid alternating user/assistant sequence.

**Change trigger:** If the joined summary is too long or the LLM has trouble parsing multiple cluster summaries in one message. Could add XML tags or section headers.

---

### 11. No verification pass for cluster summaries

**Decision:** Single LLM call per cluster, no generate+verify.

**Rationale:** Cluster inputs are small (1 previous summary + a few new messages). The verification pass adds value for large inputs (100+ messages) where omissions are likely. For 5-message clusters, single-pass is sufficient. Saves 50% of LLM calls.

**Change trigger:** If cluster summaries consistently miss important details. Add a verification prompt for clusters larger than some threshold (e.g., >20 members).

---

### 12. No query-based retrieval in `render()`

**Decision:** `render()` returns ALL cluster summaries, not just the top-k most relevant.

**Rationale:** With only 10 clusters, returning all is simpler and avoids retrieval misses. The total cold zone is ~2,000 tokens — small enough to include everything. Query-based retrieval adds complexity and risks dropping relevant clusters.

**Change trigger:** If cold zone grows too large (more clusters or longer summaries). Switching to query-based retrieval requires knowing the user's current query at render time.

---

### 13. Token budget enforcement via structure, not recursion

**Decision:** Token budget enforced by structural constraints (cluster count cap × max summary size + hot zone cap), not by recursive splitting.

**Rationale:** The current system recursion-guarantees fit. Union-find can't recurse the same way. Instead, structural bounds ensure compliance:
- 10 clusters × 200 tokens = 2,000 tokens (cold)
- 30 messages × 200 tokens = 6,000 tokens (hot)
- Total ≈ 8,000 tokens

If `max_chat_history_tokens` is 10,000+, this fits. If it's very small, reduce `evict_at` and `max_cold_clusters`.

**Safety net:** If output exceeds budget, fall back to `super().summarize()`.

**Change trigger:** If budget violations occur in practice. Add a post-render token count check.

---

### 14. CLI flag: `--chat-history-summarizer`

**Decision:** New CLI argument with choices `["recursive", "union-find"]`, default `"recursive"`.

**Rationale:** Opt-in activation matches aider's pattern. Users who want to try union-find explicitly select it. No behavior change for existing users.

**Implementation:** Add to `args.py`, read in `main.py`, construct appropriate summarizer.

**Change trigger:** If accepted upstream, could become the default after sufficient validation.

---

### 15. No persistence of forest across sessions

**Decision:** Forest is ephemeral — rebuilt from `done_messages` each time summarization triggers.

**Rationale:** Aider saves chat history as markdown (`.aider.chat.history.md`). The forest would need serialization/deserialization to persist. This adds complexity for unclear benefit in the spike. Cross-session clustering can be added later.

**Trade-off:** Every session starts with fresh clustering. Long-running topics don't accumulate cluster structure across sessions.

**Change trigger:** If users restart aider frequently and lose valuable cluster structure. Would require adding forest serialization.

---

## Known Limitations

1. **No cross-call state.** Each `summarize()` call rebuilds the forest. Previous clustering is lost.
2. **TF-IDF only captures lexical similarity.** Semantically related messages with different vocabulary may not cluster.
3. **No message edit support.** If a message is edited after clustering, the forest doesn't update.
4. **No concurrent access.** Forest is single-threaded (runs in one worker thread, so this is fine).
5. **No retrieval.** All cold clusters rendered, no query-based selection.

## Iteration Triggers

If any of these are observed during testing:

| Observation | Action |
|------------|--------|
| Recall worse than recursive | Check cluster quality, tune merge threshold, consider dense embeddings |
| Budget violations | Reduce evict_at or max_cold_clusters, add post-render check |
| Cluster summaries miss details | Tune prompt, consider verification for large clusters |
| Too many forced merges | Increase max_cold_clusters or lower merge threshold |
| TF-IDF misses obvious clusters | Evaluate adding dense embedding dependency |
| Stale discard causes issues | Investigate incremental forest reconciliation |
| summarize_all() needs clusters | Implement cluster-aware format transition |
