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

### 4. Incremental forest with stale detection

**Decision:** The forest persists across `summarize()` calls. Each call feeds only new messages (`messages[_fed_count:]`). If `_fed_count > len(messages)`, the previous result was applied (messages shrank), so the forest rebuilds from scratch.

**Rationale:** Two cases in aider's stale-safety model:
- **Result discarded** (messages grew): Forest is still valid. Feed the additional messages incrementally. Cross-call clustering preserved.
- **Result applied** (messages shrank): Forest was built from pre-summary messages that no longer exist. Rebuild cleanly from the new message list.

The detection is simple: if `_fed_count > len(messages)`, messages shrank. Otherwise, feed the delta.

**Benefits:** Cross-call clustering persists when results are discarded. Topics accumulate structure over multiple attempts. The overlap window is meaningful — background `resolveDirty()` has time to complete between calls.

**Change trigger:** If edge cases arise where the delta detection is insufficient (e.g., messages modified in place rather than appended/replaced).

---

### 5. Overlap window: `graduate_at=26`, `evict_at=30`

**Decision:** Messages graduate to the forest at position 26, evict from hot at position 30. Overlap window of ~4 messages.

**Rationale:** Starting points from gemini-cli v2 (0.3ms append+render). Not validated on aider's conversation distribution — tuning expected during experiment phase. The overlap gives `resolveDirty()` time to run before messages leave the hot zone. With incremental persistence (decision #4), the overlap window is meaningful: background resolution between calls covers graduated messages before they evict.

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

### 13. Token budget enforcement: structural bounds + mandatory fallback

**Decision:** Primary enforcement via structural constraints (cluster count cap × summary size + hot zone cap). Hard safety net: mandatory post-render token count check. If output tokens >= input tokens, fall back to `super().summarize()`.

**Rationale:** The current system guarantees fit via recursion — a contract any replacement must honor. Structural bounds (10 clusters × ~200 tokens + 30 messages × ~200 tokens ≈ 8,000 tokens) keep the common case well within budget. The mandatory fallback catches edge cases where summaries are unexpectedly verbose or hot messages are unusually large.

**The fallback means union-find never produces a worse result than the current system.** In the worst case, it delegates entirely to recursive summarization — same output as if union-find didn't exist.

**Change trigger:** If the fallback triggers frequently, investigate why summaries are inflating (prompt tuning, cluster size limits).

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

1. **TF-IDF only captures lexical similarity.** Semantically related messages with different vocabulary may not cluster.
2. **No message edit support.** If a message is edited after clustering, the forest doesn't update.
3. **No concurrent access.** Forest is single-threaded (runs in one worker thread, so this is fine).
4. **No retrieval.** All cold clusters rendered, no query-based selection.
5. **No cross-session persistence.** Forest rebuilt on each session start.

## Iteration Triggers

If any of these are observed during testing:

| Observation | Action |
|------------|--------|
| Recall worse than recursive | Check cluster quality, tune merge threshold, consider dense embeddings |
| Budget fallback triggers often | Tune prompt to produce shorter summaries, reduce max_cold_clusters |
| Cluster summaries miss details | Tune prompt, consider verification for large clusters |
| Too many forced merges | Increase max_cold_clusters or lower merge threshold |
| TF-IDF misses obvious clusters | Evaluate adding dense embedding dependency |
| summarize_all() needs clusters | Implement cluster-aware format transition |
