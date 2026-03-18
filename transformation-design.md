# Transformation Design: Union-Find Context Compaction for Aider

v2 architecture from day one. Lessons from gemini-cli applied:
- `append()` synchronous — no LLM calls
- `render()` synchronous — cached summaries + hot zone
- `resolveDirty()` async — batch-summarize dirty clusters in background
- Overlap window — graduated messages stay in hot zone until background resolves

## Target Architecture

```
append(msg)       <1ms   Synchronous. TF-IDF embed, push to hot, graduate if overflow.
render()          <1ms   Synchronous. Cached cluster summaries + hot zone verbatim.
resolveDirty()    ~4s    Async. Batch-summarize dirty clusters.
```

### Integration with aider's threading model

```
move_back_cur_messages()
  → done_messages += cur_messages
  → summarize_start()
      → summarizer.too_big(done_messages)?
      → Thread(target=summarize_worker)
          → summarizer.summarize(done_messages)
              → ChatSummaryUF.summarize():
                  1. Feed all messages into context_window.append()
                  2. Render: context_window.render()
                  3. Resolve: context_window.resolveDirty()  ← only async part
                  4. Format as aider messages and return
  → User types next message (concurrent)
  → summarize_end()
      → thread.join()
      → if not stale: done_messages = result
```

The existing `summarize_start/worker/end` lifecycle works unchanged. `ChatSummaryUF.summarize()` runs inside the worker thread. `resolveDirty()` is the only blocking LLM work, and it runs in the background thread — not the main thread.

## New Files

### `src/context_window.py`

Core data structures. Direct port from gemini-cli's `contextWindow.ts`, adapted for Python.

#### `cosine_similarity(a, b) → float`

```python
def cosine_similarity(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
```

#### `find_closest_pair(forest) → tuple or None`

O(n^2) on cluster count. With max 10 clusters, this is 45 comparisons — negligible.

#### `class Forest`

State:
- `_nodes`: dict mapping msg_id → Message
- `_summaries`: dict mapping root_id → summary string
- `_children`: dict mapping root_id → list of member msg_ids
- `_centroids`: dict mapping root_id → embedding vector
- `_dirty_inputs`: dict mapping root_id → list of strings to summarize

Methods:
- `insert(msg_id, content, embedding, timestamp)` — create singleton cluster
- `find(msg_id) → root_id` — with path compression
- `union(id_a, id_b) → root_id` — **synchronous**, structural merge only. Collects dirty inputs. No LLM calls.
- `resolve_dirty()` — **async**. One `summarizer.summarize()` call per dirty root.
- `compact(root_id) → str` — cached summary or raw content for singletons
- `expand(root_id) → list[str]` — all member raw contents
- `nearest(query_embedding, k, min_sim) → list[root_id]` — top-k by cosine similarity
- `nearest_root(query_embedding) → (root_id, sim) or None`
- `roots() → list[root_id]`
- `is_dirty(root_id) → bool`
- `dirty_roots() → list[root_id]`
- `cluster_count() → int`
- `size() → int`

**union() dirty input collection:**

When merging A and B:
1. If A has dirty inputs, use those. Else if A has a summary, use `[summary]`. Else use `[raw_content]`.
2. Same for B.
3. Merged dirty inputs = inputs_A + inputs_B.
4. This ensures `resolve_dirty()` sees either the previous clean summary or the raw content — never re-reads all historical members (O(n) not O(n^2)).

#### `class ContextWindow`

Parameters:
- `graduate_at`: default 26 — start inserting into forest when ungraduated > this
- `evict_at`: default 30 — remove from hot zone when hot > this
- `max_cold_clusters`: default 10 — force-merge closest pair if exceeded
- `merge_threshold`: default 0.15 — minimum cosine similarity to merge on graduation

State:
- `_hot`: list of Message
- `_graduated_index`: int tracking how many hot messages have been graduated
- `_next_id`: int auto-incrementing message ID
- `_forest`: Forest instance

