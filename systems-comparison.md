# Systems Comparison: Hierarchical Recursive vs Union-Find

This document compares aider's current hierarchical recursive summarization with union-find structured compaction.

## Common Problem

Both systems address unbounded chat history growth. As conversations continue, `done_messages` accumulates tokens beyond what the LLM can accept. Both compress old history to stay within `max_chat_history_tokens`.

Both are lossy: information is discarded. The question is which information and how much.

## Architectural Comparison

### Current System: Hierarchical Recursive Summarization

**Structure:** Nested half-budget splits. Each level keeps the recent half verbatim and summarizes the older half. Recursion up to depth 4.

**Process:**
1. Check if `done_messages` exceeds token budget
2. Split at half-budget boundary (reverse iteration from end)
3. Adjust split to assistant message boundary
4. Truncate head to model's `max_input_tokens - 512`
5. Summarize head via LLM → single user message
6. If summary + tail still too big, recurse (depth + 1)
7. Base case: ≤4 messages or depth > 3 → summarize everything

**Output format:** `[{"role": "user", "content": "I spoke to you previously...\n{summary}"},  {"role": "assistant", "content": "Ok."}]`

**Cost:** 1 LLM call per recursion level (no verification pass). Up to 4 calls for very large histories.

**Timing:** Runs in background thread. Blocks only at `summarize_end()` if thread hasn't finished.

---

### Union-Find: Structured Compaction

**Structure:** Forest of message clusters, each with its own summary. Two-zone architecture with overlap window.

**Process:**
1. New message appended to hot zone (synchronous, <1ms)
2. When ungraduated count exceeds `graduateAt`, oldest graduates to cold forest
3. Graduated message becomes singleton cluster or merges with nearest (cosine similarity)
4. If cluster count exceeds cap, closest pair merges
5. All merges are structural only — mark cluster dirty, no LLM calls
6. `resolveDirty()` batch-summarizes dirty clusters in background
7. Overlap window: graduated messages stay in hot zone until `evictAt`, giving background time to resolve

**Output format:** Cold cluster summaries + hot zone messages verbatim.

**Cost:** 1 LLM call per dirty cluster at resolve time. ~10 calls per 120 messages (gemini-cli v2 result: 35 total across 12 conversations).

**Timing:** `append()` and `render()` are synchronous (<1ms). `resolveDirty()` runs in background.

## Structural Differences

| Aspect | Hierarchical Recursive | Union-Find |
|--------|----------------------|------------|
| **Compression unit** | Half-budget chunk | Per-cluster (topic-grouped) |
| **Split strategy** | Token-count boundary | Semantic similarity |
| **Recursion** | Up to depth 4 | No recursion — flat forest of clusters |
| **When LLM runs** | During summarize_worker thread | During resolveDirty (background) |
| **Blocking** | Blocks at summarize_end() join | Never blocks (overlap window) |
| **Summary count** | 1 (everything collapses to one) | N (one per cluster, typically ~10) |
| **Original messages** | Discarded | Retained in cluster children |
| **Provenance** | None | Parent pointers → source messages |
| **Expandability** | No | `expand(clusterId)` → original messages |

## How Each Handles Recency

**Recursive:** Each split level keeps the recent half verbatim. At depth 0, the newest ~50% stays. But if recursion goes to depth 2, the "recent" half of level 1's head has already been summarized once. Recency preservation degrades with depth.

**Union-find:** Hot zone (30 messages) always stays verbatim. No degradation with conversation length. The overlap window (messages 26-30) provides a buffer for background resolution.

## How Each Handles Topics

**Recursive:** Splits are token-based, not topic-based. A conversation about authentication that transitions to database schema at the split boundary gets divided arbitrarily. Half the auth context goes to the summary, half stays verbatim.

**Union-find:** Clusters form by semantic similarity. Authentication messages cluster together regardless of when they occurred. Database messages form their own cluster. Summaries are topic-coherent.

## Cost Model

