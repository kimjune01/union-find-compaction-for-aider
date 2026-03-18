# Design Decisions

Three non-obvious decisions, plus a table of defaults.

---

### 1. Incremental forest with stale detection

The forest persists across `summarize()` calls. Each call feeds only new messages (`messages[_fed_count:]`). If `_fed_count > len(messages)`, the previous result was applied (messages shrank), so the forest rebuilds from scratch.

Two cases in aider's stale-safety model:
- **Result discarded** (messages grew): Forest is still valid. Feed delta. Cross-call clustering preserved.
- **Result applied** (messages shrank): Forest was built from pre-summary messages. Rebuild.

**Known gap:** Doesn't detect equal-length replacement. Narrow in practice: aider only modifies `done_messages` by appending or replacing with summary. The outer `summarize_end()` stale check provides a safety net. Content hash comparison is the fix if needed.

---

### 2. Output format: summary + ok + hot messages

`summarize()` returns `[summary_msg, ok_msg, *hot_messages]` where `summary_msg` contains all cluster summaries joined with double newlines, prefixed with `prompts.summary_prefix`.

Matches the current output structure. Alternative (separate user message per cluster) was rejected because `done_messages` must be valid alternating user/assistant sequence.

---

### 3. Token budget: structural bounds + mandatory fallback

Structural bounds keep the common case within budget (10 clusters x ~200 tokens + 30 hot messages x ~200 tokens ≈ 8,000 tokens). Two mandatory checks after rendering: (1) result exceeds `max_tokens` → fall back to recursive; (2) result >= input tokens → fall back to recursive.

Union-find never produces a worse result than the current system.

---

## Defaults

Everything else follows from "match what exists" or "start with gemini-cli values."

| Parameter | Value | Source |
|-----------|-------|--------|
| Summary voice | First-person user | Matches `prompts.summarize` |
| Model cascade | `[weak_model, main_model]` | Same as ChatSummary |
| `summarize_all()` | Delegates to parent | Edit format transitions need single blob |
| `graduate_at` | 26 | gemini-cli v2, tune during experiment |
| `evict_at` | 30 | gemini-cli v2, tune during experiment |
| `max_cold_clusters` | 10 | gemini-cli v2 |
| `merge_threshold` | 0.15 cosine | gemini-cli v2 |
| Embeddings | Pure Python TF-IDF | No external deps |
| Cluster prompt | Adapted from `prompts.summarize` | Fragments, not full conversation |
| Verification pass | None | Cluster inputs are small |
| Query retrieval | None (render all) | 10 clusters, ~2k tokens total |
| CLI flag | `--chat-history-summarizer` | Opt-in, default `recursive` |
| Session persistence | None | Spike scope |

## Known Limitations

1. TF-IDF captures lexical similarity only, not semantic.
2. No cross-session forest persistence.
3. No `/expand` command yet — provenance exists on the object but isn't exposed.