Methods:
- `append(content, timestamp=None) → msg_id` — **synchronous**. Embed, push to hot, graduate overflow, evict overflow.
- `render(query=None, k=3, min_sim=0.05) → list[str]` — **synchronous**. Cold summaries + hot contents.
- `resolve_dirty()` — **async**. Delegates to `forest.resolve_dirty()`.
- `expand(root_id) → list[str]`
- Properties: `hot_count`, `cold_cluster_count`, `total_messages`

**Graduation logic (synchronous):**
```python
while len(self._hot) - self._graduated_index > self._graduate_at:
    self._graduate(self._hot[self._graduated_index])
    self._graduated_index += 1
```

**Eviction logic (synchronous):**
```python
while len(self._hot) > self._evict_at:
    self._hot.pop(0)
    self._graduated_index -= 1
```

**_graduate(msg) — synchronous:**
1. `forest.insert(msg_id, content, embedding)`
2. Find nearest existing cluster (excluding self)
3. If similarity >= threshold: `forest.union(msg_id, nearest_root)`
4. While `cluster_count > max_cold_clusters`: `union(closest_pair)`

### `src/embedding_service.py`

#### `class TFIDFEmbedder`

Incremental TF-IDF. No external dependencies.

State:
- `_vocab`: dict mapping term → index
- `_df`: dict mapping term → document frequency
- `_doc_count`: int

Methods:
- `embed(text) → list[float]` — tokenize, update vocab/df, compute TF-IDF vector

**Tokenization:** lowercase, split on non-alphanumeric, filter stopwords.

**TF:** term frequency in this document (count / total terms).

**IDF:** `log(doc_count / df[term])`. Grows vocabulary incrementally — new terms get new dimensions. Existing embeddings remain comparable because IDF weights evolve slowly.

### `src/cluster_summarizer.py`

#### `class ClusterSummarizer`

Wraps aider's model API for cluster summarization.

```python
class ClusterSummarizer:
    def __init__(self, models):
        self.models = models if isinstance(models, list) else [models]

    def summarize(self, texts):
        content = "\n\n---\n\n".join(texts)
        messages = [
            {"role": "system", "content": CLUSTER_SUMMARIZE_PROMPT},
            {"role": "user", "content": content},
        ]
        for model in self.models:
            try:
                result = model.simple_send_with_retries(messages)
                if result is not None:
                    return result
            except Exception:
                continue
        # Fallback: join texts if all models fail
        return "\n".join(texts)
```

**Prompt:**
```
Summarize the following conversation fragments into a concise summary.
The first item may be a previous summary — integrate it with the new messages.
Preserve specific technical details: file paths, function names, error messages,
configuration values, version numbers.
Write in first person as the user ("I asked you...", "We discussed...").
Do not include code blocks. Do not conclude the summary.
```

**Note:** Matches aider's convention of user-voiced summaries. The prompt mirrors `prompts.summarize` but is adapted for cluster fragments rather than full conversation history.

**Model cascade:** Same as current system — tries `weak_model` first, falls back to `main_model`. Uses the same `models` list passed to ChatSummaryUF.

### `src/chat_summary_uf.py`

#### `class ChatSummaryUF(ChatSummary)`

Drop-in replacement. Subclasses `ChatSummary` from `aider.history`.

