# Systems Comparison: Recursive vs Union-Find

Both systems compress `done_messages` to stay within `max_chat_history_tokens`. Both are lossy. The question is which information gets dropped.

## Structural Differences

| Aspect | Recursive | Union-Find |
|--------|-----------|------------|
| **Compression unit** | Half-budget chunk (token boundary) | Per-cluster (topic-grouped) |
| **When LLM runs** | 1-4 large calls in worker thread | Many small calls in worker thread |
| **Blocking** | Blocks at `summarize_end()` join | Same — but fewer/smaller calls finish faster |
| **Summary count** | 1 blob | ~10 cluster summaries, joined into 1 output message |
| **Recency** | Degrades with recursion depth | Fixed: hot zone (30 msgs) always verbatim |
| **Topic coherence** | Splits at token boundary, not topic | Clusters by lexical similarity (TF-IDF) |
| **Original messages** | Discarded | Retained in cluster children |
| **Budget guarantee** | Strict (recursion enforces) | Structural bounds + fallback to recursive |

## Trade-Offs

| Dimension | Recursive wins | Union-Find wins |
|-----------|---------------|-----------------|
| **Simplicity** | 143 lines, no data structures | Forest, embeddings, overlap window |
| **Detail recall** | | Per-cluster summaries preserve topic-specific facts |
| **Cost** | Fewer calls (but larger inputs) | More calls (but smaller inputs); 0.79x total tokens in gemini-cli (not validated on aider) |
| **Blocking time** | | Smaller LLM calls = worker finishes faster |
| **Short conversations** | Never triggers, no overhead | |
| **Long multi-topic** | | Topics cluster regardless of when they occurred |

## Integration

Union-find runs inside the existing `summarize_start/worker/end` lifecycle. No changes to the external threading contract or `summarize_all()`. Internally, the summarizer adds `_fed_count` state for incremental feeding across calls (see DESIGN_DECISIONS.md #1). The only new external integration point is a CLI flag.
