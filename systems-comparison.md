# Systems Comparison: Recursive vs Union-Find

Both systems compress `done_messages` to stay within `max_chat_history_tokens`. Both are lossy. The question is which information gets dropped.

## Structural Differences

| Aspect | Recursive | Union-Find |
|--------|-----------|------------|
| **Compression unit** | Half-budget chunk (token boundary) | Per-cluster (topic-grouped) |
| **When LLM runs** | 1-4 large calls in worker thread | Many small calls in worker thread |
| **Blocking** | Blocks at `summarize_end()` join | Same — but fewer/smaller calls finish faster |
| **Summary count** | 1 (everything collapses to one blob) | ~10 (one per cluster) |
| **Recency** | Degrades with recursion depth | Fixed: hot zone (30 msgs) always verbatim |
| **Topic coherence** | Splits at token boundary, not topic | Clusters by semantic similarity |
| **Original messages** | Discarded | Retained in cluster children |
| **Budget guarantee** | Strict (recursion enforces) | Structural bounds + fallback to recursive |

## Trade-Offs

| Dimension | Recursive wins | Union-Find wins |
|-----------|---------------|-----------------|
| **Simplicity** | 143 lines, no data structures | Forest, embeddings, overlap window |
| **Detail recall** | | Per-cluster summaries preserve topic-specific facts |
| **Cost** | Fewer calls (but larger inputs) | More calls (but smaller inputs), 0.79x total tokens in gemini-cli |
| **Blocking time** | | Smaller LLM calls = worker finishes faster |
| **Short conversations** | Never triggers, no overhead | |
| **Long multi-topic** | | Topics cluster regardless of when they occurred |

## Integration

Union-find runs inside the existing `summarize_start/worker/end` lifecycle. No changes to threading, stale-safety, or `summarize_all()`. The only new integration point is a CLI flag to select the strategy.