```python
from aider.history import ChatSummary
from aider import prompts

class ChatSummaryUF(ChatSummary):
    def __init__(self, models=None, max_tokens=1024):
        super().__init__(models, max_tokens)
        self.context_window = ContextWindow(
            embedder=TFIDFEmbedder(),
            summarizer=ClusterSummarizer(self.models),
            graduate_at=26,
            evict_at=30,
            max_cold_clusters=10,
            merge_threshold=0.15,
        )
        self._fed_count = 0  # track how many messages have been fed

    def summarize(self, messages, depth=0):
        # Feed new messages into context window
        for msg in messages[self._fed_count:]:
            content = msg.get("content", "")
            if content:
                self.context_window.append(content)
        self._fed_count = len(messages)

        # Render: cached summaries + hot zone
        rendered = self.context_window.render()

        # Resolve dirty clusters (blocking in this thread, but this thread
        # IS the background summarize_worker — not the main thread)
        self.context_window.resolve_dirty()

        # Split rendered into cold (cluster summaries) and hot (recent messages)
        hot_count = self.context_window.hot_count
        if hot_count > 0 and hot_count < len(rendered):
            cold_parts = rendered[:-hot_count]
            summary_text = prompts.summary_prefix + "\n\n".join(cold_parts)
            # Return summary + hot messages in original dict format
            hot_messages = messages[-hot_count:]
            result = [
                {"role": "user", "content": summary_text},
                {"role": "assistant", "content": "Ok."},
                *hot_messages,
            ]
        else:
            # Everything is in hot zone — no compression needed
            return messages

        # Ensure ends with assistant message
        if result and result[-1]["role"] != "assistant":
            result.append({"role": "assistant", "content": "Ok."})

        return result

    def summarize_all(self, messages):
        # Used by Coder.create() during edit format transitions.
        # Delegate to parent's summarize_all — it produces a single
        # summary message, which is what format transitions need.
        # Union-find's per-cluster structure isn't useful here because
        # the entire history needs to collapse for the new edit format.
        return super().summarize_all(messages)
```

**Key design decisions in this class:**

1. **`_fed_count` tracks incremental feeding.** The stale-safety model means `summarize()` may be called multiple times with the same message prefix. We don't re-feed messages already in the forest.

2. **`resolveDirty()` blocks in the worker thread.** This is intentional. The worker thread is already a background thread. Blocking here means the main thread continues normally, and `summarize_end()` will join when needed. The overlap window ensures most clusters are already resolved by the time they matter.

3. **`summarize_all()` delegates to parent.** Edit format transitions need a single summary, not a cluster structure. The parent's implementation (concatenate + LLM call) is correct for this use case.

4. **Hot messages returned in original dict format.** The hot portion maps back to the original `messages` list, preserving any metadata or formatting the dicts might carry.

5. **Stale discard tolerance.** If `summarize_end()` discards the result (done_messages changed), the forest is orphaned but `_fed_count` is wrong. On the next call, `_fed_count` will exceed the new messages length, so we reset and re-feed. This handles the stale case correctly.

Wait — stale discard is actually a problem. If the result is discarded, `_fed_count` is ahead of reality. Fix:

```python
    def summarize(self, messages, depth=0):
        # Reset if messages don't match expected state (stale discard happened)
        if self._fed_count > len(messages):
            self._rebuild(messages)

        # ... rest of method
```

Actually, the cleaner approach: **rebuild the forest from scratch each call.** The forest is fast to build (append is <1ms per message, 200 messages = 200ms total). This sidesteps all stale-state concerns at the cost of ~200ms per summarization call. Since summarization runs in a background thread and only triggers when history is too big, this is acceptable.

**Revised approach — stateless per call:**

```python
    def summarize(self, messages, depth=0):
        if not self.too_big(messages):
            return messages

        # Rebuild fresh each call — avoids stale state from discarded results
        self.context_window = ContextWindow(
            embedder=TFIDFEmbedder(),
            summarizer=ClusterSummarizer(self.models),
            graduate_at=26,
            evict_at=30,
            max_cold_clusters=10,
            merge_threshold=0.15,
        )

        for msg in messages:
            content = msg.get("content", "")
            if content:
                self.context_window.append(content)

        rendered = self.context_window.render()
        self.context_window.resolve_dirty()

        hot_count = self.context_window.hot_count
        if hot_count > 0 and hot_count < len(rendered):
            cold_parts = rendered[:-hot_count]
            summary_text = prompts.summary_prefix + "\n\n".join(cold_parts)
            hot_messages = messages[-hot_count:]
            result = [
                {"role": "user", "content": summary_text},
                {"role": "assistant", "content": "Ok."},
                *hot_messages,
            ]
        else:
            return messages

        if result and result[-1]["role"] != "assistant":
            result.append({"role": "assistant", "content": "Ok."})

        return result
```

**Trade-off:** Rebuilding the forest loses cross-call learning (clusters from previous compressions). But this matches the current system's behavior — each `summarize()` call is independent. Cross-call state can be added later if the forest proves valuable to persist.

## Construction Site