**Recursive (200-message conversation):**
- If summary + tail fits after one split: 1 LLM call
- If recursion needed: 2-4 LLM calls (sequential, each depends on previous result)
- Each call processes a large chunk (~100+ messages at level 0)
- Total: 1-4 calls per compression event, compression triggers ~every 100 messages
- Over 200 messages: ~2-8 LLM calls

**Union-find (200-message conversation, from gemini-cli v2 experiment):**
- ~35 calls across 12 conversations (avg ~3 calls per 120-message conversation)
- Each call processes one cluster's dirty inputs (1 summary + few raw messages)
- Input per call is small (~1000 tokens vs ~50,000 for recursive)
- Total token consumption: 0.79x of flat compression (gemini-cli v2 result)

**Key insight:** Union-find makes more calls but each is much smaller. The total tokens consumed are comparable or lower.

## Failure Modes

### Recursive Failures
- **Cascading loss:** Summary of summary through 4 levels compounds imprecision
- **Semantic-blind splits:** Topics divided at token boundaries lose context
- **Deep recursion blocks:** 4 sequential LLM calls can take 10-30s
- **Stale discard:** Result thrown away if done_messages changed during summarization

### Union-Find Failures
- **Retrieval miss:** Query doesn't match the right cluster centroid
- **Cluster fragmentation:** Threshold too strict → too many small clusters
- **Filler pollution:** Threshold too loose → unrelated messages merged
- **Dirty cluster at render:** If resolveDirty hasn't run yet, cluster shows raw content (not a real failure — overlap window covers this)

## What Each System Optimizes For

### Recursive Optimizes For:
- **Simplicity** — Single algorithm, no data structures beyond message lists
- **Correctness** — Depth limit prevents infinite recursion, stale check prevents data loss
- **Budget compliance** — Recursion guarantees result fits within max_tokens
- **Existing integration** — Background thread already plumbed

### Union-Find Optimizes For:
- **Detail preservation** — Per-cluster summaries retain topic-specific facts
- **Non-blocking operation** — No LLM calls in the critical path
- **Semantic coherence** — Similarity-based clustering keeps related messages together
- **Expandability** — Original messages retrievable

## Key Trade-Offs

| Dimension | Recursive | Union-Find |
|-----------|-----------|------------|
| **Complexity** | Low | High (forest, embeddings, overlap) |
| **Blocking** | Sometimes (deep recursion) | Never |
| **Detail recall** | Degrades with depth | Preserved per-cluster |
| **Budget guarantee** | Strict (recursion enforces) | Soft (cluster count cap) |
| **Cost** | Low call count, large inputs | Higher call count, small inputs |
| **Provenance** | None | Full |
| **Implementation effort** | Done | Requires new module |

## When Each System Wins

**Recursive wins when:**
- Conversations are short (<50 messages, never triggers depth >0)
- Summarization rarely triggers (stays under budget)
- Implementation simplicity is valued over recall quality
- Strict budget compliance is required

**Union-find wins when:**
- Conversations are long (100+ messages, multiple compression events)
- Users reference specific details from earlier in conversation
- Non-blocking UX matters (iterative debugging sessions)
- Multiple topics interleave in the same conversation

## Open Questions for Aider Integration

1. **How does union-find interact with `summarize_all()`?**
   - Called during edit format transitions in `Coder.create()`
   - Separate code path from normal compression
   - Needs its own implementation

2. **How does stale-safety work with stateful forest?**
   - If `summarize_end()` discards result, forest state must remain consistent
   - Options: rebuild forest each call, or make forest state independent of result application

3. **What does the output look like as aider messages?**
   - Current: `[summary_msg, ok_msg]`
   - Union-find: `[cluster_summaries_msg, ok_msg, *hot_messages]`?
   - Must match expected message structure in `chunks.done`

4. **How to handle the `summarize` prompt convention?**
   - Current: "I asked you..." first-person user voice
   - Cluster summaries: third-person factual? Or adapt to user voice?

5. **Token budget compliance?**
   - Recursive guarantees fit via recursion
   - Union-find enforces via cluster count cap + hot zone size
   - Need to verify total tokens stay within `max_chat_history_tokens`