In `main.py`, the summarizer is constructed and passed to the coder:

```python
# Current
summarizer = ChatSummary(
    [main_model.weak_model, main_model],
    args.max_chat_history_tokens or main_model.max_chat_history_tokens,
)

# With union-find option
if getattr(args, 'chat_history_summarizer', None) == 'union-find':
    summarizer = ChatSummaryUF(
        [main_model.weak_model, main_model],
        args.max_chat_history_tokens or main_model.max_chat_history_tokens,
    )
else:
    summarizer = ChatSummary(
        [main_model.weak_model, main_model],
        args.max_chat_history_tokens or main_model.max_chat_history_tokens,
    )
```

New CLI arg in `args.py`:
```python
parser.add_argument(
    "--chat-history-summarizer",
    default="recursive",
    choices=["recursive", "union-find"],
    help="Algorithm for chat history summarization (default: recursive)",
)
```

## What Stays

- **`summarize_start/worker/end` lifecycle** — unchanged, union-find runs inside worker thread
- **`too_big()` check** — inherited from ChatSummary, uses same token counting
- **Model cascade** — same `[weak_model, main_model]` list
- **Summary prefix** — `prompts.summary_prefix` reused for cold zone output
- **`summarize_all()`** — delegates to parent, used for edit format transitions
- **Stale-safety** — `summarize_end()` identity check works unchanged
- **Background threading** — existing `threading.Thread` infrastructure

## What Changes

- **Compression algorithm** — recursive split → union-find clustering
- **Summary granularity** — single blob → per-cluster summaries
- **Split strategy** — token-count boundary → semantic similarity
- **Output structure** — `[summary, ok]` → `[cluster_summaries, ok, *hot_messages]`
- **LLM call pattern** — 1-4 large calls → many small calls
- **New dependency** — TF-IDF embedding (no external packages, pure Python)

## Token Budget Compliance

The current system guarantees compliance via recursion: if result still too big, recurse until it fits.

Union-find enforces differently:
- Cold zone: bounded by `max_cold_clusters × max_summary_size`. With 10 clusters and ~200 token summaries, cold ≈ 2,000 tokens.
- Hot zone: bounded by `evict_at` messages. With 30 messages averaging ~200 tokens, hot ≈ 6,000 tokens.
- Total estimate: ~8,000 tokens for compressed history. Well within typical `max_chat_history_tokens` (usually 10,000-40,000).

If compliance becomes an issue, fallback: reduce `max_cold_clusters` or `evict_at`, or delegate to parent's `summarize()` as a safety net.

## Testing Strategy

### Unit Tests (`test_context_window.py`)

Forest:
- `insert()` creates singleton with correct embedding
- `find()` returns self for singleton, root for merged
- `union()` is synchronous — does not call summarizer
- `union()` collects dirty inputs correctly
- `resolve_dirty()` calls summarizer once per dirty root
- `compact()` returns raw content for singletons, summary for resolved
- `expand()` returns all member contents
- `nearest()` returns top-k by cosine similarity

ContextWindow:
- `append()` is synchronous — never calls summarizer
- `append()` graduates when ungraduated > graduate_at
- `append()` evicts when hot > evict_at
- `render()` returns cold summaries + hot contents
- `render()` is synchronous
- `resolve_dirty()` calls summarizer

### Unit Tests (`test_embedding_service.py`)

- `embed()` returns float vector
- Same text produces same embedding
- Different texts produce different embeddings
- Vocabulary grows incrementally
- Cosine similarity: identical texts → 1.0, orthogonal → 0.0

### Unit Tests (`test_cluster_summarizer.py`)

- Calls `model.simple_send_with_retries()` with formatted input
- Falls back to second model on failure
- Returns joined text if all models fail

### Integration Tests (`test_chat_summary_uf.py`)

- `ChatSummaryUF` is subclass of `ChatSummary`
- `too_big()` works identically to parent
- `summarize()` returns valid message list
- `summarize()` result ends with assistant message
- `summarize()` output has fewer tokens than input
- `summarize_all()` delegates to parent
- Fresh forest per call (stale-safety)
- Output is valid `done_messages` format
